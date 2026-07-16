from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from aircontrol.config import AppConfig, GestureConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.domain import (
    ActionKind,
    GestureSample,
    HandObservation,
    Point2D,
    Point3D,
    Pose,
)
from aircontrol.engine import GestureEngine
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline
from aircontrol.settings import DEFAULTS
from aircontrol.store import Store


FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


def make_hand(
    extended: tuple[str, ...] = ("index",),
    *,
    pinch: bool = False,
    dx: float = 0.0,
    handedness: str = "Right",
    confidence: float = 0.99,
    input_is_mirrored: bool = True,
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
    if pinch:
        points[4] = Point3D(points[8].x + 0.01, points[8].y + 0.005)
    landmarks = tuple(Point3D(point.x + dx, point.y, point.z) for point in points)
    return HandObservation(
        landmarks=landmarks,
        handedness=handedness,
        confidence=confidence,
        image_width=1,
        image_height=1,
        input_is_mirrored=input_is_mirrored,
    )


def gesture_sample(
    pose: Pose,
    *,
    x: float = 0.5,
    pinch_ratio: float = 1.0,
) -> GestureSample:
    return GestureSample(
        pose=pose,
        pointer=Point2D(x, 0.5),
        center=Point2D(x, 0.5),
        palm_size=0.1,
        extended_fingers=(False, False, False, False),
        pinch_ratio=pinch_ratio,
    )


@pytest.fixture
def pipeline_factory() -> Callable[..., Pipeline]:
    pipelines: list[Pipeline] = []

    def make(*, click_mode: str = "two_hand", dominant_hand: str = "right") -> Pipeline:
        config = AppConfig.defaults()
        config.gestures.stability_seconds = 0.05
        config.gestures.max_observation_gap_seconds = 1.0
        config.gestures.pointer_smoothing = 1.0
        config.gestures.pointer_deadzone_palms = 0.001
        config.metrics.cpu_sampling = False
        controller = ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        )
        settings = dict(DEFAULTS)
        settings.update(
            click_mode=click_mode,
            dominant_hand=dominant_hand,
            smoothing=0,
        )
        pipeline = Pipeline(
            config,
            controller,
            store=None,
            metrics=Metrics(),
            gate=ConfidenceGate(GateThresholds()),
            settings=settings,
        )
        pipelines.append(pipeline)
        return pipeline

    yield make

    for pipeline in pipelines:
        pipeline.release()


def action_events(events: list[dict[str, Any]], kind: str | None = None) -> list[dict[str, Any]]:
    actions = [event for event in events if event["type"] == "action"]
    if kind is not None:
        actions = [event for event in actions if event["kind"] == kind]
    return actions


def sink_kinds(pipeline: Pipeline) -> list[str]:
    return [event.kind for event in pipeline.controller.sink.events]


def arm_and_stabilize_pointer(
    pipeline: Pipeline,
    *,
    pointer: HandObservation | None = None,
    click: HandObservation | None = None,
) -> tuple[HandObservation, HandObservation]:
    pointer = pointer or make_hand(handedness="Right", confidence=0.4)
    click = click or make_hand(handedness="Left", confidence=0.99)
    pipeline.toggle_arm(0.0)
    pipeline.process_hands((pointer, click), 1.0)
    pipeline.process_hands((pointer, click), 1.06)
    assert pipeline.engine.armed
    pipeline.controller.sink.events.clear()
    return pointer, click


def engage_click(
    pipeline: Pipeline,
    pointer: HandObservation,
    *,
    click_dx: float = 0.0,
) -> list[dict[str, Any]]:
    first = make_hand(
        pinch=True,
        handedness="Left",
        confidence=0.99,
        dx=click_dx,
    )
    second = make_hand(
        pinch=True,
        handedness="Left",
        confidence=0.99,
        dx=click_dx + 0.08,
    )
    events = pipeline.process_hands((pointer, first), 1.10)
    events.extend(pipeline.process_hands((pointer, second), 1.16))
    return events


