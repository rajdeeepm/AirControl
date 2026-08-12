"""Live static-pose gesture recognition: dwell, latch, and release."""

from __future__ import annotations

import pytest

from aircontrol.config import AppConfig, ClutchConfig
from aircontrol.controller import ActionController
from aircontrol.domain import HandObservation, Point3D
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.metrics import Metrics
from aircontrol.pipeline import MIN_CUSTOM_CONFIDENCE, POSE_DWELL_SECONDS, Pipeline
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
)
from aircontrol.settings import DEFAULTS
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory


_FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _configured_app() -> AppConfig:
    config = AppConfig.defaults()
    config.clutch = ClutchConfig(mode="always_on", acknowledged_expert_mode=True)
    config.gestures.stability_seconds = 100.0
    config.gestures.max_observation_gap_seconds = 1.0
    config.metrics.cpu_sampling = False
    return config


def _profile() -> CalibrationProfile:
    return CalibrationProfile(
        hand_size=1.0,
        volume=InteractionVolume(-2.0, 2.0, -2.0, 2.0),
        motion=MotionSignature(velocity_floor=0.15, velocity_ceiling=5.0),
        lighting=LightingProfile(120.0, 0.01, True),
        incidental_features=(),
        created_at=1.0,
    )


def _strict_gate() -> ConfidenceGate:
    return ConfidenceGate(
        GateThresholds(t1_top1=0.9, t2_margin=0.1, t3_incidental=0.0)
    )


def _lenient_gate() -> ConfidenceGate:
    """A gate that would fire on anything -- isolates MIN_CUSTOM_CONFIDENCE
    as the only thing standing between a poor match and firing."""
    return ConfidenceGate(
        GateThresholds(t1_top1=0.0, t2_margin=0.0, t3_incidental=0.0)
    )


def _hold_landmarks(*, dx: float = 0.0) -> tuple[Point3D, ...]:
    """A relaxed, uniformly half-curled hand -- distinct from any built-in pose."""
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for _name, (mcp, pip, dip, tip, x, y) in _FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.08)
        points[dip] = Point3D(x + 0.05, y - 0.13)
        points[tip] = Point3D(x + 0.02, y - 0.20)
    return tuple(Point3D(point.x + dx, point.y, point.z) for point in points)


def _hold_observation(*, dx: float = 0.0, handedness: str = "Right") -> HandObservation:
    return HandObservation(
        landmarks=_hold_landmarks(dx=dx),
        handedness=handedness,
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


def _fist_observation(handedness: str = "Right") -> HandObservation:
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for _name, (mcp, pip, dip, tip, x, y) in _FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.07)
        points[dip] = Point3D(x + 0.035, y - 0.01)
        points[tip] = Point3D(x + 0.018, y + 0.045)
    return HandObservation(
        landmarks=tuple(points),
        handedness=handedness,
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


def _pose_trajectory(*, dx: float = 0.0, frame_count: int = 6) -> Trajectory:
    frames = tuple(
        LandmarkFrame(
            landmarks=_hold_landmarks(dx=dx),
            handedness="Right",
            timestamp=index * 0.05,
        )
        for index in range(frame_count)
    )
    return Trajectory(frames=frames, handedness="Right")


def _seed_pose_gesture(store: Store, action: dict[str, object]) -> int:
    gesture = store.gestures.add("Peace sign", kind="pose")
    for index in range(3):
        store.exemplars.add(gesture.id, _pose_trajectory(dx=index * 0.002))
    store.mappings.set(gesture.id, action)
    return gesture.id


def _make_pipeline(store: Store, *, gate: ConfidenceGate) -> tuple[Pipeline, FakeClock]:
    config = _configured_app()
    clock = FakeClock()
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


def _feed_held(
    pipeline: Pipeline,
    clock: FakeClock,
    observation: HandObservation | None,
    *,
    frame_count: int,
    dt: float = 0.05,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index in range(frame_count):
        clock.now += dt
        events.extend(pipeline.process(observation, clock.now))
    return events


def test_held_pose_dispatches_once_then_latches_until_released() -> None:
    with Store(":memory:") as store:
        _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())
        observation = _hold_observation()

        # Enough still frames to clear the dwell threshold at least once.
        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(pipeline, clock, observation, frame_count=frame_count)
        actions = [event for event in events if event["type"] == "action"]
        assert [event["kind"] for event in actions] == ["switch_next"]

        # Continuing to hold the exact same shape must not re-fire.
        more_events = _feed_held(pipeline, clock, observation, frame_count=frame_count)
        assert not any(event["type"] == "action" for event in more_events)

        # Move the hand (breaks stillness) then hold the same shape again:
        # the pose must be able to fire a second time after release.
        clock.now += 0.05
        pipeline.process(_hold_observation(dx=0.3), clock.now)
        events_after_release = _feed_held(
            pipeline, clock, observation, frame_count=frame_count
        )
        actions_after_release = [
            event for event in events_after_release if event["type"] == "action"
        ]
        assert [event["kind"] for event in actions_after_release] == ["switch_next"]


def test_pose_never_fires_while_hand_is_not_still() -> None:
    with Store(":memory:") as store:
        _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        events: list[dict[str, object]] = []
        for index in range(30):
            clock.now += 0.05
            # A steadily translating hand never counts as "still" long enough
            # to accumulate dwell.
            events.extend(
                pipeline.process(_hold_observation(dx=index * 0.05), clock.now)
            )

        assert not any(event["type"] == "action" for event in events)


def test_pose_and_motion_gestures_never_cross_fire() -> None:
    with Store(":memory:") as store:
        pose_id = _seed_pose_gesture(store, {"kind": "switch_next"})
        motion = store.gestures.add("Wave", kind="motion")
        store.exemplars.add(
            motion.id,
            Trajectory(
                frames=tuple(
                    LandmarkFrame(
                        landmarks=_hold_landmarks(dx=index * 0.05),
                        handedness="Right",
                        timestamp=index * 0.05,
                    )
                    for index in range(20)
                ),
                handedness="Right",
            ),
        )
        store.mappings.set(motion.id, {"kind": "scroll", "amount": 1})
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        # Holding the pose steady must never dispatch the motion mapping.
        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        held_events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )
        assert [
            event["kind"] for event in held_events if event["type"] == "action"
        ] == ["switch_next"]
        assert pose_id is not None


