"""Pipeline & daemon "test your gesture" mode.

After a custom gesture is recorded and saved, the UI enters a mandatory
test step before the user is allowed to map it (see GestureRecordingDialog's
"testing" step). This module covers the pipeline/daemon side of that:
gesture-test mode must recognize exactly like normal armed operation --
including while the engine itself is still paused, which is the real state
right after a recording finishes -- but must NEVER dispatch a real action.
Only a ``gesture_test`` event describing the attempt is emitted.
"""

from __future__ import annotations

from typing import Any

from aircontrol.config import ClutchConfig
from aircontrol.controller import ActionController
from aircontrol.ipc import ack_event
from aircontrol.metrics import Metrics
from aircontrol.pipeline import MIN_CUSTOM_CONFIDENCE, POSE_DWELL_SECONDS, Pipeline
from aircontrol.profile import save_profile
from aircontrol.store import Store

from tests.test_matcher_integration import (
    _configured_app,
    _feed_pipeline,
    _make_pipeline as _make_motion_pipeline,
    _profile,
    _scripted_observations,
    _seed_two_gestures,
    _strict_gate,
)
from tests.test_pose_pipeline import (
    _feed_held,
    _fist_observation,
    _hold_observation,
    _lenient_gate,
    _make_pipeline as _make_pose_pipeline,
    _seed_pose_gesture,
)
from tests.test_recording_ipc import (
    _command,
    _make_daemon,
    _record_good_takes,
    _recording,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _make_disarmed_pipeline(store: Store, *, gate: Any) -> tuple[Pipeline, _FakeClock]:
    """Build a pipeline whose engine starts (and stays) disarmed.

    Mirrors the real state right after a recording finishes: the daemon
    force-pauses the engine, and nothing re-arms it until the user performs
    the real arm gesture or toggles it manually.
    """
    config = _configured_app()
    config.clutch = ClutchConfig()  # default wake_pose: starts disarmed
    clock = _FakeClock()
    pipeline = Pipeline(
        config,
        ActionController(config.input.pointer_pixels_per_palm, practice=True),
        store=store,
        metrics=Metrics(clock=clock),
        gate=gate,
        profile=_profile(),
        clock=clock,
    )
    return pipeline, clock


def _attempts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event["type"] == "gesture_test" and event["state"] == "attempt"
    ]


# ---------------------------------------------------------------------------
# Motion gestures
# ---------------------------------------------------------------------------


def test_motion_gesture_test_fires_but_never_dispatches() -> None:
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_motion_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(ids["horizontal"], "motion")

        events = _feed_pipeline(pipeline, clock, "horizontal")

        assert pipeline.controller.sink.events == []
        assert not any(event["type"] == "action" for event in events)
        attempts = _attempts(events)
        assert attempts, "expected at least one gesture_test attempt"
        assert any(
            attempt["fired"]
            and attempt["is_target"]
            and attempt["matched_gesture_id"] == ids["horizontal"]
            and attempt["confidence"] >= MIN_CUSTOM_CONFIDENCE
            for attempt in attempts
        )


def test_motion_gesture_test_recognizes_while_engine_is_disarmed() -> None:
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_disarmed_pipeline(store, gate=_strict_gate())
        assert pipeline.engine.armed is False
        pipeline.start_gesture_test(ids["horizontal"], "motion")

        events = _feed_pipeline(pipeline, clock, "horizontal")

        assert pipeline.engine.armed is False, (
            "gesture-test mode must bypass the armed check without actually "
            "arming the engine"
        )
        assert pipeline.controller.sink.events == []
        attempts = _attempts(events)
        assert any(
            attempt["fired"] and attempt["is_target"] for attempt in attempts
        )


def test_motion_gesture_test_reports_a_different_match_as_not_the_target() -> None:
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_motion_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(ids["horizontal"], "motion")

        events = _feed_pipeline(pipeline, clock, "vertical")

        assert pipeline.controller.sink.events == []
        attempts = _attempts(events)
        assert attempts
        assert any(
            attempt["fired"]
            and not attempt["is_target"]
            and attempt["matched_gesture_id"] == ids["vertical"]
            for attempt in attempts
        )


