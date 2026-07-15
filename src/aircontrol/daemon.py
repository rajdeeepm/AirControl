"""Lifecycle and command boundary for the AirControl gesture pipeline."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from aircontrol import settings as app_settings
from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.domain import HandObservation
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.ipc import (
    ack_event,
    app_settings_event,
    camera_event,
    library_event,
    metrics_snapshot_event,
    settings_event,
)
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline, PipelineEvent
from aircontrol.profile import load_active_profile
from aircontrol.store import Store
from aircontrol.trajectory import Trajectory


logger = logging.getLogger(__name__)
_MAX_ANIMATION_FRAMES = 30
_STORE_COMMAND_NAMES = frozenset(
    {
        "list_library",
        "set_mapping",
        "delete_gesture",
        "rename_gesture",
        "get_metrics",
        "get_settings",
        "get_app_settings",
        "set_app_setting",
        "delete_everything",
    }
)


def default_store_path() -> Path:
    """Return the platform's per-user AirControl database location."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        configured_base = Path(local_app_data).expanduser() if local_app_data else None
        base = (
            configured_base
            if configured_base is not None and configured_base.is_absolute()
            else Path.home() / "AppData" / "Local"
        )
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        data_home = os.environ.get("XDG_DATA_HOME")
        configured_base = Path(data_home).expanduser() if data_home else None
        base = (
            configured_base
            if configured_base is not None and configured_base.is_absolute()
            else Path.home() / ".local" / "share"
        )
    return base / "AirControl" / "aircontrol.db"


def _animation_payload(trajectory: Trajectory) -> dict[str, list[Any]]:
    frames = trajectory.frames
    if len(frames) <= _MAX_ANIMATION_FRAMES:
        indices = range(len(frames))
    else:
        last_index = len(frames) - 1
        indices = (
            index * last_index // (_MAX_ANIMATION_FRAMES - 1)
            for index in range(_MAX_ANIMATION_FRAMES)
        )

    selected = [frames[index] for index in indices]
    return {
        "timestamps": [frame.timestamp for frame in selected],
        "frames": [
            [[point.x, point.y, point.z] for point in frame.landmarks]
            for frame in selected
        ],
    }


