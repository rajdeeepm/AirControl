from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.domain import (
    GestureSample,
    HandObservation,
    Point2D,
    Point3D,
    Pose,
)
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
)
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


def make_fist(
    *,
    handedness: str = "Left",
    confidence: float = 0.99,
    dx: float = 0.0,
    input_is_mirrored: bool = True,
) -> HandObservation:
    return make_hand(
        extended=(),
        handedness=handedness,
        confidence=confidence,
        dx=dx,
        input_is_mirrored=input_is_mirrored,
    )


def make_open_hand(
    *,
    handedness: str = "Left",
    confidence: float = 0.99,
    dx: float = 0.0,
    input_is_mirrored: bool = True,
) -> HandObservation:
    return make_hand(
        extended=tuple(FINGER_LAYOUT),
        handedness=handedness,
        confidence=confidence,
        dx=dx,
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

    def make(
        *,
        click_mode: str = "two_hand",
        dominant_hand: str = "right",
        profile: CalibrationProfile | None = None,
    ) -> Pipeline:
        config = AppConfig.defaults()
        config.gestures.stability_seconds = 0.05
        config.gestures.max_observation_gap_seconds = 1.0
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
            profile=profile,
        )
        pipelines.append(pipeline)
        return pipeline

    yield make

    for pipeline in pipelines:
        pipeline.release()


def action_events(
    events: list[dict[str, Any]],
    kind: str | None = None,
) -> list[dict[str, Any]]:
    actions = [event for event in events if event["type"] == "action"]
    if kind is not None:
        actions = [event for event in actions if event["kind"] == kind]
    return actions


def sink_kinds(pipeline: Pipeline) -> list[str]:
    return [event.kind for event in pipeline.controller.sink.events]


def calibration_profile() -> CalibrationProfile:
    return CalibrationProfile(
        hand_size=1.0,
        volume=InteractionVolume(-2.0, 2.0, -2.0, 2.0),
        motion=MotionSignature(velocity_floor=0.15, velocity_ceiling=5.0),
        lighting=LightingProfile(120.0, 0.01, True),
        incidental_features=(),
        created_at=1.0,
    )


# The pointer (dominant) hand sits on the RIGHT side of the mirrored preview
# (larger x); the modifier hand sits on the LEFT. Handedness labels are set
# to misleading values throughout to prove role assignment stays spatial.
POINTER_DX = 0.30
MODIFIER_DX = -0.20


def pointer_hand(
    *,
    pinch: bool = False,
    dx: float = POINTER_DX,
    input_is_mirrored: bool = True,
) -> HandObservation:
    return make_hand(
        pinch=pinch,
        dx=dx,
        handedness="Left",  # deliberately wrong label; roles are spatial
        confidence=0.4,
        input_is_mirrored=input_is_mirrored,
    )


def modifier_neutral(**kwargs: Any) -> HandObservation:
    return make_hand(
        dx=MODIFIER_DX,
        handedness="Right",  # deliberately wrong label
        confidence=0.99,
        **kwargs,
    )


def modifier_open_palm(dx: float = MODIFIER_DX) -> HandObservation:
    return make_open_hand(handedness="Right", dx=dx)


def modifier_fist(dx: float = MODIFIER_DX) -> HandObservation:
    return make_fist(handedness="Right", dx=dx)


def arm_two_hand(
    pipeline: Pipeline,
    *,
    modifier: HandObservation | None = None,
) -> HandObservation:
    pointer = pointer_hand()
    modifier = modifier or modifier_neutral()
    pipeline.toggle_arm(0.0)
    pipeline.process_hands((pointer, modifier), 1.0)
    pipeline.process_hands((pointer, modifier), 1.06)
    assert pipeline.engine.armed
    pipeline.controller.sink.events.clear()
    return pointer


def enter_lock_mode(pipeline: Pipeline, now: float) -> float:
    """Feed the open-palm modifier for the debounce window; returns next t."""
    pointer = pointer_hand()
    pipeline.process_hands((pointer, modifier_open_palm()), now)
    pipeline.process_hands((pointer, modifier_open_palm()), now + 0.03)
    return now + 0.06


def enter_drag_mode(pipeline: Pipeline, now: float) -> float:
    pointer = pointer_hand()
    pipeline.process_hands((pointer, modifier_fist()), now)
    pipeline.process_hands((pointer, modifier_fist()), now + 0.03)
    return now + 0.06


def pinch_down(
    pipeline: Pipeline,
    now: float,
    *,
    modifier: HandObservation,
    dx: float = POINTER_DX,
) -> tuple[list[dict[str, Any]], float]:
    """Drive the dominant pinch through pose stability until LEFT_DOWN."""
    events: list[dict[str, Any]] = []
    events.extend(
        pipeline.process_hands((pointer_hand(pinch=True, dx=dx), modifier), now)
    )
    events.extend(
        pipeline.process_hands(
            (pointer_hand(pinch=True, dx=dx), modifier),
            now + 0.06,
        )
    )
    return events, now + 0.12