def test_click_hand_pinch_dispatches_once_without_pointer_motion(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pointer, _click = arm_and_stabilize_pointer(pipeline)

    events = engage_click(pipeline, pointer, click_dx=-0.18)
    held_click = make_hand(
        pinch=True,
        handedness="Left",
        confidence=0.99,
        dx=0.18,
    )
    events.extend(pipeline.process_hands((pointer, held_click), 1.20))
    released_click = make_hand(
        handedness="Left",
        confidence=0.99,
        dx=0.24,
    )
    release_events = pipeline.process_hands((pointer, released_click), 1.21)
    release_events.extend(
        pipeline.process_hands((pointer, released_click), 1.26)
    )
    events.extend(release_events)

    assert len(action_events(events, "left_down")) == 1
    assert len(action_events(events, "left_up")) == 1
    assert all(event["category"] == "click" for event in action_events(events))
    assert not action_events(events, "move_pointer")
    assert sink_kinds(pipeline) == ["left_down", "left_up"]
    assert pipeline.last_sample == pipeline.recognizer.recognize(pointer)


def test_click_hand_hold_drags_with_pointer_motion(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pointer, _click = arm_and_stabilize_pointer(pipeline)

    events = engage_click(pipeline, pointer)
    moved_pointer = make_hand(
        handedness="Right",
        confidence=0.4,
        dx=0.06,
    )
    held_click = make_hand(pinch=True, handedness="Left", confidence=0.99)
    events.extend(pipeline.process_hands((moved_pointer, held_click), 1.20))
    released_click = make_hand(handedness="Left", confidence=0.99)
    events.extend(pipeline.process_hands((moved_pointer, released_click), 1.21))
    events.extend(pipeline.process_hands((moved_pointer, released_click), 1.26))

    assert len(action_events(events, "left_down")) == 1
    assert action_events(events, "move_pointer")
    assert len(action_events(events, "left_up")) == 1
    assert sink_kinds(pipeline) == ["left_down", "move_relative", "left_up"]


def test_single_frame_release_flicker_does_not_end_or_refire_click(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pointer, _click = arm_and_stabilize_pointer(pipeline)
    engage_click(pipeline, pointer)
    assert sink_kinds(pipeline) == ["left_down"]

    flicker = make_hand(handedness="Left", confidence=0.99)
    resumed_pinch = make_hand(
        pinch=True,
        handedness="Left",
        confidence=0.99,
    )
    events = pipeline.process_hands((pointer, flicker), 1.20)
    events.extend(pipeline.process_hands((pointer, resumed_pinch), 1.22))
    events.extend(pipeline.process_hands((pointer, resumed_pinch), 1.30))

    assert not action_events(events, "left_up")
    assert not action_events(events, "left_down")
    assert sink_kinds(pipeline) == ["left_down"]
    assert pipeline.controller.sink.left_is_down


def test_pointer_hand_pinch_moves_without_click_or_approach_freeze(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pointer, click = arm_and_stabilize_pointer(pipeline)
    pinching_pointer = make_hand(
        pinch=True,
        handedness="Right",
        confidence=0.4,
        dx=0.04,
    )

    events = pipeline.process_hands((pinching_pointer, click), 1.20)

    assert action_events(events, "move_pointer")
    assert not action_events(events, "left_down")
    assert sink_kinds(pipeline) == ["move_relative"]


def test_click_hand_is_ignored_while_pointer_engine_is_disarmed(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pointer = make_hand(handedness="Right", confidence=0.4)
    click = make_hand(pinch=True, handedness="Left", confidence=0.99)

    events = pipeline.process_hands((pointer, click), 0.0)
    events.extend(pipeline.process_hands((pointer, click), 0.10))

    assert not action_events(events, "left_down")
    assert not action_events(events, "left_up")
    assert pipeline.controller.sink.events == []


def test_lost_pointer_releases_held_two_hand_click(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pointer, _click = arm_and_stabilize_pointer(pipeline)
    engage_click(pipeline, pointer)
    assert pipeline.controller.sink.left_is_down

    remaining_click = make_hand(pinch=True, handedness="Left", confidence=0.99)
    lost_events = pipeline.process_hands((remaining_click,), 1.20)
    repeated_events = pipeline.process_hands((remaining_click,), 1.21)

    assert len(action_events(lost_events, "left_up")) == 1
    assert action_events(lost_events, "left_up")[0]["category"] == "click"
    assert not action_events(repeated_events, "left_up")
    assert not pipeline.controller.sink.left_is_down


def test_one_hand_in_two_hand_mode_cannot_click(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pipeline.toggle_arm(0.0)
    pinching_pointer = make_hand(
        pinch=True,
        handedness="Right",
        confidence=0.99,
    )

    events = pipeline.process_hands((pinching_pointer,), 1.0)
    events.extend(pipeline.process_hands((pinching_pointer,), 1.06))

    assert not action_events(events, "left_down")
    assert not pipeline.controller.sink.left_is_down


def test_force_pause_releases_held_two_hand_click(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    pointer, _click = arm_and_stabilize_pointer(pipeline)
    engage_click(pipeline, pointer)
    assert pipeline.controller.sink.left_is_down

    events = pipeline.force_pause("Paused for test")

    assert len(action_events(events, "left_up")) == 1
    assert not pipeline.controller.sink.left_is_down
    assert not pipeline.engine.armed


def test_single_mode_process_hands_matches_legacy_process(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    legacy = pipeline_factory(click_mode="single")
    multiple = pipeline_factory(click_mode="single")
    legacy.toggle_arm(0.0)
    multiple.toggle_arm(0.0)
    low_confidence = make_hand(handedness="Left", confidence=0.2, dx=-0.2)
    pinching_best = make_hand(
        pinch=True,
        handedness="Right",
        confidence=0.99,
    )
    released_best = make_hand(handedness="Right", confidence=0.99)

    expected: list[dict[str, Any]] = []
    actual: list[dict[str, Any]] = []
    for now, best in ((1.0, pinching_best), (1.06, pinching_best), (1.10, released_best)):
        expected.extend(legacy.process(best, now))
        actual.extend(multiple.process_hands((low_confidence, best), now))

    assert actual == expected
    assert multiple.last_sample == legacy.last_sample
    assert multiple.engine.status() == legacy.engine.status()
    assert multiple.controller.sink.events == legacy.controller.sink.events
    assert sink_kinds(multiple) == ["left_down", "left_up"]


@pytest.mark.parametrize(
    ("dominant_hand", "input_is_mirrored", "pointer_dx", "click_dx"),
    [
        ("right", True, 0.20, -0.20),
        ("right", False, -0.20, 0.20),
        ("left", True, -0.20, 0.20),
        ("left", False, 0.20, -0.20),
    ],
)
def test_same_handedness_uses_dominant_frame_side_tiebreak(
    pipeline_factory: Callable[..., Pipeline],
    dominant_hand: str,
    input_is_mirrored: bool,
    pointer_dx: float,
    click_dx: float,
) -> None:
    pipeline = pipeline_factory(dominant_hand=dominant_hand)
    pointer = make_hand(
        handedness=dominant_hand.upper(),
        confidence=0.3,
        dx=pointer_dx,
        input_is_mirrored=input_is_mirrored,
    )
    click = make_hand(
        handedness=dominant_hand,
        confidence=0.99,
        dx=click_dx,
        input_is_mirrored=input_is_mirrored,
    )
    arm_and_stabilize_pointer(pipeline, pointer=pointer, click=click)
    pinched_click = make_hand(
        pinch=True,
        handedness=dominant_hand,
        confidence=0.99,
        dx=click_dx,
        input_is_mirrored=input_is_mirrored,
    )

    events = pipeline.process_hands((pinched_click, pointer), 1.10)
    events.extend(pipeline.process_hands((pointer, pinched_click), 1.16))

    assert len(action_events(events, "left_down")) == 1
    assert pipeline.last_sample == pipeline.recognizer.recognize(pointer)


def test_engine_suppressed_pinch_is_pointer_motion_without_click() -> None:
    config = GestureConfig(
        stability_seconds=0.05,
        max_observation_gap_seconds=1.0,
        pointer_smoothing=1.0,
        pointer_deadzone_palms=0.001,
    )
    engine = GestureEngine(config, suppress_pinch_click=True)
    engine.manual_toggle(now=0.0)

    first = engine.update(
        gesture_sample(Pose.PINCH, x=0.5, pinch_ratio=0.3),
        1.0,
    )
    stable = engine.update(
        gesture_sample(Pose.PINCH, x=0.5, pinch_ratio=0.3),
        1.06,
    )
    moved = engine.update(
        gesture_sample(Pose.PINCH, x=0.51, pinch_ratio=0.3),
        1.10,
    )

    assert first == []
    assert stable == []
    assert [action.kind for action in moved] == [ActionKind.MOVE_POINTER]


@pytest.mark.parametrize(
    ("persisted_mode", "configured_max_hands", "expected_max_hands"),
    [("two_hand", 1, 2), ("single", 2, 1)],
)
def test_daemon_startup_applies_persisted_click_mode_to_tracker_config(
    persisted_mode: str,
    configured_max_hands: int,
    expected_max_hands: int,
) -> None:
    with Store(":memory:") as store:
        store.app_settings.set("click_mode", persisted_mode)
        config = AppConfig.defaults()
        config.tracking.max_hands = configured_max_hands
        daemon = Daemon(
            config,
            practice=True,
            controller=ActionController(
                config.input.pointer_pixels_per_palm,
                practice=True,
            ),
            store=store,
        )
        try:
            assert config.tracking.max_hands == expected_max_hands
        finally:
            daemon.stop()


def test_live_click_mode_change_restarts_active_tracker_only_when_needed() -> None:
    with Store(":memory:") as store:
        config = AppConfig.defaults()
        daemon = Daemon(
            config,
            practice=True,
            controller=ActionController(
                config.input.pointer_pixels_per_palm,
                practice=True,
            ),
            store=store,
        )
        try:
            daemon.command({"name": "set_camera", "enabled": True})
            assert daemon.take_camera_restart_request()
            daemon.set_camera_state("starting")
            daemon.set_camera_state("active")

            events = daemon.command(
                {
                    "name": "set_app_setting",
                    "id": "two-hand",
                    "key": "click_mode",
                    "value": "two_hand",
                }
            )

            assert events[0]["type"] == "ack" and events[0]["ok"] is True
            assert events[1]["type"] == "app_settings"
            assert events[1]["settings"]["click_mode"] == "two_hand"
            assert any(event["type"] == "camera" for event in events)
            assert config.tracking.max_hands == 2
            assert daemon.camera_state == "starting"
            assert daemon.take_camera_restart_request()

            daemon.set_camera_state("starting")
            daemon.set_camera_state("active")
            same_mode_events = daemon.command(
                {
                    "name": "set_app_setting",
                    "id": "same-mode",
                    "key": "click_mode",
                    "value": "two_hand",
                }
            )
            assert [event["type"] for event in same_mode_events] == [
                "ack",
                "app_settings",
            ]
            assert daemon.camera_state == "active"
            assert not daemon.take_camera_restart_request()

            daemon.command(
                {
                    "name": "set_app_setting",
                    "id": "single-hand",
                    "key": "click_mode",
                    "value": "single",
                }
            )
            assert config.tracking.max_hands == 1
            assert daemon.camera_state == "starting"
            assert daemon.take_camera_restart_request()
        finally:
            daemon.stop()


def test_live_mode_switch_releases_held_click_before_tracker_restart() -> None:
    with Store(":memory:") as store:
        store.app_settings.set("click_mode", "two_hand")
        config = AppConfig.defaults()
        config.gestures.stability_seconds = 0.05
        config.gestures.max_observation_gap_seconds = 1.0
        daemon = Daemon(
            config,
            practice=True,
            controller=ActionController(
                config.input.pointer_pixels_per_palm,
                practice=True,
            ),
            store=store,
        )
        try:
            daemon.command({"name": "set_camera", "enabled": True})
            assert daemon.take_camera_restart_request()
            daemon.set_camera_state("starting")
            daemon.set_camera_state("active")

            pointer, _click = arm_and_stabilize_pointer(daemon.pipeline)
            engage_click(daemon.pipeline, pointer)
            assert daemon.pipeline.controller.sink.left_is_down

            events = daemon.command(
                {
                    "name": "set_app_setting",
                    "key": "click_mode",
                    "value": "single",
                }
            )

            assert len(action_events(events, "left_up")) == 1
            assert not daemon.pipeline.controller.sink.left_is_down
            assert config.tracking.max_hands == 1
            assert daemon.camera_state == "starting"
            assert daemon.take_camera_restart_request()
        finally:
            daemon.stop()


def test_click_mode_change_while_camera_off_defers_worker_start() -> None:
    with Store(":memory:") as store:
        config = AppConfig.defaults()
        daemon = Daemon(
            config,
            practice=True,
            controller=ActionController(
                config.input.pointer_pixels_per_palm,
                practice=True,
            ),
            store=store,
        )
        try:
            events = daemon.command(
                {
                    "name": "set_app_setting",
                    "key": "click_mode",
                    "value": "two_hand",
                }
            )

            assert [event["type"] for event in events] == ["ack", "app_settings"]
            assert config.tracking.max_hands == 2
            assert daemon.camera_state == "off"
            assert not daemon.take_camera_restart_request()
        finally:
            daemon.stop()