def test_motion_gesture_test_reports_no_hand_state_and_throttles_it() -> None:
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_motion_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(ids["horizontal"], "motion")

        events: list[dict[str, Any]] = []
        for _ in range(5):
            clock.now += 0.05
            events.extend(pipeline.process(None, clock.now))

        no_hand_events = [
            event
            for event in events
            if event["type"] == "gesture_test" and event["state"] == "no_hand"
        ]
        assert len(no_hand_events) == 1, "the no_hand status must only broadcast once per change"


def test_motion_gesture_test_inactive_leaves_recognition_unchanged() -> None:
    with Store(":memory:") as store:
        _seed_two_gestures(store)
        pipeline, clock = _make_motion_pipeline(store, gate=_strict_gate())

        events = _feed_pipeline(pipeline, clock, "horizontal")

        assert not any(event["type"] == "gesture_test" for event in events)
        assert any(event["type"] == "action" for event in events)


def test_stopping_gesture_test_restores_normal_dispatch() -> None:
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_motion_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(ids["horizontal"], "motion")

        for now, observation in _scripted_observations("horizontal"):
            clock.now = now
            pipeline.process(observation, now)
        assert pipeline.controller.sink.events == []

        pipeline.stop_gesture_test()

        events: list[dict[str, Any]] = []
        base = 100.0
        for offset, observation in _scripted_observations("horizontal"):
            clock.now = base + offset
            events.extend(pipeline.process(observation, base + offset))

        assert any(event["type"] == "action" for event in events)
        assert pipeline.controller.sink.events != []


# ---------------------------------------------------------------------------
# Pose gestures
# ---------------------------------------------------------------------------


def test_pose_gesture_test_fires_but_never_dispatches() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(gesture_id, "pose")

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )

        assert pipeline.controller.sink.events == []
        assert not any(event["type"] == "action" for event in events)
        attempts = _attempts(events)
        assert attempts
        assert any(
            attempt["fired"]
            and attempt["is_target"]
            and attempt["matched_gesture_id"] == gesture_id
            for attempt in attempts
        )


def test_pose_gesture_test_recognizes_while_engine_is_disarmed() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_disarmed_pipeline(store, gate=_strict_gate())
        assert pipeline.engine.armed is False
        pipeline.start_gesture_test(gesture_id, "pose")

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )

        assert pipeline.engine.armed is False
        assert pipeline.controller.sink.events == []
        attempts = _attempts(events)
        assert any(
            attempt["fired"] and attempt["is_target"] for attempt in attempts
        )


def test_pose_gesture_test_reports_below_floor_for_a_different_shape() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_lenient_gate())
        pipeline.start_gesture_test(gesture_id, "pose")

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _fist_observation(), frame_count=frame_count
        )

        assert pipeline.controller.sink.events == []
        attempts = _attempts(events)
        assert attempts
        assert any(
            not attempt["fired"] and attempt["reason"] == "below_min_confidence"
            for attempt in attempts
        )


def test_pose_gesture_test_reports_moving_then_holding_states() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(gesture_id, "pose")

        events: list[dict[str, Any]] = []
        for index in range(10):
            clock.now += 0.05
            events.extend(
                pipeline.process(_hold_observation(dx=index * 0.05), clock.now)
            )
        assert any(
            event["type"] == "gesture_test" and event["state"] == "moving"
            for event in events
        )

        events.clear()
        for _ in range(3):
            clock.now += 0.05
            events.extend(pipeline.process(_hold_observation(), clock.now))
        assert any(
            event["type"] == "gesture_test" and event["state"] == "holding"
            for event in events
        )


def test_pose_gesture_test_allows_repeated_attempts_without_releasing() -> None:
    """Testing never latches: holding the same shape re-attempts every dwell
    period, so the two successful recognitions the UI requires to pass do
    not force the user to visibly release the pose between them."""
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(gesture_id, "pose")

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count * 3
        )

        fired_attempts = [
            attempt
            for attempt in _attempts(events)
            if attempt["fired"] and attempt["is_target"]
        ]
        assert len(fired_attempts) >= 2
        assert pipeline.controller.sink.events == []


def test_pose_gesture_test_inactive_leaves_recognition_unchanged() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_strict_gate())

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )

        assert not any(event["type"] == "gesture_test" for event in events)
        assert any(event["type"] == "action" for event in events)
        assert gesture_id is not None


# ---------------------------------------------------------------------------
# Daemon commands
# ---------------------------------------------------------------------------


