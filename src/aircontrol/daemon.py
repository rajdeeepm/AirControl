"""Lifecycle and command boundary for the AirControl gesture pipeline."""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
from collections import deque
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
    recording_event,
    settings_event,
)
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline, PipelineEvent
from aircontrol.profile import load_active_profile
from aircontrol.recording import (
    MAX_TAKE_SECONDS,
    RecordingConfig,
    RecordingOutcome,
    RecordingSession,
)
from aircontrol.store import Store
from aircontrol.trajectory import Trajectory, frame_from_observation


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
        "reset_app_settings",
        "delete_everything",
    }
)
_RECORDING_COMMAND_NAMES = frozenset(
    {
        "start_recording",
        "start_take",
        "end_take",
        "confirm_take",
        "discard_take",
        "finish_recording",
        "cancel_recording",
        "get_recording_state",
    }
)
_GESTURE_TEST_COMMAND_NAMES = frozenset(
    {
        "start_gesture_test",
        "stop_gesture_test",
    }
)
# Both sets need the store-owner-thread queueing dance: recording commands
# mutate the store, and start_gesture_test reads it (to look up the tested
# gesture's kind).
_QUEUED_RECORDING_COMMAND_NAMES = _RECORDING_COMMAND_NAMES | _GESTURE_TEST_COMMAND_NAMES


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


_RECORDING_PRIMARY_ANCHORS = (0, 5, 9, 13, 17)