def test_lock_modifier_freezes_pointer_and_dominant_pinch_clicks(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)
    t = enter_lock_mode(pipeline, 1.10)

    # Pointer movement while locked must not move the cursor.
    move_events = pipeline.process_hands(
        (pointer_hand(dx=POINTER_DX + 0.06), modifier_open_palm()),
        t,
    )
    move_events.extend(
        pipeline.process_hands(
            (pointer_hand(dx=POINTER_DX + 0.12), modifier_open_palm()),
            t + 0.06,
        )
    )
    assert action_events(move_events, "move_pointer") == []
    assert "move_relative" not in sink_kinds(pipeline)

    # The dominant pinch clicks at the locked position.
    events, t = pinch_down(pipeline, t + 0.12, modifier=modifier_open_palm())
    assert len(action_events(events, "left_down")) == 1
    assert pipeline.controller.sink.left_is_down

    release = pipeline.process_hands(
        (pointer_hand(), modifier_open_palm()),
        t,
    )
    release.extend(
        pipeline.process_hands((pointer_hand(), modifier_open_palm()), t + 0.06)
    )
    assert len(action_events(release, "left_up")) == 1
    assert not pipeline.controller.sink.left_is_down
    # The whole click happened with zero cursor motion.
    assert "move_relative" not in sink_kinds(pipeline)


def test_neutral_modifier_suppresses_dominant_pinch(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)

    events, t = pinch_down(pipeline, 1.10, modifier=modifier_neutral())
    events.extend(
        pipeline.process_hands(
            (pointer_hand(pinch=True), modifier_neutral()),
            t,
        )
    )
    assert action_events(events, "left_down") == []
    assert not pipeline.controller.sink.left_is_down


def test_neutral_modifier_pointer_moves_normally(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)

    events = pipeline.process_hands(
        (pointer_hand(dx=POINTER_DX + 0.05), modifier_neutral()),
        1.10,
    )
    events.extend(
        pipeline.process_hands(
            (pointer_hand(dx=POINTER_DX + 0.10), modifier_neutral()),
            1.16,
        )
    )
    assert len(action_events(events, "move_pointer")) >= 1
    assert "move_relative" in sink_kinds(pipeline)


def test_drag_modifier_fist_enables_pinch_drag(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)
    t = enter_drag_mode(pipeline, 1.10)

    events, t = pinch_down(pipeline, t, modifier=modifier_fist())
    assert len(action_events(events, "left_down")) == 1
    assert pipeline.controller.sink.left_is_down

    drag = pipeline.process_hands(
        (pointer_hand(pinch=True, dx=POINTER_DX + 0.05), modifier_fist()),
        t,
    )
    drag.extend(
        pipeline.process_hands(
            (pointer_hand(pinch=True, dx=POINTER_DX + 0.10), modifier_fist()),
            t + 0.06,
        )
    )
    assert len(action_events(drag, "move_pointer")) >= 1
    assert pipeline.controller.sink.left_is_down

    release = pipeline.process_hands(
        (pointer_hand(dx=POINTER_DX + 0.10), modifier_fist()),
        t + 0.12,
    )
    release.extend(
        pipeline.process_hands(
            (pointer_hand(dx=POINTER_DX + 0.10), modifier_fist()),
            t + 0.18,
        )
    )
    assert len(action_events(release, "left_up")) == 1
    assert not pipeline.controller.sink.left_is_down


def test_modifier_fist_alone_does_not_click_or_disarm(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)

    now = 1.10
    for _ in range(30):  # far longer than pause_hold_seconds
        pipeline.process_hands((pointer_hand(), modifier_fist()), now)
        now += 0.06
    assert pipeline.engine.armed
    assert action_events([], "left_down") == []
    assert not pipeline.controller.sink.left_is_down
    assert "left_down" not in sink_kinds(pipeline)


def test_dominant_fist_still_disarms(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)

    fist_pointer = make_fist(handedness="Left", dx=POINTER_DX)
    now = 1.10
    for _ in range(15):  # > pause_hold_seconds at 60ms steps
        pipeline.process_hands((fist_pointer, modifier_neutral()), now)
        now += 0.06
    assert not pipeline.engine.armed


def test_modifier_flicker_does_not_drop_held_drag(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)
    t = enter_drag_mode(pipeline, 1.10)
    _, t = pinch_down(pipeline, t, modifier=modifier_fist())
    assert pipeline.controller.sink.left_is_down

    # One-frame flicker of the modifier away from the fist while the pinch
    # holds the button: the mode is latched, nothing releases.
    pipeline.process_hands(
        (pointer_hand(pinch=True), modifier_neutral()),
        t,
    )
    assert pipeline.controller.sink.left_is_down

    pipeline.process_hands((pointer_hand(pinch=True), modifier_fist()), t + 0.06)
    assert pipeline.controller.sink.left_is_down


