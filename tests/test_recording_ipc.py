from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.domain import HandObservation, Point3D
from aircontrol.ipc import (
    IpcProtocolError,
    ack_event,
    parse_command,
    recording_event,
)
from aircontrol.profile import save_profile
from aircontrol.recording import POSE_BUILTIN_REFUSAL, POSE_UNSTABLE_REFUSAL, RecordingConfig
from aircontrol.store import Store
from tests.test_matcher_integration import (
    _add_gesture,
    _candidate_trajectory,
    _configured_app,
    _profile,
    _scripted_observations,
)


def _command(
    name: str,
    *,
    id: str = "recording-request",
    **fields: object,
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "command",
        "name": name,
        "id": id,
        **fields,
    }


def _make_daemon(
    store: Store,
    *,
    config: AppConfig | None = None,
    ipc: object | None = None,
) -> Daemon:
    effective_config = config or _configured_app()
    return Daemon(
        effective_config,
        practice=True,
        controller=ActionController(
            effective_config.input.pointer_pixels_per_palm,
            practice=True,
        ),
        store=store,
        ipc=ipc,
    )


def _recording(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(event for event in reversed(events) if event["type"] == "recording")


def _feed_take(
    daemon: Daemon,
    *,
    base: float,
    kind: str = "horizontal",
    amplitude: float = 1.03,
    noise: float = 0.009,
    phase: float = 0.23,
) -> list[dict[str, Any]]:
    """Drive one explicit start_take -> feed(...) -> end_take cycle."""
    events: list[dict[str, Any]] = []
    events.extend(daemon.command(_command("start_take", id="start-take")))
    for offset, observation in _scripted_observations(
        kind,
        amplitude=amplitude,
        noise=noise,
        phase=phase,
    ):
        events.extend(daemon.feed(observation, base + offset))
    events.extend(daemon.command(_command("end_take", id="end-take")))
    return events


def _record_good_takes(daemon: Daemon) -> None:
    for index in range(RecordingConfig().min_takes):
        centered = index - 3.5
        base = index * 2.0
        events = _feed_take(
            daemon,
            base=base,
            amplitude=1.0 + centered * 0.012,
            noise=0.003 + index * 0.0003,
            phase=index * 0.41,
        )
        assert _recording(events)["phase"] == "pending_take"
        daemon.command(_command("confirm_take", id=f"confirm-{index}"))
        daemon.feed(None, base + 1.1)


class _CaptureTransport:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.callback: Callable[[dict[str, Any]], None] | None = None

    def on_command(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self.callback = callback

    def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def send(self, command: dict[str, Any]) -> None:
        assert self.callback is not None
        self.callback(command)


@pytest.mark.parametrize(
    "payload",
    [
        _command("start_recording", gesture_name="Wave"),
        _command("confirm_take"),
        _command("discard_take"),
        _command("finish_recording"),
        _command("cancel_recording"),
        _command("get_recording_state"),
        {"v": 1, "type": "command", "name": "confirm_take"},
    ],
)
def test_parse_command_accepts_recording_commands(payload: dict[str, Any]) -> None:
    assert parse_command(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    "payload",
    [
        _command("start_recording"),
        _command("start_recording", gesture_name=""),
        _command("start_recording", gesture_name="   "),
        _command("start_recording", gesture_name=42),
    ],
)
def test_start_recording_requires_non_empty_gesture_name(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(IpcProtocolError):
        parse_command(json.dumps(payload))


def test_recording_event_has_exact_v1_shape_and_validates_phase() -> None:
    outcome = {
        "saved": True,
        "reason": "saved",
        "gesture_id": 7,
        "conflict_gesture_name": None,
    }

    assert recording_event(
        phase="saved",
        name="Wave",
        takes_confirmed=8,
        min_takes=8,
        max_takes=12,
        pending_take=False,
        pending_take_frames=None,
        outcome=outcome,
        id="state-1",
    ) == {
        "v": 1,
        "type": "recording",
        "phase": "saved",
        "name": "Wave",
        "takes_confirmed": 8,
        "min_takes": 8,
        "max_takes": 12,
        "pending_take": False,
        "pending_take_frames": None,
        "capture_state": "idle",
        "max_take_seconds": 10.0,
        "capture_elapsed_seconds": 0.0,
        "last_take_refused": None,
        "gesture_kind": "motion",
        "pose_steady": None,
        "outcome": outcome,
        "id": "state-1",
    }

    assert recording_event(
        phase="capturing",
        name="Wave",
        takes_confirmed=0,
        min_takes=8,
        max_takes=12,
        capture_state="capturing",
        max_take_seconds=10.0,
        capture_elapsed_seconds=1.4,
    )["capture_state"] == "capturing"

    with pytest.raises(IpcProtocolError):
        recording_event(
            phase="capture",
            name="Wave",
            takes_confirmed=0,
            min_takes=8,
            max_takes=12,
        )

    with pytest.raises(IpcProtocolError):
        recording_event(
            phase="capturing",
            name="Wave",
            takes_confirmed=0,
            min_takes=8,
            max_takes=12,
            capture_state="not-a-real-state",
        )


def test_start_recording_requires_store_and_active_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _configured_app()
    monkeypatch.setattr(Daemon, "_open_configured_store", lambda self: None)
    no_store = Daemon(
        config,
        practice=True,
        controller=ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        ),
    )
    try:
        assert no_store.command(
            _command("start_recording", gesture_name="Wave", id="no-store")
        ) == [ack_event("no-store", False, "no store")]
    finally:
        no_store.stop()

    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            assert daemon.command(
                _command(
                    "start_recording",
                    gesture_name="Wave",
                    id="no-profile",
                )
            ) == [ack_event("no-profile", False, "calibrate first")]
            assert daemon._recording is None
        finally:
            daemon.stop()


def test_start_enters_capturing_force_pauses_and_broadcasts_state() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command("toggle_arm")
            assert daemon.pipeline.engine.armed

            events = daemon.command(
                _command("start_recording", gesture_name="Wave", id="start-1")
            )

            assert ack_event("start-1", True) in events
            assert _recording(events) == recording_event(
                phase="capturing",
                name="Wave",
                takes_confirmed=0,
                min_takes=8,
                max_takes=12,
            )
            assert daemon._recording is not None
            assert not daemon.pipeline.engine.armed
            assert daemon.preview_enabled

            preview_events = daemon.command(
                {
                    "name": "set_preview",
                    "enabled": False,
                    "id": "preview-off-during-recording",
                }
            )
            assert preview_events == [
                ack_event("preview-off-during-recording", True)
            ]
            assert daemon.preview_enabled

            daemon.command("toggle_arm")
            assert not daemon.pipeline.engine.armed
        finally:
            daemon.stop()


def test_is_recording_flips_true_on_start_and_false_on_cancel_or_finish() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            assert not daemon.is_recording

            daemon.command(_command("start_recording", gesture_name="Wave"))
            assert daemon.is_recording

            daemon.command(_command("cancel_recording"))
            assert not daemon.is_recording

            daemon.command(_command("start_recording", gesture_name="Wave"))
            assert daemon.is_recording
            _record_good_takes(daemon)
            daemon.command(_command("finish_recording"))
            assert not daemon.is_recording
        finally:
            daemon.stop()


def test_scripted_take_can_be_confirmed_then_another_discarded() -> None:
    with Store(":memory:") as store:
        profile = _profile()
        save_profile(store, profile)
        config = _configured_app()
        config.ipc.enabled = True
        daemon = _make_daemon(store, config=config)
        try:
            daemon.command({"name": "set_camera", "enabled": True})
            assert daemon.take_camera_restart_request()
            daemon.set_camera_state("active")
            daemon.command(_command("start_recording", gesture_name="Wave"))
            assert daemon._recording is not None
            assert daemon._recording.profile.motion == profile.motion
            daemon.command(
                {
                    "name": "set_preview",
                    "enabled": False,
                    "id": "live-preview-off-during-recording",
                }
            )
            assert daemon.preview_enabled

            pending = _recording(_feed_take(daemon, base=0.0))
            assert pending["phase"] == "pending_take"
            assert pending["pending_take"] is True
            assert pending["pending_take_frames"] == 14

            confirmed = daemon.command(
                _command("confirm_take", id="confirm-first")
            )
            assert ack_event("confirm-first", True) in confirmed
            assert _recording(confirmed) == recording_event(
                phase="capturing",
                name="Wave",
                takes_confirmed=1,
                min_takes=8,
                max_takes=12,
            )

            daemon.feed(None, 1.1)
            second_pending = _recording(_feed_take(daemon, base=2.0))
            assert second_pending["phase"] == "pending_take"

            discarded = daemon.command(
                _command("discard_take", id="discard-second")
            )
            assert ack_event("discard-second", True) in discarded
            assert _recording(discarded)["takes_confirmed"] == 1
            assert _recording(discarded)["phase"] == "capturing"
            assert _recording(discarded)["pending_take"] is False

            no_op = daemon.command(_command("confirm_take", id="no-pending"))
            assert ack_event("no-pending", True) in no_op
            assert _recording(no_op)["takes_confirmed"] == 1
        finally:
            daemon.stop()


@pytest.mark.parametrize("terminal_command", ["finish_recording", "cancel_recording"])
def test_recording_end_does_not_restore_preview_after_camera_is_turned_off(
    terminal_command: str,
) -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        config = _configured_app()
        config.ipc.enabled = True
        daemon = _make_daemon(store, config=config)
        try:
            daemon.command({"name": "set_camera", "enabled": True})
            assert daemon.take_camera_restart_request()
            daemon.set_camera_state("active")
            daemon.command({"name": "set_preview", "enabled": True})
            assert daemon.preview_enabled
            daemon.command(_command("start_recording", gesture_name="Wave"))

            daemon.command({"name": "set_camera", "enabled": False})
            assert daemon.camera_state == "off"
            assert not daemon.preview_enabled

            daemon.command(_command(terminal_command))

            assert not daemon.preview_enabled
        finally:
            daemon.stop()


def test_recording_feed_bypasses_normal_pipeline_and_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            daemon.pipeline.controller.sink.events.clear()

            def fail_process(*_args: object, **_kwargs: object) -> None:
                pytest.fail("normal pipeline ran during recording")

            monkeypatch.setattr(daemon.pipeline, "process_hands", fail_process)
            observation = _scripted_observations("horizontal")[0][1]

            events = daemon.feed(observation, 0.0)

            assert not any(event["type"] == "action" for event in events)
            assert daemon.pipeline.controller.sink.events == []
        finally:
            daemon.stop()


def test_recording_uses_highest_confidence_observation_and_missing_hand() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            assert daemon._recording is not None
            captured: list[tuple[object, float]] = []
            daemon._recording.feed = lambda frame, now: captured.append((frame, now))

            base = _scripted_observations("horizontal")[0][1]
            low = replace(base, handedness="Left", confidence=0.2)
            high = replace(base, handedness="Right", confidence=0.9)
            daemon.feed((low, high), 3.0)
            daemon.feed(None, 4.0)

            frame, timestamp = captured[0]
            assert frame is not None
            assert frame.handedness == "Right"
            assert frame.landmarks == high.landmarks
            assert timestamp == 3.0
            assert captured[1] == (None, 4.0)
        finally:
            daemon.stop()


def _jumping_hand(observation: object, *, confidence: float) -> object:
    """Build a second hand far from ``observation``, for FIX 2 coverage."""
    return replace(
        observation,
        handedness="Left",
        confidence=confidence,
        landmarks=tuple(
            Point3D(x=point.x + 5.0, y=point.y + 5.0, z=point.z)
            for point in observation.landmarks
        ),
    )


def test_recording_primary_hand_stays_stable_despite_a_higher_confidence_jumper() -> None:
    """FIX 2: once a primary hand is selected, a second hand that jumps in
    and out with higher raw confidence must not steal primary status frame
    to frame -- continuity (nearest to the last selected hand) wins.
    """
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            assert daemon._recording is not None
            captured: list[tuple[object, float]] = []
            daemon._recording.feed = lambda frame, now: captured.append((frame, now))

            scripted = _scripted_observations("horizontal")
            for index, (offset, observation) in enumerate(scripted):
                # Lower confidence on the very first frame only, so the
                # initial (no-continuity) pick still lands on the real hand;
                # from then on the jumper out-scores it on raw confidence
                # alone, and only position continuity should keep it out.
                jumper = _jumping_hand(
                    observation,
                    confidence=0.0 if index == 0 else 1.0,
                )
                daemon.feed((observation, jumper), offset)

            assert len(captured) == len(scripted)
            for (frame, timestamp), (offset, observation) in zip(captured, scripted):
                assert frame is not None
                assert frame.handedness == "Right"
                assert frame.landmarks == observation.landmarks
                assert timestamp == offset
        finally:
            daemon.stop()


def test_recording_still_captures_a_take_despite_a_jumping_second_hand() -> None:
    """FIX 2: a jumping second hand must not corrupt the trajectory enough to
    block a take from completing.
    """
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            events: list[dict[str, Any]] = list(
                daemon.command(_command("start_take", id="start-take"))
            )
            for index, (offset, observation) in enumerate(
                _scripted_observations("horizontal")
            ):
                jumper = _jumping_hand(observation, confidence=1.0)
                events.extend(daemon.feed((observation, jumper), offset))
            events.extend(daemon.command(_command("end_take", id="end-take")))

            pending = _recording(events)
            assert pending["phase"] == "pending_take"
            assert pending["pending_take"] is True
        finally:
            daemon.stop()


def test_daemon_recording_capture_state_transitions_and_broadcasts_on_change() -> None:
    """capture_state only ever moves idle -> capturing -> pending_take, only
    on the user's explicit start_take/end_take commands -- motion alone,
    without those commands, never changes it -- and every daemon broadcast
    reflects an actual transition, not per-frame chatter.
    """
    with Store(":memory:") as store:
        save_profile(store, _profile())
        transport = _CaptureTransport()
        daemon = _make_daemon(store, ipc=transport)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            transport.events.clear()

            scripted = _scripted_observations("horizontal")
            seen_states: list[str] = []

            # Motion alone, with no start_take, never leaves "idle".
            for offset, observation in scripted[:5]:
                daemon.feed(observation, offset)
                seen_states.append(daemon._recording_capture_state)
            assert set(seen_states) == {"idle"}

            daemon.command(_command("start_take", id="start-take"))
            seen_states.append(daemon._recording_capture_state)
            assert seen_states[-1] == "capturing"

            for offset, observation in scripted[5:]:
                daemon.feed(observation, offset)
                seen_states.append(daemon._recording_capture_state)
            # Still capturing -- nothing finalises without end_take.
            assert seen_states[-1] == "capturing"

            daemon.command(_command("end_take", id="end-take"))
            seen_states.append(daemon._recording_capture_state)
            assert seen_states[-1] == "pending_take"

            recording_events = [
                event for event in transport.events if event["type"] == "recording"
            ]
            reported_states = [event["capture_state"] for event in recording_events]

            # Every broadcast reflects an actual change -- no back-to-back
            # duplicates -- and it moves idle -> capturing -> pending_take.
            assert all(
                reported_states[index] != reported_states[index - 1]
                for index in range(1, len(reported_states))
            )
            assert reported_states[0] == "capturing"
            assert reported_states[-1] == "pending_take"
        finally:
            daemon.stop()


def test_finish_saves_refreshes_matcher_and_leaves_control_disarmed() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            _record_good_takes(daemon)

            events = daemon.command(
                _command("finish_recording", id="finish-saved")
            )

            assert ack_event("finish-saved", True) in events
            state = _recording(events)
            assert state["phase"] == "saved"
            assert state["takes_confirmed"] == 8
            assert state["outcome"] == {
                "saved": True,
                "reason": "saved",
                "gesture_id": state["outcome"]["gesture_id"],
                "conflict_gesture_name": None,
            }
            gesture_id = state["outcome"]["gesture_id"]
            assert isinstance(gesture_id, int)
            assert daemon._recording is None
            assert not daemon.pipeline.engine.armed
            assert daemon.pipeline.matcher is not None
            assert (
                daemon.pipeline.matcher.match(
                    _candidate_trajectory("horizontal")
                ).gesture_id
                == gesture_id
            )
        finally:
            daemon.stop()


def test_finish_refuses_too_few_takes() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Short"))
            pending = _feed_take(daemon, base=0.0)
            assert _recording(pending)["phase"] == "pending_take"
            daemon.command(_command("confirm_take"))

            events = daemon.command(
                _command("finish_recording", id="finish-refused")
            )

            assert ack_event("finish-refused", True) in events
            assert _recording(events)["phase"] == "refused"
            assert _recording(events)["outcome"] == {
                "saved": False,
                "reason": "need more takes",
                "gesture_id": None,
                "conflict_gesture_name": None,
            }
            assert daemon._recording is None
            assert store.gestures.list() == []
        finally:
            daemon.stop()


def test_finish_refuses_too_similar_and_maps_conflict_name() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        existing_id = _add_gesture(
            store,
            "Existing",
            "horizontal",
            {"kind": "switch_next"},
        )
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Duplicate"))
            _record_good_takes(daemon)

            events = daemon.command(
                _command("finish_recording", id="finish-similar")
            )

            assert ack_event("finish-similar", True) in events
            assert _recording(events)["phase"] == "refused"
            assert _recording(events)["outcome"] == {
                "saved": False,
                "reason": "too similar",
                "gesture_id": None,
                "conflict_gesture_name": "Existing",
            }
            assert [gesture.id for gesture in store.gestures.list()] == [existing_id]
        finally:
            daemon.stop()


def test_cancel_drops_pending_session_and_state_query_reports_inactive() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            assert _recording(_feed_take(daemon, base=0.0))["phase"] == "pending_take"

            events = daemon.command(
                _command("cancel_recording", id="cancel-recording")
            )

            assert ack_event("cancel-recording", True) in events
            assert _recording(events) == recording_event(
                phase="inactive",
                name="",
                takes_confirmed=0,
                min_takes=8,
                max_takes=12,
            )
            assert daemon._recording is None
            assert store.gestures.list() == []

            query = daemon.command(
                _command("get_recording_state", id="recording-state")
            )
            assert query == [
                recording_event(
                    phase="inactive",
                    name="",
                    takes_confirmed=0,
                    min_takes=8,
                    max_takes=12,
                    id="recording-state",
                )
            ]
        finally:
            daemon.stop()


def test_live_ipc_recording_commands_are_pumped_on_store_owner_thread() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        transport = _CaptureTransport()
        daemon = _make_daemon(store, ipc=transport)
        try:
            command = _command(
                "start_recording",
                gesture_name="Threaded",
                id="threaded-start",
            )
            thread = threading.Thread(target=transport.send, args=(command,))
            thread.start()
            thread.join()

            assert transport.events == []
            assert daemon._recording is None

            daemon.process_pending_commands()

            assert ack_event("threaded-start", True) in transport.events
            assert _recording(transport.events)["phase"] == "capturing"
            assert daemon._recording is not None
        finally:
            daemon.stop()


# --- Static hand-pose recording over IPC ------------------------------------

_POSE_FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


def _pose_observation(*, jitter: float = 0.0, seed: float = 0.0) -> HandObservation:
    """A relaxed, uniformly half-curled hand -- distinct from any built-in pose."""
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for _name, (mcp, pip, dip, tip, x, y) in _POSE_FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.08)
        points[dip] = Point3D(x + 0.05, y - 0.13)
        points[tip] = Point3D(x + 0.02, y - 0.20)
    if jitter:
        points = [
            Point3D(
                point.x + jitter * math.sin(seed + index * 1.7),
                point.y + jitter * math.cos(seed + index * 2.3),
                point.z,
            )
            for index, point in enumerate(points)
        ]
    return HandObservation(
        landmarks=tuple(points),
        handedness="Right",
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


def _fist_observation() -> HandObservation:
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for _name, (mcp, pip, dip, tip, x, y) in _POSE_FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.07)
        points[dip] = Point3D(x + 0.035, y - 0.01)
        points[tip] = Point3D(x + 0.018, y + 0.045)
    return HandObservation(
        landmarks=tuple(points),
        handedness="Right",
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


def _run_pose_take(
    daemon: Daemon,
    observation_fn: Callable[[int], HandObservation],
    *,
    count: int = 12,
    dt: float = 0.05,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events.extend(daemon.command(_command("start_take", id="pose-start-take")))
    for index in range(count):
        events.extend(daemon.feed(observation_fn(index), index * dt))
    events.extend(daemon.command(_command("end_take", id="pose-end-take")))
    return events


def test_start_recording_with_pose_kind_starts_a_pose_session() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command("toggle_arm")

            events = daemon.command(
                _command(
                    "start_recording",
                    gesture_name="Peace sign",
                    gesture_kind="pose",
                    id="start-pose",
                )
            )

            assert ack_event("start-pose", True) in events
            recording = _recording(events)
            assert recording["gesture_kind"] == "pose"
            assert daemon._recording is not None
            assert daemon._recording.kind == "pose"
        finally:
            daemon.stop()


def test_start_recording_rejects_an_invalid_gesture_kind() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            events = daemon.command(
                _command(
                    "start_recording",
                    gesture_name="Bad",
                    gesture_kind="wiggle",
                    id="bad-kind",
                )
            )

            assert events == [ack_event("bad-kind", False, "invalid gesture_kind")]
            assert daemon._recording is None
        finally:
            daemon.stop()


def test_pose_recording_reports_steady_signal_and_captures_a_pending_take() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(
                _command(
                    "start_recording",
                    gesture_name="Peace sign",
                    gesture_kind="pose",
                )
            )

            events = _run_pose_take(daemon, lambda index: _pose_observation())

            # The live steadiness signal must have reported "steady" at some
            # point while the shape was held (it resets to None once the
            # capture window closes, so this is checked mid-stream rather
            # than on the daemon's post-close state).
            steady_events = [
                event
                for event in events
                if event.get("type") == "recording" and event.get("pose_steady") is True
            ]
            assert steady_events != []

            final = _recording(events)
            assert final["phase"] == "pending_take"
            assert final["gesture_kind"] == "pose"
            assert final["last_take_refused"] is None
        finally:
            daemon.stop()


def test_pose_recording_reports_unstable_refusal_without_consuming_a_take() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(
                _command(
                    "start_recording",
                    gesture_name="Peace sign",
                    gesture_kind="pose",
                )
            )

            events = _run_pose_take(
                daemon,
                lambda index: _pose_observation(jitter=0.08, seed=index * 0.9),
            )

            final = _recording(events)
            assert final["phase"] == "capturing"
            assert final["capture_state"] == "idle"
            assert final["last_take_refused"] == POSE_UNSTABLE_REFUSAL
            assert final["takes_confirmed"] == 0
        finally:
            daemon.stop()


def test_pose_recording_reports_builtin_refusal_without_consuming_a_take() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(
                _command(
                    "start_recording",
                    gesture_name="My fist",
                    gesture_kind="pose",
                )
            )

            events = _run_pose_take(daemon, lambda index: _fist_observation())

            final = _recording(events)
            assert final["phase"] == "capturing"
            assert final["capture_state"] == "idle"
            assert final["last_take_refused"] == POSE_BUILTIN_REFUSAL
            assert final["takes_confirmed"] == 0
        finally:
            daemon.stop()
