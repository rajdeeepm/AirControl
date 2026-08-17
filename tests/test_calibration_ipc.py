"""IPC round trip for the in-app guided calibration flow.

Mirrors tests/test_recording_ipc.py's structure: the daemon drives a
CalibrationRunner (tests/test_calibration.py covers the runner's own
maths) the same way it already drives a RecordingSession, so these tests
focus on the command/event contract, threading, mutual exclusion with
recording, and profile persistence -- not calibration geometry.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aircontrol.config import CalibrationConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.domain import HandObservation, Point3D
from aircontrol.ipc import (
    IpcProtocolError,
    ack_event,
    calibration_event,
    parse_command,
)
from aircontrol.profile import load_active_profile, save_profile
from aircontrol.store import Store
from tests.test_matcher_integration import _configured_app, _profile


_OPEN_PALM_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}
_OPEN_PALM = ("index", "middle", "ring", "pinky")


def _make_hand(
    extended: tuple[str, ...] = (),
    *,
    dx: float = 0.0,
    dy: float = 0.0,
) -> HandObservation:
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for name, (mcp, pip, dip, tip, x, y) in _OPEN_PALM_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        if name in extended:
            points[pip] = Point3D(x, y - 0.13)
            points[dip] = Point3D(x, y - 0.24)
            points[tip] = Point3D(x, y - 0.34)
        else:
            points[pip] = Point3D(x, y - 0.07)
            points[dip] = Point3D(x + 0.035, y - 0.01)
            points[tip] = Point3D(x + 0.018, y + 0.045)
    landmarks = tuple(Point3D(point.x + dx, point.y + dy, point.z) for point in points)
    return HandObservation(
        landmarks=landmarks,
        handedness="Left",
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


def _command(name: str, *, id: str = "calib-request", **fields: object) -> dict[str, Any]:
    return {"v": 1, "type": "command", "name": name, "id": id, **fields}


def _make_daemon(store: Store, *, config=None, ipc: object | None = None) -> Daemon:
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


def _small_calibration_config():
    config = _configured_app()
    config.calibration = CalibrationConfig(
        negative_seconds=0.5,
        snapshot_seconds=0.1,
        motion_reps=1,
    )
    return config


def _calibration(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(event for event in reversed(events) if event["type"] == "calibration")


class _CaptureTransport:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.callback = None

    def on_command(self, callback) -> None:
        self.callback = callback

    def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def send(self, command: dict[str, Any]) -> None:
        assert self.callback is not None
        self.callback(command)


@pytest.mark.parametrize(
    "payload",
    [
        _command("start_calibration"),
        _command("advance_calibration"),
        _command("cancel_calibration"),
        _command("get_calibration_state"),
        {"v": 1, "type": "command", "name": "advance_calibration"},
    ],
)
def test_parse_command_accepts_calibration_commands(payload: dict[str, Any]) -> None:
    assert parse_command(json.dumps(payload)) == payload


def test_calibration_event_has_exact_v1_shape_and_defaults() -> None:
    assert calibration_event(active=True, step="framing", instruction="Move around.", progress=0.5, recording=True) == {
        "v": 1,
        "type": "calibration",
        "active": True,
        "step": "framing",
        "instruction": "Move around.",
        "progress": 0.5,
        "recording": True,
        "complete": False,
        "error": None,
    }
    assert calibration_event(active=False) == {
        "v": 1,
        "type": "calibration",
        "active": False,
        "step": "",
        "instruction": "",
        "progress": 0.0,
        "recording": False,
        "complete": False,
        "error": None,
    }


def test_start_calibration_requires_store(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _configured_app()
    monkeypatch.setattr(Daemon, "_open_configured_store", lambda self: None)
    daemon = Daemon(
        config,
        practice=True,
        controller=ActionController(
            config.input.pointer_pixels_per_palm, practice=True
        ),
        store=None,
    )
    try:
        assert daemon.command(
            _command("start_calibration", id="no-store")
        ) == [ack_event("no-store", False, "no store")]
    finally:
        daemon.stop()


def test_start_calibration_begins_a_session_and_broadcasts_first_step() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            events = daemon.command(_command("start_calibration", id="start-1"))

            assert ack_event("start-1", True) in events
            step_event = _calibration(events)
            assert step_event["active"] is True
            assert step_event["step"] == "framing"
            assert step_event["complete"] is False
            assert daemon.is_calibrating
            assert daemon.preview_enabled
        finally:
            daemon.stop()


def test_feed_advances_progress_and_throttles_unchanged_broadcasts() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_calibration"))

            first = daemon.feed(_make_hand(_OPEN_PALM, dx=-0.1, dy=-0.1), 0.0)
            assert not any(event["type"] == "calibration" for event in first)

            second = daemon.feed(_make_hand(_OPEN_PALM, dx=0.1, dy=0.1), 0.1)
            progressed = _calibration(second)
            assert progressed["step"] == "framing"
            assert progressed["progress"] == pytest.approx(1.0)

            unchanged = daemon.feed(_make_hand(_OPEN_PALM, dx=0.1, dy=0.1), 0.2)
            assert not any(event["type"] == "calibration" for event in unchanged)
        finally:
            daemon.stop()


def test_advance_calibration_moves_to_the_next_step() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_calibration"))
            daemon.feed(_make_hand(_OPEN_PALM, dx=-0.1, dy=-0.1), 0.0)
            daemon.feed(_make_hand(_OPEN_PALM, dx=0.1, dy=0.1), 0.1)

            events = daemon.command(_command("advance_calibration", id="advance-1"))

            assert ack_event("advance-1", True) in events
            assert _calibration(events)["step"] == "hand_snapshot"
        finally:
            daemon.stop()


def test_full_walkthrough_persists_and_activates_a_profile() -> None:
    with Store(":memory:") as store:
        config = _small_calibration_config()
        daemon = _make_daemon(store, config=config)
        try:
            assert load_active_profile(store) is None

            start_events = daemon.command(_command("start_calibration", id="start"))
            assert _calibration(start_events)["step"] == "framing"

            # framing: two distinct hand centers, then advance.
            daemon.feed(_make_hand(_OPEN_PALM, dx=-0.1, dy=-0.1), 0.0)
            daemon.feed(_make_hand(_OPEN_PALM, dx=0.1, dy=0.1), 0.1)
            advanced = daemon.command(_command("advance_calibration", id="adv-framing"))
            assert _calibration(advanced)["step"] == "hand_snapshot"

            # hand_snapshot auto-completes once enough open-palm samples span
            # the configured snapshot window.
            daemon.feed(_make_hand(_OPEN_PALM), 1.0)
            daemon.feed(_make_hand(_OPEN_PALM), 1.11)
            assert daemon._calibration.current_step().name == "motion_signature"
            # A stray Continue press on an auto-advancing step is a harmless
            # no-op, exactly like the CLI's Space key today.
            noop = daemon.command(_command("advance_calibration", id="adv-noop"))
            assert _calibration(noop)["step"] == "motion_signature"

            # motion_signature: one deliberate rep (motion_reps=1), then advance.
            for now, dx in ((2.0, 0.0), (2.1, 0.02), (2.2, 0.04)):
                daemon.feed(_make_hand(_OPEN_PALM, dx=dx), now)
            advanced = daemon.command(_command("advance_calibration", id="adv-motion"))
            assert _calibration(advanced)["step"] == "negative_capture"

            # negative_capture auto-completes after the configured window.
            daemon.feed(_make_hand(("index",)), 3.0)
            daemon.feed(_make_hand(("index",), dx=0.001), 3.6)
            assert daemon._calibration.current_step().name == "lighting"

            # lighting: brightness + centers, then the final advance finishes
            # and persists the profile.
            daemon.feed(_make_hand(_OPEN_PALM), 4.0, 120.0)
            daemon.feed(_make_hand(_OPEN_PALM, dx=0.001), 4.1, 121.0)
            finished = daemon.command(_command("advance_calibration", id="finish"))

            assert ack_event("finish", True) in finished
            final_event = _calibration(finished)
            assert final_event["complete"] is True
            assert final_event["active"] is False
            assert not daemon.is_calibrating
            assert daemon._calibration is None

            profile = load_active_profile(store)
            assert profile is not None
        finally:
            daemon.stop()


def test_get_calibration_state_reconciles_after_reconnect() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            inactive = daemon.command(_command("get_calibration_state", id="q-1"))
            assert inactive == [calibration_event(active=False, id="q-1")]

            daemon.command(_command("start_calibration"))
            daemon.feed(_make_hand(_OPEN_PALM, dx=-0.1, dy=-0.1), 0.0)
            daemon.feed(_make_hand(_OPEN_PALM, dx=0.1, dy=0.1), 0.1)

            reconciled = daemon.command(_command("get_calibration_state", id="q-2"))
            assert reconciled == [
                calibration_event(
                    active=True,
                    step="framing",
                    instruction=(
                        "Move your hand around the interaction area, then press Space."
                    ),
                    progress=1.0,
                    recording=True,
                    id="q-2",
                )
            ]
        finally:
            daemon.stop()


def test_cancel_calibration_clears_state_and_restores_preview() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_calibration"))
            daemon.feed(_make_hand(_OPEN_PALM), 0.0)
            assert daemon.is_calibrating

            events = daemon.command(_command("cancel_calibration", id="cancel-1"))

            assert ack_event("cancel-1", True) in events
            cancelled = _calibration(events)
            assert cancelled["active"] is False
            assert cancelled["complete"] is False
            assert not daemon.is_calibrating
            assert daemon._calibration is None
            assert load_active_profile(store) is None
        finally:
            daemon.stop()


def test_calibration_and_recording_are_mutually_exclusive() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            daemon.command(_command("start_recording", gesture_name="Wave"))
            assert daemon.is_recording

            refused = daemon.command(_command("start_calibration", id="refused-1"))
            assert refused == [ack_event("refused-1", False, "recording already active")]
            assert not daemon.is_calibrating

            daemon.command(_command("cancel_recording"))
            assert not daemon.is_recording

            daemon.command(_command("start_calibration"))
            assert daemon.is_calibrating

            refused_recording = daemon.command(
                _command("start_recording", gesture_name="Wave", id="refused-2")
            )
            assert refused_recording == [
                ack_event("refused-2", False, "calibration already active")
            ]
            assert not daemon.is_recording
        finally:
            daemon.stop()


def test_live_ipc_advance_calibration_is_pumped_on_store_owner_thread() -> None:
    import threading

    with Store(":memory:") as store:
        config = _small_calibration_config()
        transport = _CaptureTransport()
        daemon = _make_daemon(store, config=config, ipc=transport)
        try:
            thread = threading.Thread(
                target=transport.send, args=(_command("start_calibration", id="threaded-start"),)
            )
            thread.start()
            thread.join()

            assert transport.events == []
            assert not daemon.is_calibrating

            daemon.process_pending_commands()

            assert ack_event("threaded-start", True) in transport.events
            assert daemon.is_calibrating
        finally:
            daemon.stop()


def test_calibration_state_is_cleared_on_daemon_stop() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        daemon.command(_command("start_calibration"))
        daemon.feed(_make_hand(_OPEN_PALM), 0.0)
        assert daemon.is_calibrating

        daemon.stop()

        assert daemon._calibration is None
        assert daemon._calibration_last_step_name is None


def test_start_calibration_rejects_unsupported_command_id_type() -> None:
    with pytest.raises(IpcProtocolError):
        parse_command(json.dumps({"v": 1, "type": "command", "name": "start_calibration", "id": 5}))