def test_modifier_hand_loss_mid_drag_keeps_button_until_pinch_release(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)
    t = enter_drag_mode(pipeline, 1.10)
    _, t = pinch_down(pipeline, t, modifier=modifier_fist())
    assert pipeline.controller.sink.left_is_down

    # Modifier hand disappears entirely; the held pinch latches the mode.
    pipeline.process_hands((pointer_hand(pinch=True),), t)
    assert pipeline.controller.sink.left_is_down

    # Releasing the dominant pinch ends the drag even with one hand.
    release = pipeline.process_hands((pointer_hand(),), t + 0.06)
    release.extend(pipeline.process_hands((pointer_hand(),), t + 0.12))
    assert len(action_events(release, "left_up")) == 1
    assert not pipeline.controller.sink.left_is_down


def test_single_visible_hand_points_but_cannot_click(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)

    events = pipeline.process_hands((pointer_hand(dx=POINTER_DX + 0.05),), 1.10)
    events.extend(
        pipeline.process_hands((pointer_hand(dx=POINTER_DX + 0.10),), 1.16)
    )
    assert len(action_events(events, "move_pointer")) >= 1

    pinch_events = pipeline.process_hands((pointer_hand(pinch=True),), 1.30)
    pinch_events.extend(
        pipeline.process_hands((pointer_hand(pinch=True),), 1.36)
    )
    assert action_events(pinch_events, "left_down") == []
    assert not pipeline.controller.sink.left_is_down


def test_force_pause_releases_held_pinch(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)
    t = enter_drag_mode(pipeline, 1.10)
    _, t = pinch_down(pipeline, t, modifier=modifier_fist())
    assert pipeline.controller.sink.left_is_down

    events = pipeline.force_pause("test pause")
    assert len(action_events(events, "left_up")) == 1
    assert not pipeline.controller.sink.left_is_down
    assert not pipeline.engine.armed


def test_click_mode_change_releases_held_pinch(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)
    t = enter_drag_mode(pipeline, 1.10)
    _, t = pinch_down(pipeline, t, modifier=modifier_fist())
    assert pipeline.controller.sink.left_is_down

    settings = dict(DEFAULTS)
    settings.update(click_mode="single", dominant_hand="right", smoothing=0)
    pipeline.apply_settings(settings)
    assert not pipeline.controller.sink.left_is_down
    assert not pipeline.engine.armed


@pytest.mark.parametrize(
    ("dominant", "mirrored", "pointer_dx", "modifier_dx"),
    [
        ("right", True, 0.30, -0.20),
        ("left", True, -0.20, 0.30),
        ("right", False, -0.20, 0.30),
        ("left", False, 0.30, -0.20),
    ],
)
def test_spatial_roles_follow_dominant_side_not_labels(
    pipeline_factory: Callable[..., Pipeline],
    dominant: str,
    mirrored: bool,
    pointer_dx: float,
    modifier_dx: float,
) -> None:
    pipeline = pipeline_factory(dominant_hand=dominant)
    pointer = make_hand(
        dx=pointer_dx,
        handedness="Left" if dominant == "right" else "Right",  # wrong labels
        confidence=0.4,
        input_is_mirrored=mirrored,
    )
    modifier = make_open_hand(
        handedness="Right" if dominant == "right" else "Left",
        dx=modifier_dx,
        input_is_mirrored=mirrored,
    )
    pipeline.toggle_arm(0.0)
    pipeline.process_hands((pointer, modifier), 1.0)
    pipeline.process_hands((pointer, modifier), 1.06)
    assert pipeline.engine.armed
    pipeline.controller.sink.events.clear()

    # Open-palm modifier (2 frames) then the dominant-side pinch must click,
    # proving the dominant-side hand is the pointer regardless of labels.
    pipeline.process_hands((pointer, modifier), 1.10)
    pipeline.process_hands((pointer, modifier), 1.13)
    pinch = make_hand(
        pinch=True,
        dx=pointer_dx,
        handedness=pointer.handedness,
        confidence=0.4,
        input_is_mirrored=mirrored,
    )
    events = pipeline.process_hands((pinch, modifier), 1.20)
    events.extend(pipeline.process_hands((pinch, modifier), 1.26))
    assert len(action_events(events, "left_down")) == 1
    assert pipeline.controller.sink.left_is_down


def test_single_mode_pinch_clicks_without_modifier(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory(click_mode="single")
    pointer = make_hand(handedness="Right")
    pipeline.toggle_arm(0.0)
    pipeline.process_hands((pointer,), 1.0)
    pipeline.controller.sink.events.clear()

    pinch = make_hand(pinch=True, handedness="Right")
    events = pipeline.process_hands((pinch,), 1.10)
    events.extend(pipeline.process_hands((pinch,), 1.16))
    assert len(action_events(events, "left_down")) == 1
    assert pipeline.controller.sink.left_is_down


def test_daemon_quit_releases_held_pinch(
    pipeline_factory: Callable[..., Pipeline],
) -> None:
    pipeline = pipeline_factory()
    arm_two_hand(pipeline)
    t = enter_drag_mode(pipeline, 1.10)
    _, t = pinch_down(pipeline, t, modifier=modifier_fist())
    assert pipeline.controller.sink.left_is_down

    pipeline.release()
    assert not pipeline.controller.sink.left_is_down