class Daemon:
    """Own a pipeline and expose its synchronous lifecycle and command surface."""

    def __init__(
        self,
        config: AppConfig,
        *,
        practice: bool,
        controller: ActionController,
        store: Store | None = None,
        ipc: Any = None,
        require_ipc: bool = False,
    ) -> None:
        self.config = config
        self.practice = practice
        self.ipc = ipc
        self.require_ipc = require_ipc
        self.quit_requested = False
        self.preview_enabled = False
        self._started = False
        self._stopped = False
        self._lock = threading.RLock()
        self._camera_enabled = False
        self._camera_state = "off"
        self._camera_error: str | None = None
        self._camera_restart_requested = False
        self._store_owner_thread_id = threading.get_ident()
        self._matcher_refresh_pending = False
        self._owns_store = store is None
        self.store = store if store is not None else self._open_configured_store()
        profile = load_active_profile(self.store) if self.store is not None else None
        loaded_settings = (
            app_settings.load(self.store) if self.store is not None else None
        )
        self.metrics = Metrics()
        self.gate = ConfidenceGate(GateThresholds())
        self.pipeline = Pipeline(
            config,
            controller,
            store=self.store,
            metrics=self.metrics,
            gate=self.gate,
            settings=loaded_settings,
            profile=profile,
        )
        if self.ipc is not None:
            self.ipc.on_command(self._handle_ipc_command)

    def feed(
        self,
        observation: HandObservation | None,
        now: float,
    ) -> list[PipelineEvent]:
        with self._lock:
            self._ensure_running()
            if self.config.ipc.enabled and (
                not self._camera_enabled or self._camera_state != "active"
            ):
                return []
            if self._matcher_refresh_pending:
                self._refresh_matcher()
                self._matcher_refresh_pending = False
            events = self.pipeline.process(observation, now)
            self._broadcast(events)
            return events

    def command(self, name: str | dict[str, Any]) -> list[PipelineEvent]:
        message = {"name": name} if isinstance(name, str) else name
        command_name = message.get("name")
        with self._lock:
            self._ensure_running()
            if command_name in _STORE_COMMAND_NAMES:
                events = self._store_command(message, self.store)
            elif command_name == "toggle_arm":
                if self.config.ipc.enabled and self._camera_state != "active":
                    events = self.pipeline.force_pause(
                        "Camera must be active to arm"
                    )
                else:
                    events = self.pipeline.toggle_arm(time.monotonic())
            elif command_name == "pause":
                events = self.pipeline.force_pause("Paused manually")
            elif command_name == "undo":
                events = self.pipeline.undo()
            elif command_name == "refresh_matcher":
                if self.pipeline.matcher is not None:
                    self.pipeline.matcher.refresh()
                events = [self.pipeline.status()]
            elif command_name == "get_status":
                events = [self.pipeline.status()]
            elif command_name == "set_preview":
                self.preview_enabled = message["enabled"] and (
                    not self.config.ipc.enabled
                    or (
                        self._camera_enabled
                        and self._camera_state == "active"
                    )
                )
                request_id = message.get("id")
                events = [
                    ack_event(
                        request_id if isinstance(request_id, str) else None,
                        True,
                    )
                ]
            elif command_name == "set_camera":
                events = self._set_camera_enabled(
                    message["enabled"],
                    message.get("id"),
                )
            elif command_name == "retry_camera":
                events = self._set_camera_enabled(True, message.get("id"))
            elif command_name == "quit":
                self.quit_requested = True
                events = self.pipeline.force_pause("Stopped")
            else:
                raise ValueError(f"Unsupported daemon command: {command_name}")
            self._broadcast(events)
            return events

    @property
    def camera_enabled(self) -> bool:
        with self._lock:
            return self._camera_enabled

    @property
    def camera_state(self) -> str:
        with self._lock:
            return self._camera_state

    @property
    def camera_error(self) -> str | None:
        with self._lock:
            return self._camera_error

    def take_camera_restart_request(self) -> bool:
        """Consume the app loop's one-shot camera start/restart request."""
        with self._lock:
            self._ensure_running()
            requested = self._camera_restart_requested
            self._camera_restart_requested = False
            return requested

    def set_camera_state(
        self,
        state: str,
        error: str | None = None,
    ) -> list[PipelineEvent]:
        """Report camera runtime state and broadcast safety-relevant changes."""
        event = camera_event(state, error if state == "error" else None)
        with self._lock:
            self._ensure_running()
            camera_error = error if state == "error" else None
            if not self._camera_enabled and state != "off":
                return []
            if self._camera_restart_requested and state != "starting":
                return []
            if (
                state == self._camera_state
                and camera_error == self._camera_error
            ):
                return []

            self._camera_state = state
            self._camera_error = camera_error
            if state in {"off", "active", "error"}:
                self._camera_restart_requested = False

            events: list[PipelineEvent] = []
            if state in {"off", "starting", "error"}:
                self.preview_enabled = False
            if state in {"off", "error"}:
                reason = (
                    f"Paused - {camera_error}"
                    if camera_error
                    else "Paused - camera is off"
                )
                events.extend(self.pipeline.force_pause(reason))
            events.append(event)
            self._broadcast(events)
            return events

    def status(self) -> PipelineEvent:
        with self._lock:
            return self.pipeline.status()

    def force_pause(self, reason: str) -> list[PipelineEvent]:
        with self._lock:
            self._ensure_running()
            events = self.pipeline.force_pause(reason)
            self._broadcast(events)
            return events

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                raise RuntimeError("Daemon has already stopped")
            if self._started:
                return
            self._started = True
            start = getattr(self.ipc, "start", None)
        if start is None:
            return
        try:
            start()
        except Exception as exc:
            if self.require_ipc:
                cause = str(exc) or type(exc).__name__
                raise RuntimeError(
                    "The app UI could not start: the local WebSocket server "
                    f"failed ({cause}). Your virtual environment may be missing "
                    "the websockets package -- run setup.cmd to repair it."
                ) from exc
            logger.exception("IPC server could not start; continuing in embedded mode")

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        try:
            stop = getattr(self.ipc, "stop", None)
            if stop is not None:
                stop()
        finally:
            with self._lock:
                try:
                    self.pipeline.release()
                finally:
                    if self._owns_store and self.store is not None:
                        self.store.close()

    def _handle_ipc_command(self, command: dict[str, Any]) -> None:
        try:
            if (
                command.get("name") in _STORE_COMMAND_NAMES
                and threading.get_ident() != self._store_owner_thread_id
            ):
                self._thread_local_store_command(command)
            else:
                self.command(command)
        except Exception:
            logger.exception("IPC daemon command failed")
            request_id = command.get("id")
            if isinstance(request_id, str):
                self._broadcast([ack_event(request_id, False, "command failed")])

    def _thread_local_store_command(
        self,
        message: dict[str, Any],
    ) -> list[PipelineEvent]:
        """Run a socket store command with a connection owned by the IPC thread."""
        store = self._open_configured_store()
        try:
            with self._lock:
                self._ensure_running()
                events = self._store_command(
                    message,
                    store,
                    defer_matcher_refresh=True,
                )
                self._broadcast(events)
                return events
        finally:
            if store is not None:
                store.close()

    def _store_command(
        self,
        message: dict[str, Any],
        store: Store | None,
        *,
        defer_matcher_refresh: bool = False,
    ) -> list[PipelineEvent]:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            request_id = None

        name = message["name"]
        if name == "get_settings":
            return [settings_event(self._settings_payload(store), request_id)]

        if store is None:
            return [ack_event(request_id, False, "no store")]

        if name == "list_library":
            return [library_event(self._library_payload(store), request_id)]
        if name == "set_mapping":
            store.mappings.set(
                message["gesture_id"],
                message["action"],
                message.get("context", "global"),
                message.get("enabled", True),
            )
            return [ack_event(request_id, True)]
        if name == "delete_gesture":
            store.gestures.delete(message["gesture_id"])
            self._refresh_matcher_after_store_change(defer_matcher_refresh)
            return [ack_event(request_id, True)]
        if name == "rename_gesture":
            store.gestures.rename(message["gesture_id"], message["new_name"])
            self._refresh_matcher_after_store_change(defer_matcher_refresh)
            return [ack_event(request_id, True)]
        if name == "get_metrics":
            snapshot = self.metrics.snapshot()
            return [
                metrics_snapshot_event(
                    candidates_per_hour=snapshot.candidates_per_hour,
                    armed_candidates_per_hour=snapshot.armed_candidates_per_hour,
                    fp_per_hour=snapshot.fp_per_hour,
                    latency_ms_p50=snapshot.latency_ms_p50,
                    latency_ms_p95=snapshot.latency_ms_p95,
                    cpu_pct=snapshot.cpu_pct,
                    uptime_seconds=snapshot.uptime_seconds,
                    id=request_id,
                )
            ]
        if name == "get_app_settings":
            return [app_settings_event(app_settings.load(store), request_id)]
        if name == "set_app_setting":
            key = message["key"]
            try:
                value = app_settings.validate(key, message["value"])
            except ValueError as exc:
                return [ack_event(request_id, False, str(exc))]
            store.app_settings.set(key, value)
            reloaded = app_settings.load(store)
            self.pipeline.apply_settings(reloaded)
            return [
                ack_event(request_id, True),
                app_settings_event(reloaded),
            ]

        store.delete_everything()
        self._refresh_matcher_after_store_change(defer_matcher_refresh)
        return [ack_event(request_id, True)]

    def _library_payload(self, store: Store) -> list[dict[str, Any]]:
        payload = []
        for gesture in store.gestures.list():
            stats = store.gesture_stats.get(gesture.id)
            mapping = store.mappings.for_gesture(gesture.id)
            exemplars = store.exemplars.list(gesture.id)
            payload.append(
                {
                    "id": gesture.id,
                    "name": gesture.name,
                    "description": gesture.description,
                    "exemplar_count": len(exemplars),
                    "confirms": stats.confirms,
                    "rejects": stats.rejects,
                    "threshold_offset": stats.threshold_offset,
                    "mapping": (
                        {**mapping.action, "enabled": mapping.enabled}
                        if mapping is not None
                        else None
                    ),
                    "animation": (
                        _animation_payload(exemplars[0]) if exemplars else None
                    ),
                }
            )
        return payload

    def _settings_payload(self, store: Store | None) -> dict[str, Any]:
        configured_path = self.config.store.db_path
        store_path = (
            Path(configured_path).expanduser()
            if configured_path
            else default_store_path()
        )
        thresholds = self.gate.thresholds
        profile = load_active_profile(store) if store is not None else None
        payload: dict[str, Any] = {
            "clutch_mode": self.config.clutch.mode,
            "gate_thresholds": {
                "t1": thresholds.t1_top1,
                "t2": thresholds.t2_margin,
                "t3": thresholds.t3_incidental,
            },
            "camera_index": self.config.camera.index,
            "camera_state": self._camera_state,
            "camera_error": self._camera_error,
            "store_db_path": str(store_path.resolve()),
            "has_calibration_profile": profile is not None,
        }
        if profile is not None:
            payload["calibration"] = {
                "hand_size": profile.hand_size,
                "lighting_acceptable": profile.lighting.acceptable,
                "created_at": profile.created_at,
            }
        return payload

    def _refresh_matcher(self) -> None:
        if self.pipeline.matcher is not None:
            self.pipeline.matcher.refresh()

    def _refresh_matcher_after_store_change(self, defer: bool) -> None:
        if defer:
            self._matcher_refresh_pending = True
        else:
            self._refresh_matcher()

    def _set_camera_enabled(
        self,
        enabled: bool,
        request_id: object,
    ) -> list[PipelineEvent]:
        correlation_id = request_id if isinstance(request_id, str) else None
        restarting_active_camera = enabled and self._camera_state == "active"
        self._camera_enabled = enabled
        self._camera_state = "starting" if enabled else "off"
        self._camera_error = None
        self._camera_restart_requested = enabled
        self.preview_enabled = False

        events: list[PipelineEvent] = []
        if restarting_active_camera:
            events.extend(self.pipeline.force_pause("Paused - camera restarting"))
        elif not enabled:
            events.extend(self.pipeline.force_pause("Paused - camera is off"))
        events.append(camera_event(self._camera_state, id=correlation_id))
        return events

    def broadcast_preview(self, jpeg: bytes) -> None:
        with self._lock:
            preview_allowed = self.preview_enabled and (
                not self.config.ipc.enabled
                or (
                    self._camera_enabled
                    and self._camera_state == "active"
                )
            )
            ipc = self.ipc if preview_allowed else None
        if ipc is not None:
            ipc.broadcast_binary(jpeg)

    def _broadcast(self, events: list[PipelineEvent]) -> None:
        if self.ipc is None:
            return
        for event in events:
            self.ipc.broadcast(event)

    def _open_configured_store(self) -> Store | None:
        configured = self.config.store.db_path
        path = Path(configured).expanduser() if configured else default_store_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return Store(path)
        except Exception:
            logger.exception("Local store is unavailable; continuing without persistence")
            return None

    def _ensure_running(self) -> None:
        if self._stopped:
            raise RuntimeError("Daemon has stopped")


__all__ = ["Daemon", "default_store_path"]
