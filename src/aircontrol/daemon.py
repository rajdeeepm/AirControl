"""Lifecycle and command boundary for the AirControl gesture pipeline."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.domain import HandObservation
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.ipc import (
    ack_event,
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
        self._owns_store = store is None
        self.store = store if store is not None else self._open_configured_store()
        profile = load_active_profile(self.store) if self.store is not None else None
        self.metrics = Metrics()
        self.gate = ConfidenceGate(GateThresholds())
        self.pipeline = Pipeline(
            config,
            controller,
            store=self.store,
            metrics=self.metrics,
            gate=self.gate,
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
            events = self.pipeline.process(observation, now)
        self._broadcast(events)
        return events

    def command(self, name: str | dict[str, Any]) -> list[PipelineEvent]:
        message = {"name": name} if isinstance(name, str) else name
        command_name = message.get("name")
        with self._lock:
            self._ensure_running()
            if command_name in _STORE_COMMAND_NAMES:
                events = self._store_command(message)
            elif command_name == "toggle_arm":
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
                self.preview_enabled = message["enabled"]
                request_id = message.get("id")
                events = [
                    ack_event(
                        request_id if isinstance(request_id, str) else None,
                        True,
                    )
                ]
            elif command_name == "quit":
                self.quit_requested = True
                events = self.pipeline.force_pause("Stopped")
            else:
                raise ValueError(f"Unsupported daemon command: {command_name}")
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
        self.command(command)

    def _store_command(self, message: dict[str, Any]) -> list[PipelineEvent]:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            request_id = None

        store = self.store
        if store is None:
            return [ack_event(request_id, False, "no store")]

        name = message["name"]
        if name == "list_library":
            return [library_event(self._library_payload(store), request_id)]
        if name == "set_mapping":
            store.mappings.set(
                message["gesture_id"],
                message["action"],
                context=message.get("context", "global"),
            )
            return [ack_event(request_id, True)]
        if name == "delete_gesture":
            store.gestures.delete(message["gesture_id"])
            self._refresh_matcher()
            return [ack_event(request_id, True)]
        if name == "rename_gesture":
            store.gestures.rename(message["gesture_id"], message["new_name"])
            self._refresh_matcher()
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
        if name == "get_settings":
            return [settings_event(self._settings_payload(), request_id)]

        store.delete_everything()
        self._refresh_matcher()
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
                    "mapping": dict(mapping.action) if mapping is not None else None,
                    "animation": (
                        _animation_payload(exemplars[0]) if exemplars else None
                    ),
                }
            )
        return payload

    def _settings_payload(self) -> dict[str, Any]:
        configured_path = self.config.store.db_path
        store_path = (
            Path(configured_path).expanduser()
            if configured_path
            else default_store_path()
        )
        thresholds = self.pipeline.gate.thresholds
        return {
            "clutch_mode": self.config.clutch.mode,
            "gate_thresholds": {
                "t1": thresholds.t1_top1,
                "t2": thresholds.t2_margin,
                "t3": thresholds.t3_incidental,
            },
            "camera_index": self.config.camera.index,
            "store_db_path": str(store_path.resolve()),
        }

    def _refresh_matcher(self) -> None:
        if self.pipeline.matcher is not None:
            self.pipeline.matcher.refresh()

    def broadcast_preview(self, jpeg: bytes) -> None:
        with self._lock:
            ipc = self.ipc if self.preview_enabled else None
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