def _save_motion_gesture(daemon: Any) -> int:
    daemon.command(_command("start_recording", gesture_name="Wave"))
    _record_good_takes(daemon)
    events = daemon.command(_command("finish_recording", id="finish-wave"))
    state = _recording(events)
    assert state["phase"] == "saved"
    gesture_id = state["outcome"]["gesture_id"]
    assert isinstance(gesture_id, int)
    # A freshly-saved gesture has no mapping yet (that only happens after
    # the mandatory test step passes); give it one here so that after
    # stop_gesture_test we can prove a real dispatch actually happens.
    daemon.command(
        _command(
            "set_mapping",
            id="map-wave",
            gesture_id=gesture_id,
            action={"kind": "switch_next"},
        )
    )
    return gesture_id


def test_start_and_stop_gesture_test_commands_set_and_clear_pipeline_mode() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            gesture_id = _save_motion_gesture(daemon)

            start_events = daemon.command(
                _command("start_gesture_test", id="start-test", gesture_id=gesture_id)
            )
            assert ack_event("start-test", True) in start_events
            assert daemon.pipeline.gesture_test_id == gesture_id
            assert daemon.pipeline.gesture_test_kind == "motion"
            assert daemon.preview_enabled is True

            test_events: list[dict[str, Any]] = []
            for offset, observation in _scripted_observations("horizontal"):
                test_events.extend(daemon.feed(observation, offset))

            assert daemon.pipeline.controller.sink.events == []
            assert not any(event["type"] == "action" for event in test_events)
            attempts = _attempts(test_events)
            assert attempts
            assert any(
                attempt["fired"] and attempt["is_target"] for attempt in attempts
            )

            stop_events = daemon.command(_command("stop_gesture_test", id="stop-test"))
            assert ack_event("stop-test", True) in stop_events
            assert daemon.pipeline.gesture_test_id is None

            resumed_events: list[dict[str, Any]] = []
            base = 1000.0
            for offset, observation in _scripted_observations("horizontal"):
                resumed_events.extend(daemon.feed(observation, base + offset))

            assert any(event["type"] == "action" for event in resumed_events)
            assert daemon.pipeline.controller.sink.events != []
        finally:
            daemon.stop()


def test_start_gesture_test_rejects_unknown_gesture_id() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            events = daemon.command(
                _command("start_gesture_test", id="bad-id", gesture_id=999)
            )
            assert ack_event("bad-id", False, "gesture not found") in events
            assert daemon.pipeline.gesture_test_id is None
        finally:
            daemon.stop()


def test_start_gesture_test_rejects_a_non_integer_gesture_id() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            events = daemon.command(
                _command("start_gesture_test", id="bad-type", gesture_id="oops")
            )
            assert ack_event(
                "bad-type", False, "gesture_id must be an integer"
            ) in events
            assert daemon.pipeline.gesture_test_id is None
        finally:
            daemon.stop()


def test_stop_gesture_test_is_a_harmless_noop_when_nothing_is_active() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            events = daemon.command(_command("stop_gesture_test", id="noop-stop"))
            assert ack_event("noop-stop", True) in events
            assert daemon.pipeline.gesture_test_id is None
        finally:
            daemon.stop()


def test_starting_a_new_recording_stops_a_leaked_gesture_test() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            gesture_id = _save_motion_gesture(daemon)
            daemon.command(
                _command("start_gesture_test", id="leak-start", gesture_id=gesture_id)
            )
            assert daemon.pipeline.gesture_test_id == gesture_id

            daemon.command(_command("start_recording", gesture_name="Second"))

            assert daemon.pipeline.gesture_test_id is None
        finally:
            daemon.stop()


def test_cancel_recording_stops_a_leaked_gesture_test() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        try:
            gesture_id = _save_motion_gesture(daemon)
            daemon.command(
                _command(
                    "start_gesture_test", id="leak-start-2", gesture_id=gesture_id
                )
            )
            assert daemon.pipeline.gesture_test_id == gesture_id

            daemon.command(_command("cancel_recording", id="cancel-1"))

            assert daemon.pipeline.gesture_test_id is None
        finally:
            daemon.stop()


def test_daemon_stop_clears_gesture_test_mode() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        daemon = _make_daemon(store)
        gesture_id = _save_motion_gesture(daemon)
        daemon.command(
            _command("start_gesture_test", id="leak-start-3", gesture_id=gesture_id)
        )
        assert daemon.pipeline.gesture_test_id == gesture_id

        daemon.stop()

        assert daemon.pipeline.gesture_test_id is None
