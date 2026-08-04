"""Pipeline & daemon "test your gesture" mode.

The mandatory test step now runs AFTER the user has mapped their gesture to
an action (see GestureTestDialog), and exercises the REAL end-to-end path:
while a gesture test is active, a matching gesture still calls
``_mapped_action``/``_dispatch_matched`` exactly as normal live use does, so
the user's mapped action really fires and they can see it happen. A
``gesture_test`` event is emitted ALONGSIDE (not instead of) the normal
candidate/action events, describing the attempt for the UI (matched
gesture, confidence, runner-up, whether it fired, whether it was the target,
the failure reason, and -- when it fired -- the dispatched action's
description).

Because recording force-pauses the engine, and the daemon's own defensive
stop calls must never leave a forgotten test permanently armed, the pipeline
genuinely arms the engine for the duration of a test (via the same
clutch.set_armed the arm button uses) and restores whatever armed/paused
state preceded it when the test stops.
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
    """Build a pipeline whose engine starts (and stays, until told otherwise) disarmed.

    Mirrors the real state right after a recording finishes: the daemon
    force-pauses the engine, and nothing re-arms it until the user performs
    the real arm gesture, toggles it manually, or a gesture test genuinely
    arms it for its duration.
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


def test_motion_gesture_test_dispatches_the_real_action() -> None:
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_motion_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(ids["horizontal"], "motion")

        events = _feed_pipeline(pipeline, clock, "horizontal")

        assert pipeline.controller.sink.events != [], (
            "a gesture test must exercise the real dispatch path, not a "
            "suppressed one"
        )
        assert any(event["type"] == "action" for event in events)
        attempts = _attempts(events)
        assert attempts, "expected at least one gesture_test attempt"
        fired_target = [
            attempt
            for attempt in attempts
            if attempt["fired"]
            and attempt["is_target"]
            and attempt["matched_gesture_id"] == ids["horizontal"]
            and attempt["confidence"] >= MIN_CUSTOM_CONFIDENCE
        ]
        assert fired_target
        assert fired_target[0]["action_description"] == "NEXT APP"


def test_motion_gesture_test_arms_a_disarmed_engine_and_dispatches() -> None:
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_disarmed_pipeline(store, gate=_strict_gate())
        assert pipeline.engine.armed is False
        pipeline.start_gesture_test(ids["horizontal"], "motion")
        assert pipeline.engine.armed is True, (
            "starting a test must genuinely arm the engine, covering the "
            "common case where recording just force-paused it"
        )

        events = _feed_pipeline(pipeline, clock, "horizontal")

        assert pipeline.controller.sink.events != []
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

        # The matched gesture ("vertical") is mapped too, so it really
        # dispatches -- just not as the gesture under test.
        assert pipeline.controller.sink.events != []
        attempts = _attempts(events)
        assert attempts
        matched_other = [
            attempt
            for attempt in attempts
            if attempt["fired"]
            and not attempt["is_target"]
            and attempt["matched_gesture_id"] == ids["vertical"]
        ]
        assert matched_other
        assert matched_other[0]["action_description"] is not None


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


def test_stopping_gesture_test_restores_prior_disarmed_state() -> None:
    """Leak guard: a test that ends must not leave the system armed."""
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_disarmed_pipeline(store, gate=_strict_gate())
        assert pipeline.engine.armed is False
        pipeline.start_gesture_test(ids["horizontal"], "motion")
        assert pipeline.engine.armed is True

        events = _feed_pipeline(pipeline, clock, "horizontal")
        assert pipeline.controller.sink.events != []
        assert any(
            attempt["fired"] and attempt["is_target"]
            for attempt in _attempts(events)
        )

        pipeline.stop_gesture_test()

        assert pipeline.engine.armed is False, (
            "stopping the test must restore the prior (disarmed) state"
        )

        # Disarmed and not testing: recognition stays inert, exactly like
        # real disarmed operation.
        pipeline.controller.sink.events.clear()
        more_events: list[dict[str, Any]] = []
        base = 100.0
        for offset, observation in _scripted_observations("horizontal"):
            clock.now = base + offset
            more_events.extend(pipeline.process(observation, base + offset))

        assert pipeline.controller.sink.events == []
        assert not any(event["type"] == "gesture_test" for event in more_events)


def test_stopping_gesture_test_preserves_prior_armed_state() -> None:
    """If the engine was already armed before the test, stopping keeps it armed."""
    with Store(":memory:") as store:
        ids = _seed_two_gestures(store)
        pipeline, clock = _make_disarmed_pipeline(store, gate=_strict_gate())
        pipeline.toggle_arm(clock.now)
        assert pipeline.engine.armed is True

        pipeline.start_gesture_test(ids["horizontal"], "motion")
        assert pipeline.engine.armed is True

        pipeline.stop_gesture_test()

        assert pipeline.engine.armed is True, (
            "stopping the test must restore whatever armed state preceded it, "
            "not force it back to disarmed"
        )


# ---------------------------------------------------------------------------
# Pose gestures
# ---------------------------------------------------------------------------


def test_pose_gesture_test_dispatches_the_real_action() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(gesture_id, "pose")

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )

        assert pipeline.controller.sink.events != []
        assert any(event["type"] == "action" for event in events)
        attempts = _attempts(events)
        assert attempts
        fired_target = [
            attempt
            for attempt in attempts
            if attempt["fired"]
            and attempt["is_target"]
            and attempt["matched_gesture_id"] == gesture_id
        ]
        assert fired_target
        assert fired_target[0]["action_description"] == "NEXT APP"


