from __future__ import annotations

import math

import pytest

from aircontrol.config import AppConfig, ClutchConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.density import IncidentalDensity
from aircontrol.domain import Action, ActionKind, HandObservation, Point3D
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
    save_profile,
)
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory
from aircontrol.undo import UndoManager


FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


def make_hand(
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
    for name, (mcp, pip, dip, tip, x, y) in FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        if name in extended:
            points[pip] = Point3D(x, y - 0.13)
            points[dip] = Point3D(x, y - 0.24)
            points[tip] = Point3D(x, y - 0.34)
        else:
            points[pip] = Point3D(x, y - 0.07)
            points[dip] = Point3D(x + 0.035, y - 0.01)
            points[tip] = Point3D(x + 0.018, y + 0.045)
    landmarks = tuple(
        Point3D(point.x + dx, point.y + dy, point.z) for point in points
    )
    return HandObservation(
        landmarks=landmarks,
        handedness="Left",
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


def incidental_trajectory(amplitude: float) -> Trajectory:
    frames: list[LandmarkFrame] = []
    anchors = frozenset((0, 5, 9, 17))
    for frame_index in range(12):
        observation = make_hand(("index",))
        offset = amplitude * math.sin(frame_index * 1.2)
        landmarks = tuple(
            point
            if index in anchors
            else Point3D(point.x + offset, point.y, point.z)
            for index, point in enumerate(observation.landmarks)
        )
        frames.append(LandmarkFrame(landmarks, "Left", frame_index * 0.1))
    return Trajectory(tuple(frames), "Left")


def fitted_profile() -> CalibrationProfile:
    density = IncidentalDensity.fit(
        (incidental_trajectory(0.005), incidental_trajectory(0.008))
    )
    return CalibrationProfile(
        hand_size=0.22,
        volume=InteractionVolume(0.0, 1.0, 0.0, 1.0),
        motion=MotionSignature(velocity_floor=0.5, velocity_ceiling=5.0),
        lighting=LightingProfile(120.0, 0.01, True),
        incidental_features=density.to_rows(),
        created_at=1.0,
    )


def configured_app() -> AppConfig:
    config = AppConfig.defaults()
    config.clutch = ClutchConfig(hold_seconds=0.1, window_seconds=10.0)
    config.gestures.stability_seconds = 0.05
    config.gestures.swipe_threshold_palms = 0.3
    config.gestures.max_observation_gap_seconds = 1.0
    config.metrics.cpu_sampling = False
    return config


def make_pipeline(profile: CalibrationProfile) -> Pipeline:
    config = configured_app()
    return Pipeline(
        config,
        ActionController(config.input.pointer_pixels_per_palm, practice=True),
        store=None,
        metrics=Metrics(),
        gate=ConfidenceGate(GateThresholds()),
        profile=profile,
    )


def test_unclutched_desk_work_dispatches_nothing_and_fires_no_candidates() -> None:
    pipeline = make_pipeline(fitted_profile())
    events: list[dict] = []

    for index in range(30):
        pose = ("index",) if index % 2 == 0 else ("index", "middle")
        events.extend(
            pipeline.process(
                make_hand(pose, dx=0.002 * math.sin(index)),
                index * 0.05,
            )
        )

    assert not pipeline.engine.armed
    assert pipeline.controller.sink.events == []
    assert not any(event["type"] == "action" for event in events)
    assert not any(
        event["type"] == "candidate" and event["gate"] == "fire"
        for event in events
    )
    assert pipeline.metrics.snapshot().candidates_per_hour == 0.0


def test_wake_pose_then_deliberate_swipe_emits_segment_and_heuristic_action() -> None:
    pipeline = make_pipeline(fitted_profile())
    open_palm = ("index", "middle", "ring", "pinky")
    swipe_pose = ("index", "middle", "ring")
    events: list[dict] = []

    events.extend(pipeline.process(make_hand(open_palm), 0.0))
    events.extend(pipeline.process(make_hand(open_palm), 0.11))
    for now in (0.16, 0.21, 0.26, 0.31):
        events.extend(pipeline.process(make_hand(open_palm), now))
    events.extend(pipeline.process(make_hand(swipe_pose), 0.36))
    events.extend(pipeline.process(make_hand(swipe_pose), 0.42))
    for now, dx in ((0.47, -0.04), (0.52, -0.08), (0.57, -0.12)):
        events.extend(pipeline.process(make_hand(swipe_pose, dx=dx), now))

    offset_events: list[dict] = []
    for now in (0.62, 0.67, 0.72, 0.77):
        offset_events.extend(pipeline.process(make_hand(swipe_pose, dx=-0.12), now))
    events.extend(offset_events)

    action_events = [event for event in events if event["type"] == "action"]
    assert pipeline.engine.armed
    assert any(event["kind"] == "switch_next" for event in action_events)
    assert any(event["type"] == "candidate" for event in offset_events)
    assert pipeline.metrics.snapshot().candidates_per_hour > 0.0
    assert len(action_events) == 1


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_daemon(store: Store) -> tuple[Daemon, FakeClock]:
    config = configured_app()
    controller = ActionController(config.input.pointer_pixels_per_palm, practice=True)
    daemon = Daemon(
        config,
        practice=True,
        controller=controller,
        store=store,
    )
    clock = FakeClock()
    daemon.pipeline._clock = clock
    daemon.pipeline._undo = UndoManager(daemon.metrics, clock=clock)
    return daemon, clock


def test_daemon_undo_within_window_dispatches_inverse_and_logs_fp() -> None:
    with Store(":memory:") as store:
        save_profile(store, fitted_profile())
        daemon, clock = make_daemon(store)
        try:
            fired = daemon.pipeline._gated_action_events(
                [Action(ActionKind.SWITCH_NEXT)],
                now=clock(),
            )
            assert any(event.get("kind") == "switch_next" for event in fired)
            daemon.pipeline.controller.sink.events.clear()

            clock.now = 2.0
            events = daemon.command("undo")

            assert [event["kind"] for event in events] == ["switch_previous"]
            assert daemon.pipeline.controller.sink.events
            assert daemon.metrics.snapshot().fp_per_hour > 0.0
        finally:
            daemon.stop()


def test_daemon_undo_after_window_dispatches_nothing() -> None:
    with Store(":memory:") as store:
        save_profile(store, fitted_profile())
        daemon, clock = make_daemon(store)
        try:
            daemon.pipeline._gated_action_events(
                [Action(ActionKind.SWITCH_NEXT)],
                now=clock(),
            )
            daemon.pipeline.controller.sink.events.clear()

            clock.now = 3.01

            assert daemon.command("undo") == []
            assert daemon.pipeline.controller.sink.events == []
            assert daemon.metrics.snapshot().fp_per_hour == 0.0
        finally:
            daemon.stop()