def test_pose_never_fires_when_not_armed() -> None:
    with Store(":memory:") as store:
        _seed_pose_gesture(store, {"kind": "switch_next"})
        config = _configured_app()
        # Default wake_pose clutch: armed only after the user performs the
        # open-palm arm gesture, which this test never does.
        config.clutch = ClutchConfig()
        clock = FakeClock()
        pipeline = Pipeline(
            config,
            ActionController(config.input.pointer_pixels_per_palm, practice=True),
            store=store,
            metrics=Metrics(clock=clock),
            gate=_strict_gate(),
            profile=_profile(),
            clock=clock,
        )
        assert pipeline.engine.armed is False

        events = _feed_held(
            pipeline,
            clock,
            _hold_observation(),
            frame_count=int(POSE_DWELL_SECONDS / 0.05) + 6,
        )

        assert not any(event["type"] == "action" for event in events)


@pytest.mark.parametrize("click_mode", ["single", "two_hand"])
def test_pose_fires_in_both_click_modes(click_mode: str) -> None:
    with Store(":memory:") as store:
        _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())
        settings = dict(DEFAULTS)
        settings["click_mode"] = click_mode
        pipeline.apply_settings(settings)

        events: list[dict[str, object]] = []
        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        for _ in range(frame_count):
            clock.now += 0.05
            events.extend(pipeline.process_hands((_hold_observation(),), clock.now))

        actions = [event for event in events if event["type"] == "action"]
        assert [event["kind"] for event in actions] == ["switch_next"]


def test_correct_pose_fires_and_clears_the_min_confidence_floor() -> None:
    """A held pose that genuinely matches its exemplar must both fire and
    report a confidence at or above MIN_CUSTOM_CONFIDENCE -- not just clear
    whatever the sensitivity slider's gate happens to require."""
    with Store(":memory:") as store:
        _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _hold_observation(), frame_count=frame_count
        )

        candidates = [event for event in events if event["type"] == "candidate"]
        fired = [c for c in candidates if c["gate"] == "fire"]
        assert fired, "expected the correct pose to fire"
        assert fired[0]["confidence"] >= MIN_CUSTOM_CONFIDENCE
        assert [event["kind"] for event in events if event["type"] == "action"] == [
            "switch_next"
        ]


def test_different_shape_is_blocked_solely_by_the_min_confidence_floor() -> None:
    """Even with a gate lenient enough to fire on anything, a held shape
    that does not genuinely match its exemplar (top1 < MIN_CUSTOM_CONFIDENCE)
    must abstain, and the abstain reason must name the floor -- not the gate
    -- as the cause, since the gate itself would have allowed it through."""
    with Store(":memory:") as store:
        _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pipeline(store, gate=_lenient_gate())

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        events = _feed_held(
            pipeline, clock, _fist_observation(), frame_count=frame_count
        )

        candidates = [event for event in events if event["type"] == "candidate"]
        assert candidates, "expected at least one candidate event"
        abstains = [c for c in candidates if c["gate"] == "abstain"]
        assert abstains
        assert any(c["reason"] == "below_min_confidence" for c in abstains)
        assert not any(event["type"] == "action" for event in events)


def test_status_event_stays_last_during_pose_dwell() -> None:
    with Store(":memory:") as store:
        _seed_pose_gesture(store, {"kind": "switch_next"})
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        frame_count = int(POSE_DWELL_SECONDS / 0.05) + 6
        for _ in range(frame_count):
            clock.now += 0.05
            frame_events = pipeline.process(_hold_observation(), clock.now)
            assert frame_events[-1]["type"] == "status"
            assert sum(
                1 for event in frame_events if event["type"] == "candidate"
            ) <= 1