def _recording_hand_center(observation: HandObservation) -> tuple[float, float]:
    """Return an observation's palm center, for recording primary-hand tracking.

    Self-contained here (not shared with pipeline.py's two-hand pointer/role
    continuity) since the recording flow only ever needs a single stable
    hand, not pointer/modifier roles.
    """
    landmarks = observation.landmarks
    anchors = tuple(
        landmarks[index] for index in _RECORDING_PRIMARY_ANCHORS if index < len(landmarks)
    )
    if not anchors:
        return (0.0, 0.0)
    return (
        sum(point.x for point in anchors) / len(anchors),
        sum(point.y for point in anchors) / len(anchors),
    )


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
        self._focus_requested = False
        self._store_owner_thread_id = threading.get_ident()
        self._matcher_refresh_pending = False
        self._pending_recording_commands: deque[dict[str, Any]] = deque()
        self._recording: RecordingSession | None = None
        self._recording_phase = "inactive"
        self._recording_name = ""
        self._recording_takes_confirmed = 0
        recording_defaults = RecordingConfig()
        self._recording_min_takes = recording_defaults.min_takes
        self._recording_max_takes = recording_defaults.max_takes
        self._recording_pending_frames: int | None = None
        self._recording_outcome: dict[str, Any] | None = None
        self._recording_capture_state = "idle"
        self._recording_last_refusal: str | None = None
        self._recording_kind = "motion"
        self._recording_pose_steady: bool | None = None
        self._recording_max_take_seconds = MAX_TAKE_SECONDS
        self._recording_primary_center: tuple[float, float] | None = None
        self._preview_before_recording = False
        self._preview_before_gesture_test = False
        self._owns_store = store is None
        self.store = store if store is not None else self._open_configured_store()
        profile = load_active_profile(self.store) if self.store is not None else None
        loaded_settings = (
            app_settings.load(self.store) if self.store is not None else None
        )
        click_mode = (
            loaded_settings["click_mode"]
            if loaded_settings is not None
            else app_settings.DEFAULTS["click_mode"]
        )
        self.config.tracking.max_hands = 2 if click_mode == "two_hand" else 1
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
        observation: HandObservation | tuple[HandObservation, ...] | None,
        now: float,
    ) -> list[PipelineEvent]:
        with self._lock:
            self._ensure_running()
            if threading.get_ident() == self._store_owner_thread_id:
                self._process_pending_commands()
            if self.config.ipc.enabled and (
                not self._camera_enabled or self._camera_state != "active"
            ):
                return []
            if self._matcher_refresh_pending:
                self._refresh_matcher()
                self._matcher_refresh_pending = False
            if observation is None:
                observations: tuple[HandObservation, ...] = ()
            elif isinstance(observation, tuple):
                observations = observation
            else:
                observations = (observation,)
            if self._recording is not None:
                primary = self._select_recording_primary(observations)
                frame = (
                    frame_from_observation(primary, now)
                    if primary is not None
                    else None
                )
                take = self._recording.feed(frame, now)
                capture_state = self._recording.capture_state
                state_changed = capture_state != self._recording_capture_state
                self._recording_capture_state = capture_state
                self._recording_last_refusal = self._recording.last_take_refused
                pose_steady = self._recording.capture_steady
                steady_changed = pose_steady != self._recording_pose_steady
                self._recording_pose_steady = pose_steady
                events = [self.pipeline.status()]
                if take is not None:
                    self._recording_phase = "pending_take"
                    self._recording_pending_frames = take.frame_count
                    events.append(self._recording_state_event())
                elif state_changed or steady_changed:
                    events.append(self._recording_state_event())
                self._broadcast(events)
                return events
            events = self.pipeline.process_hands(observations, now)
            self._broadcast(events)
            return events

    def command(self, name: str | dict[str, Any]) -> list[PipelineEvent]:
        message = {"name": name} if isinstance(name, str) else name
        command_name = message.get("name")
        with self._lock:
            self._ensure_running()
            if command_name in _RECORDING_COMMAND_NAMES:
                events = self._recording_command(message)
            elif command_name in _GESTURE_TEST_COMMAND_NAMES:
                events = self._gesture_test_command(message)
            elif command_name in _STORE_COMMAND_NAMES:
                events = self._store_command(message, self.store)
            elif command_name == "toggle_arm":
                if self._recording is not None:
                    events = self.pipeline.force_pause(
                        "Paused - recording gesture"
                    )
                elif self.config.ipc.enabled and self._camera_state != "active":
                    events = self.pipeline.force_pause(
                        "Camera must be active to arm"
                    )
                else:
                    events = self.pipeline.toggle_arm(time.monotonic())
            elif command_name == "pause":
                events = self.pipeline.force_pause("Paused manually")
            elif command_name == "undo":
                events = (
                    [self.pipeline.status()]
                    if self._recording is not None
                    else self.pipeline.undo()
                )
            elif command_name == "refresh_matcher":
                if self.pipeline.matcher is not None:
                    self.pipeline.matcher.refresh()
                events = [self.pipeline.status()]
            elif command_name == "get_status":
                events = [self.pipeline.status()]
            elif command_name == "focus_dashboard":
                self._focus_requested = True
                request_id = message.get("id")
                events = [
                    ack_event(
                        request_id if isinstance(request_id, str) else None,
                        True,
                    )
                ]
            elif command_name == "set_preview":
                preview_requested = (
                    message["enabled"] or self._recording is not None
                )
                self.preview_enabled = (
                    preview_requested
                    and self._preview_runtime_allows_streaming()
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

    def process_pending_commands(self) -> list[PipelineEvent]:
        """Run queued recording commands on the store-owning app thread."""
        if threading.get_ident() != self._store_owner_thread_id:
            raise RuntimeError(
                "pending commands must run on the daemon store owner thread"
            )
        with self._lock:
            self._ensure_running()
            return self._process_pending_commands()

    @property
    def camera_enabled(self) -> bool:
        with self._lock:
            return self._camera_enabled

    @property
    def camera_state(self) -> str:
        with self._lock:
            return self._camera_state

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording is not None

    @property
    def camera_error(self) -> str | None:
        with self._lock:
            return self._camera_error

    @property
    def focus_requested(self) -> bool:
        with self._lock:
            return self._focus_requested

    def take_focus_request(self) -> bool:
        """Consume the desktop loop's one-shot dashboard focus request."""
        with self._lock:
            self._ensure_running()
            requested = self._focus_requested
            self._focus_requested = False
            return requested

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
            elif state == "active" and self._recording is not None:
                self.preview_enabled = True
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
            self._pending_recording_commands.clear()
            self._recording = None
            self._reset_recording_state()
            self.pipeline.stop_gesture_test()
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
            command_name = command.get("name")
            if (
                command_name in _QUEUED_RECORDING_COMMAND_NAMES
                and threading.get_ident() != self._store_owner_thread_id
            ):
                with self._lock:
                    self._ensure_running()
                    self._pending_recording_commands.append(command)
            elif (
                command_name in _STORE_COMMAND_NAMES
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

    def _process_pending_commands(self) -> list[PipelineEvent]:
        events: list[PipelineEvent] = []
        while self._pending_recording_commands:
            command = self._pending_recording_commands.popleft()
            try:
                events.extend(self.command(command))
            except Exception:
                logger.exception("Queued recording command failed")
                request_id = command.get("id")
                failure = ack_event(
                    request_id if isinstance(request_id, str) else None,
                    False,
                    "command failed",
                )
                self._broadcast([failure])
                events.append(failure)
        return events

    def _select_recording_primary(
        self,
        observations: tuple[HandObservation, ...],
    ) -> HandObservation | None:
        """Pick a stable primary hand for the recording trajectory.

        Raw per-frame max-confidence selection lets the "primary" hand jump
        between two hands in view frame-to-frame, corrupting the recorded
        trajectory. Prefer continuity: once a hand has been selected, stick
        with whichever observed hand is closest to its last known position.
        Fall back to max-confidence when there is no prior selection or only
        one hand is visible.
        """
        if not observations:
            return None
        if len(observations) == 1 or self._recording_primary_center is None:
            selected = max(observations, key=lambda item: item.confidence)
        else:
            last_x, last_y = self._recording_primary_center

            def distance(observation: HandObservation) -> float:
                x, y = _recording_hand_center(observation)
                return math.hypot(x - last_x, y - last_y)

            selected = min(observations, key=distance)
        self._recording_primary_center = _recording_hand_center(selected)
        return selected

    def _recording_command(
        self,
        message: dict[str, Any],
    ) -> list[PipelineEvent]:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            request_id = None

        name = message["name"]
        if name == "get_recording_state":
            return [self._recording_state_event(request_id)]
        if name == "start_recording":
            return self._start_recording(message, request_id)
        if name == "cancel_recording":
            return self._cancel_recording(request_id)

        session = self._recording
        if session is None:
            return [ack_event(request_id, True)]
        if name == "start_take":
            session.begin_take(time.monotonic())
            self._recording_phase = "capturing"
            self._recording_capture_state = session.capture_state
            self._recording_last_refusal = session.last_take_refused
            self._recording_pose_steady = session.capture_steady
            return [
                ack_event(request_id, True),
                self._recording_state_event(),
            ]
        if name == "end_take":
            take = session.end_take(time.monotonic())
            self._recording_capture_state = session.capture_state
            self._recording_last_refusal = session.last_take_refused
            self._recording_pose_steady = session.capture_steady
            if take is not None:
                self._recording_phase = "pending_take"
                self._recording_pending_frames = take.frame_count
            else:
                self._recording_phase = "capturing"
                self._recording_pending_frames = None
            return [
                ack_event(request_id, True),
                self._recording_state_event(),
            ]
        if name == "confirm_take":
            session.confirm_take()
            self._recording_phase = "capturing"
            self._recording_takes_confirmed = session.takes_confirmed
            self._recording_pending_frames = None
            self._recording_capture_state = session.capture_state
            self._recording_last_refusal = session.last_take_refused
            self._recording_pose_steady = session.capture_steady
            return [
                ack_event(request_id, True),
                self._recording_state_event(),
            ]
        if name == "discard_take":
            session.discard_take()
            self._recording_phase = "capturing"
            self._recording_takes_confirmed = session.takes_confirmed
            self._recording_pending_frames = None
            self._recording_capture_state = session.capture_state
            self._recording_last_refusal = session.last_take_refused
            self._recording_pose_steady = session.capture_steady
            return [
                ack_event(request_id, True),
                self._recording_state_event(),
            ]

        outcome = session.finish()
        self._recording_takes_confirmed = session.takes_confirmed
        self._recording_phase = "saved" if outcome.saved else "refused"
        self._recording_pending_frames = None
        self._recording_capture_state = "idle"
        self._recording_last_refusal = None
        self._recording_pose_steady = None
        self._recording_primary_center = None
        self._recording_outcome = self._recording_outcome_payload(outcome)
        if outcome.saved:
            self._refresh_matcher()
        self._recording = None
        self.preview_enabled = (
            self._preview_before_recording
            and self._preview_runtime_allows_streaming()
        )
        events = self.pipeline.force_pause("Paused - recording finished")
        events.extend(
            [
                ack_event(request_id, True),
                self._recording_state_event(),
            ]
        )
        return events

    def _start_recording(
        self,
        message: dict[str, Any],
        request_id: str | None,
    ) -> list[PipelineEvent]:
        if self._recording is not None:
            return [ack_event(request_id, False, "recording already active")]
        if self.store is None:
            return [ack_event(request_id, False, "no store")]
        # Defensive: a gesture test the caller forgot to stop must never
        # leak into a new recording session and silently suppress dispatch
        # for whatever the user records next.
        self._stop_gesture_test()

        profile = load_active_profile(self.store)
        if profile is None:
            return [ack_event(request_id, False, "calibrate first")]
        gesture_name = message.get("gesture_name")
        if not isinstance(gesture_name, str) or not gesture_name.strip():
            return [ack_event(request_id, False, "name required")]
        gesture_kind = message.get("gesture_kind", "motion")
        if gesture_kind not in ("motion", "pose"):
            return [ack_event(request_id, False, "invalid gesture_kind")]

        session = RecordingSession(
            gesture_name.strip(),
            self.store,
            profile,
            self.config,
            kind=gesture_kind,
        )
        events = self.pipeline.force_pause("Paused - recording gesture")
        self.pipeline.last_sample = None
        self._recording = session
        self._recording_phase = "capturing"
        self._recording_name = session.name
        self._recording_kind = session.kind
        self._recording_takes_confirmed = 0
        self._recording_min_takes = session.rec_config.min_takes
        self._recording_max_takes = session.rec_config.max_takes
        self._recording_pending_frames = None
        self._recording_outcome = None
        self._recording_capture_state = "idle"
        self._recording_last_refusal = None
        self._recording_pose_steady = None
        self._recording_max_take_seconds = session.max_take_seconds
        self._recording_primary_center = None
        self._preview_before_recording = self.preview_enabled
        self.preview_enabled = True
        events.extend(
            [
                ack_event(request_id, True),
                self._recording_state_event(),
            ]
        )
        return events

    def _cancel_recording(
        self,
        request_id: str | None,
    ) -> list[PipelineEvent]:
        events: list[PipelineEvent] = []
        # Cancel is also the escape hatch a dialog dismissal falls back to;
        # a still-active gesture test must not survive it.
        self._stop_gesture_test()
        if self._recording is not None:
            events.extend(
                self.pipeline.force_pause("Paused - recording cancelled")
            )
            self.preview_enabled = (
                self._preview_before_recording
                and self._preview_runtime_allows_streaming()
            )
        self._recording = None
        self._reset_recording_state()
        events.extend(
            [
                ack_event(request_id, True),
                self._recording_state_event(),
            ]
        )
        return events

    def _gesture_test_command(
        self,
        message: dict[str, Any],
    ) -> list[PipelineEvent]:
        """Handle start_gesture_test/stop_gesture_test.

        Runs on the store-owner thread (queued like recording commands) so
        the gesture-kind lookup below is always safe.
        """
        request_id = message.get("id")
        if not isinstance(request_id, str):
            request_id = None

        if message["name"] == "stop_gesture_test":
            self._stop_gesture_test()
            return [ack_event(request_id, True)]

        if self.store is None:
            return [ack_event(request_id, False, "no store")]
        gesture_id = message.get("gesture_id")
        if isinstance(gesture_id, bool) or not isinstance(gesture_id, int):
            return [ack_event(request_id, False, "gesture_id must be an integer")]
        gesture = self.store.gestures.get(gesture_id)
        if gesture is None:
            return [ack_event(request_id, False, "gesture not found")]

        self._preview_before_gesture_test = self.preview_enabled
        self.preview_enabled = True
        self.pipeline.start_gesture_test(gesture_id, gesture.kind)
        return [ack_event(request_id, True)]

    def _stop_gesture_test(self) -> None:
        """Leave gesture-test mode and restore the prior preview state.

        Idempotent, and safe to call even when no test is active (used
        defensively from recording start/cancel/stop so a caller that
        forgot to send stop_gesture_test can never leave dispatch
        permanently suppressed for the tested gesture).
        """
        if self.pipeline.gesture_test_id is None:
            return
        self.pipeline.stop_gesture_test()
        self.preview_enabled = (
            self._preview_before_gesture_test
            and self._preview_runtime_allows_streaming()
        )

    def _recording_state_event(
        self,
        request_id: str | None = None,
    ) -> PipelineEvent:
        return recording_event(
            phase=self._recording_phase,
            name=self._recording_name,
            takes_confirmed=self._recording_takes_confirmed,
            min_takes=self._recording_min_takes,
            max_takes=self._recording_max_takes,
            pending_take=self._recording_phase == "pending_take",
            pending_take_frames=self._recording_pending_frames,
            capture_state=self._recording_capture_state,
            max_take_seconds=self._recording_max_take_seconds,
            capture_elapsed_seconds=self._recording_capture_elapsed_seconds(),
            last_take_refused=self._recording_last_refusal,
            gesture_kind=self._recording_kind,
            pose_steady=self._recording_pose_steady,
            outcome=(
                dict(self._recording_outcome)
                if self._recording_outcome is not None
                else None
            ),
            id=request_id,
        )

    def _recording_capture_elapsed_seconds(self) -> float:
        """Seconds since the currently open capture window began, if any.

        Zero when no window is open (idle, pending_take, or no session) --
        the UI runs its own local stopwatch from the moment it observes the
        "capturing" transition, so this only needs to seed that clock.
        """
        session = self._recording
        if session is None:
            return 0.0
        started_at = session.capture_started_at
        if started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - started_at)

    def _recording_outcome_payload(
        self,
        outcome: RecordingOutcome,
    ) -> dict[str, Any]:
        conflict_name: str | None = None
        if outcome.conflict_gesture_id is not None and self.store is not None:
            conflict = self.store.gestures.get(outcome.conflict_gesture_id)
            if conflict is not None:
                conflict_name = conflict.name
        return {
            "saved": outcome.saved,
            "reason": outcome.reason,
            "gesture_id": outcome.gesture_id,
            "conflict_gesture_name": conflict_name,
        }

    def _reset_recording_state(self) -> None:
        defaults = RecordingConfig()
        self._recording_phase = "inactive"
        self._recording_name = ""
        self._recording_takes_confirmed = 0
        self._recording_min_takes = defaults.min_takes
        self._recording_max_takes = defaults.max_takes
        self._recording_pending_frames = None
        self._recording_outcome = None
        self._recording_capture_state = "idle"
        self._recording_last_refusal = None
        self._recording_kind = "motion"
        self._recording_pose_steady = None
        self._recording_max_take_seconds = MAX_TAKE_SECONDS
        self._recording_primary_center = None

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
            current = app_settings.load(store)
            try:
                value = app_settings.validate(
                    key,
                    message["value"],
                    settings=current,
                )
            except ValueError as exc:
                return [ack_event(request_id, False, str(exc))]
            store.app_settings.set(key, value)
            reloaded = app_settings.load(store)
            setting_events = self.pipeline.apply_settings(reloaded)
            desired_max_hands = (
                2 if reloaded["click_mode"] == "two_hand" else 1
            )
            tracking_changed = (
                self.config.tracking.max_hands != desired_max_hands
            )
            self.config.tracking.max_hands = desired_max_hands
            events = [
                ack_event(request_id, True),
                app_settings_event(reloaded),
            ]
            events.extend(setting_events)
            if (
                tracking_changed
                and self._camera_enabled
                and self._camera_state in {"active", "starting"}
            ):
                events.extend(self._set_camera_enabled(True, None))
            return events
        if name == "reset_app_settings":
            app_settings.reset(store)
            reloaded = app_settings.load(store)
            setting_events = self.pipeline.apply_settings(reloaded)
            desired_max_hands = (
                2 if reloaded["click_mode"] == "two_hand" else 1
            )
            tracking_changed = (
                self.config.tracking.max_hands != desired_max_hands
            )
            self.config.tracking.max_hands = desired_max_hands
            events = [app_settings_event(reloaded, request_id)]
            events.extend(setting_events)
            if (
                tracking_changed
                and self._camera_enabled
                and self._camera_state in {"active", "starting"}
            ):
                events.extend(self._set_camera_enabled(True, None))
            return events

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
                    "kind": gesture.kind,
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
        click_mode = (
            app_settings.load(store)["click_mode"]
            if store is not None
            else app_settings.DEFAULTS["click_mode"]
        )
        payload: dict[str, Any] = {
            "clutch_mode": self.config.clutch.mode,
            "click_mode": click_mode,
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
            preview_allowed = (
                self.preview_enabled
                and self._preview_runtime_allows_streaming()
            )
            ipc = self.ipc if preview_allowed else None
        if ipc is not None:
            ipc.broadcast_binary(jpeg)

    def _preview_runtime_allows_streaming(self) -> bool:
        return not self.config.ipc.enabled or (
            self._camera_enabled and self._camera_state == "active"
        )

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