def test_pose_gesture_test_arms_a_disarmed_engine_and_dispatches() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_disarmed_pipeline(store, gate=_strict_gate())
        assert pipeline.engine.armed is False
        pipeline.start_gesture_test(gesture_id, "pose")
        assert pipeline.engine.armed is True

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )

        assert pipeline.controller.sink.events != []
        attempts = _attempts(events)
        assert any(
            attempt["fired"] and attempt["is_target"] for attempt in attempts
        )

        pipeline.stop_gesture_test()
        assert pipeline.engine.armed is False


def test_pose_gesture_test_reports_below_floor_for_a_different_shape() -> None:
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_lenient_gate())
        pipeline.start_gesture_test(gesture_id, "pose")

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _fist_observation(), frame_count=frame_count
        )

        assert pipeline.controller.sink.events == [], (
            "a below-floor match must never dispatch"
        )
        attempts = _attempts(events)
        assert attempts
        below_floor = [
            attempt
            for attempt in attempts
            if not attempt["fired"] and attempt["reason"] == "below_min_confidence"
        ]
        assert below_floor
        assert below_floor[0]["action_description"] is None


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


def test_pose_gesture_test_latches_after_firing_then_can_refire_after_release() -> None:
    """A gesture test behaves like real use: fire once per hold, then the
    user must visibly release the pose (or move the hand) before it can fire
    again -- the old "never latch while testing" special case is gone."""
    with Store(":memory:") as store:
        gesture_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pose_pipeline(store, gate=_strict_gate())
        pipeline.start_gesture_test(gesture_id, "pose")

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )
        fired_once = [
            attempt
            for attempt in _attempts(events)
            if attempt["fired"] and attempt["is_target"]
        ]
        assert len(fired_once) == 1
        assert len(pipeline.controller.sink.events) == 1

        # Continuing to hold the exact same shape must not re-fire.
        more_events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )
        assert not any(
            attempt["fired"] and attempt["is_target"]
            for attempt in _attempts(more_events)
        )
        assert len(pipeline.controller.sink.events) == 1

        # Move the hand (breaks stillness and releases the latch), then hold
        # the same shape again: it must be able to fire a second time.
        clock.now += 0.05
        pipeline.process(_hold_observation(dx=0.3), clock.now)
        events_after_release = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )
        fired_again = [
            attempt
            for attempt in _attempts(events_after_release)
            if attempt["fired"] and attempt["is_target"]
        ]
        assert len(fired_again) == 1
        assert len(pipeline.controller.sink.events) == 2


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
    # A freshly-saved gesture has no mapping yet (that only happens once the
    # user maps it, which is what triggers the mandatory test step); give it
    # one here so a real dispatch can be proven during the test.
    daemon.command(
        _command(
            "set_mapping",
            id="map-wave",
            gesture_id=gesture_id,
            action={"kind": "switch_next"},
        )
    )
    return gesture_id


def test_start_and_stop_gesture_test_commands_dispatch_the_real_action() -> None:
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

            assert daemon.pipeline.controller.sink.events != [], (
                "the test must dispatch the gesture's mapped action for real"
            )
            assert any(event["type"] == "action" for event in test_events)
            attempts = _attempts(test_events)
            assert attempts
            fired_target = [
                attempt
                for attempt in attempts
                if attempt["fired"] and attempt["is_target"]
            ]
            assert fired_target
            assert fired_target[0]["action_description"] == "NEXT APP"

            stop_events = daemon.command(_command("stop_gesture_test", id="stop-test"))
            assert ack_event("stop-test", True) in stop_events
            assert daemon.pipeline.gesture_test_id is None

            resumed_events: list[dict[str, Any]] = []
            base = 1000.0
            for offset, observation in _scripted_observations("horizontal"):
                resumed_events.extend(daemon.feed(observation, base + offset))

            assert any(event["type"] == "action" for event in resumed_events)
        finally:
            daemon.stop()


def test_gesture_test_arms_engine_left_paused_by_recording_and_restores_on_stop() -> None:
    """End-to-end leak-guard coverage at the daemon level, with the default
    wake-pose clutch (unlike the always-on clutch the other daemon tests use)
    so "recording left the engine paused" and "stopping restores it" are both
    real, observable armed-state transitions rather than a no-op."""
    with Store(":memory:") as store:
        save_profile(store, _profile())
        config = _configured_app()
        config.clutch = ClutchConfig()  # default wake_pose: starts disarmed
        daemon = _make_daemon(store, config=config)
        try:
            gesture_id = _save_motion_gesture(daemon)
            assert daemon.pipeline.engine.armed is False, (
                "finishing a recording force-pauses the engine"
            )

            daemon.command(
                _command("start_gesture_test", id="arm-start", gesture_id=gesture_id)
            )
            assert daemon.pipeline.engine.armed is True

            test_events: list[dict[str, Any]] = []
            for offset, observation in _scripted_observations("horizontal"):
                test_events.extend(daemon.feed(observation, offset))

            assert daemon.pipeline.engine.armed is True, (
                "the engine must stay armed for the whole test"
            )
            assert daemon.pipeline.controller.sink.events != []
            assert any(
                attempt["fired"] and attempt["is_target"]
                for attempt in _attempts(test_events)
            )

            daemon.command(_command("stop_gesture_test", id="arm-stop"))

            assert daemon.pipeline.engine.armed is False, (
                "stopping the test must restore the prior disarmed state"
            )
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
