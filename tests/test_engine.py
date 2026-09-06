import json
import math

import pytest

from aircontrol.clutch import WakePoseClutch
from aircontrol.config import GestureConfig, load_config
from aircontrol.domain import ActionKind, GestureSample, Point2D, Pose
from aircontrol.engine import GestureEngine


def sample(pose, x=0.5, y=0.5, palm=0.1, pinch_ratio=1.0):
    return GestureSample(
        pose=pose,
        pointer=Point2D(x, y),
        center=Point2D(x, y),
        palm_size=palm,
        extended_fingers=(False, False, False, False),
        pinch_ratio=pinch_ratio,
    )


def kinds(actions):
    return [action.kind for action in actions]


def arm(engine):
    now = 0.0
    while now <= engine.config.arm_hold_seconds + 0.05:
        engine.update(sample(Pose.OPEN_PALM), now)
        now += 0.05
    assert engine.armed


def stabilize(engine, pose, start=1.0, **kwargs):
    engine.update(sample(pose, **kwargs), start)
    return engine.update(sample(pose, **kwargs), start + engine.config.stability_seconds + 0.01)


def test_open_palm_arms_and_fist_pauses():
    engine = GestureEngine(GestureConfig())
    arm(engine)
    now = 1.0
    while now <= 1.0 + engine.config.pause_hold_seconds + 0.05:
        engine.update(sample(Pose.FIST), now)
        now += 0.05
    assert not engine.armed


def test_activation_hold_restarts_when_the_hand_is_moving():
    engine = GestureEngine(
        GestureConfig(hold_motion_limit_palms=0.3, max_observation_gap_seconds=1.0)
    )
    engine.update(sample(Pose.OPEN_PALM, x=0.5), 0.0)
    engine.update(sample(Pose.OPEN_PALM, x=0.6), 0.6)
    engine.update(sample(Pose.OPEN_PALM, x=0.6), 0.72)
    assert not engine.armed
    engine.update(sample(Pose.OPEN_PALM, x=0.6), 1.31)
    assert engine.armed


def test_manual_arm_auto_pauses_when_no_hand_arrives():
    engine = GestureEngine(GestureConfig(auto_pause_seconds=0.5))
    engine.manual_toggle(now=0.0)
    engine.update(None, 0.51)
    assert not engine.armed


def test_disarmed_engine_never_emits_pointer_actions():
    engine = GestureEngine(GestureConfig())
    stabilize(engine, Pose.POINTER)
    assert engine.update(sample(Pose.POINTER, x=0.7), 2.0) == []


def test_pointer_motion_is_relative_and_capped():
    engine = GestureEngine(GestureConfig())
    arm(engine)
    stabilize(engine, Pose.POINTER, x=0.5)
    actions = engine.update(sample(Pose.POINTER, x=0.6), 1.2)
    assert kinds(actions) == [ActionKind.MOVE_POINTER]
    assert 0 < actions[0].dx <= engine.config.pointer_max_step_palms


def test_slow_pointer_motion_is_not_dropped_by_the_legacy_deadzone():
    engine = GestureEngine(
        GestureConfig(
            max_observation_gap_seconds=1.0,
            pointer_deadzone_palms=0.018,
            pointer_max_step_palms=1.0,
        )
    )
    arm(engine)
    stabilize(engine, Pose.POINTER, x=0.5)

    moves = []
    for frame in range(1, 11):
        actions = engine.update(
            sample(Pose.POINTER, x=0.5 + frame * 0.001),
            1.15 + frame / 30,
        )
        moves.extend(
            action for action in actions if action.kind == ActionKind.MOVE_POINTER
        )

    assert len(moves) == 10
    assert all(0.0 < action.dx < 0.018 for action in moves)
    assert sum(action.dx for action in moves) > 0.05


def test_pointer_filter_suppresses_static_jitter():
    engine = GestureEngine(
        GestureConfig(
            max_observation_gap_seconds=1.0,
            pointer_max_step_palms=1.0,
        )
    )
    arm(engine)
    stabilize(engine, Pose.POINTER, x=0.5)

    moves = []
    for frame in range(1, 31):
        sign = 1.0 if frame % 2 else -1.0
        actions = engine.update(
            sample(Pose.POINTER, x=0.5 + sign * 0.002),
            1.15 + frame / 30,
        )
        moves.extend(
            action for action in actions if action.kind == ActionKind.MOVE_POINTER
        )

    assert moves
    assert max(math.hypot(action.dx, action.dy) for action in moves) < 0.006
    assert abs(sum(action.dx for action in moves)) < 0.006


def test_pointer_filter_stays_responsive_to_fast_motion():
    engine = GestureEngine(
        GestureConfig(
            max_observation_gap_seconds=1.0,
            pointer_max_step_palms=0.2,
        )
    )
    arm(engine)
    stabilize(engine, Pose.POINTER, x=0.5)

    actions = engine.update(sample(Pose.POINTER, x=0.62), 1.15 + 1 / 30)

    assert kinds(actions) == [ActionKind.MOVE_POINTER]
    assert actions[0].dx >= 0.15
    assert actions[0].dx <= engine.config.pointer_max_step_palms


def test_pinch_holds_and_releases_left_button():
    engine = GestureEngine(GestureConfig())
    arm(engine)
    actions = stabilize(engine, Pose.PINCH)
    assert kinds(actions) == [ActionKind.LEFT_DOWN]
    actions = engine.update(sample(Pose.POINTER), 1.2)
    assert ActionKind.LEFT_UP in kinds(actions)


def test_natural_scroll_follows_upward_finger_motion():
    engine = GestureEngine(GestureConfig(scroll_notches_per_palm=10.0))
    arm(engine)
    stabilize(engine, Pose.SCROLL, y=0.55)
    actions = engine.update(sample(Pose.SCROLL, y=0.53), 1.2)
    assert kinds(actions) == [ActionKind.SCROLL]
    assert actions[0].amount < 0


def test_three_finger_swipe_fires_only_once_until_reset():
    engine = GestureEngine(GestureConfig(swipe_threshold_palms=0.8))
    arm(engine)
    stabilize(engine, Pose.WINDOW_SWIPE, x=0.5)
    first = engine.update(sample(Pose.WINDOW_SWIPE, x=0.4), 1.35)
    second = engine.update(sample(Pose.WINDOW_SWIPE, x=0.3), 1.45)
    assert kinds(first) == [ActionKind.SWITCH_NEXT]
    assert second == []


def test_slow_three_finger_drift_does_not_switch_apps():
    engine = GestureEngine(GestureConfig(swipe_threshold_palms=0.8, swipe_max_seconds=0.5))
    arm(engine)
    stabilize(engine, Pose.WINDOW_SWIPE, x=0.5)

    actions = []
    for index in range(1, 8):
        actions.extend(
            engine.update(
                sample(Pose.WINDOW_SWIPE, x=0.5 - index * 0.015),
                1.15 + index * 0.1,
            )
        )

    assert actions == []


def test_tracking_loss_releases_drag_then_auto_pauses():
    config = GestureConfig(lost_hand_grace_seconds=0.2, auto_pause_seconds=1.0)
    engine = GestureEngine(config)
    arm(engine)
    stabilize(engine, Pose.PINCH)
    assert engine.update(None, 1.25) == []
    assert kinds(engine.update(None, 1.4)) == [ActionKind.LEFT_UP]
    engine.update(None, 2.3)
    assert not engine.armed


def test_wake_pose_hand_loss_releases_drag_without_disarming():
    config = GestureConfig(
        stability_seconds=0.1,
        lost_hand_grace_seconds=0.2,
        auto_pause_seconds=0.5,
        max_observation_gap_seconds=10.0,
    )
    clutch = WakePoseClutch(config, None, hold_seconds=0.2)
    engine = GestureEngine(config, clutch=clutch)

    engine.update(sample(Pose.OPEN_PALM), 0.0)
    engine.update(sample(Pose.OPEN_PALM), 0.21)
    assert engine.armed
    assert kinds(stabilize(engine, Pose.PINCH, start=0.3)) == [ActionKind.LEFT_DOWN]

    assert engine.update(None, 0.5) == []
    released = engine.update(None, 1.0)

    assert kinds(released) == [ActionKind.LEFT_UP]
    assert engine.armed
    assert engine.status().status_text == "Armed, fist pauses control"
    assert engine.update(None, 5.0) == []
    assert engine.armed
    assert engine.status().status_text == "Armed, fist pauses control"


def test_brief_tracking_loss_reanchors_scroll_without_replaying_blind_motion():
    engine = GestureEngine(GestureConfig(scroll_notches_per_palm=10.0))
    arm(engine)
    stabilize(engine, Pose.SCROLL, y=0.5)

    engine.update(None, 1.2)
    recovered = engine.update(sample(Pose.SCROLL, y=0.2), 1.25)

    assert recovered == []


def test_candidate_cannot_stabilize_across_a_missing_observation():
    engine = GestureEngine(GestureConfig())
    arm(engine)
    engine.update(sample(Pose.PINCH), 1.0)
    engine.update(None, 1.08)

    actions = engine.update(sample(Pose.PINCH), 1.16)

    assert ActionKind.LEFT_DOWN not in kinds(actions)


def test_long_frame_stall_releases_drag_before_accepting_new_motion():
    engine = GestureEngine(GestureConfig(max_observation_gap_seconds=0.2))
    arm(engine)
    stabilize(engine, Pose.PINCH)

    actions = engine.update(sample(Pose.PINCH, x=0.7), 1.5)

    assert ActionKind.LEFT_UP in kinds(actions)
    assert ActionKind.MOVE_POINTER not in kinds(actions)


def test_three_finger_swipe_fires_after_a_long_stationary_hold():
    """Holding the pose steady past swipe_max_seconds must not kill the swipe.

    Users raise three fingers, wait for recognition, then swipe; the motion
    window has to start at motion onset, not pose entry.
    """
    engine = GestureEngine(GestureConfig(swipe_threshold_palms=0.8))
    arm(engine)
    stabilize(engine, Pose.WINDOW_SWIPE, x=0.5)
    now = 1.15
    while now < 3.0:
        engine.update(sample(Pose.WINDOW_SWIPE, x=0.5), now)
        now += 0.05
    first = engine.update(sample(Pose.WINDOW_SWIPE, x=0.4), now + 0.1)
    second = engine.update(sample(Pose.WINDOW_SWIPE, x=0.3), now + 0.2)
    assert ActionKind.SWITCH_NEXT in kinds(first) + kinds(second)


def test_pointer_freezes_during_pinch_approach_until_left_down():
    engine = GestureEngine(
        GestureConfig(
            stability_seconds=0.05,
            pointer_min_cutoff=100.0,
            pointer_beta=0.0,
            max_observation_gap_seconds=1.0,
        )
    )
    arm(engine)
    stabilize(engine, Pose.POINTER, start=1.0, x=0.5, pinch_ratio=1.0)

    actions = []
    actions.extend(
        engine.update(sample(Pose.POINTER, x=0.505, pinch_ratio=0.74), 1.07)
    )
    actions.extend(
        engine.update(sample(Pose.POINTER, x=0.51, pinch_ratio=0.6), 1.08)
    )
    actions.extend(engine.update(sample(Pose.PINCH, x=0.51, pinch_ratio=0.4), 1.09))
    actions.extend(engine.update(sample(Pose.PINCH, x=0.51, pinch_ratio=0.4), 1.15))

    assert kinds(actions) == [ActionKind.LEFT_DOWN]


def test_pinch_drag_stays_locked_until_deliberate_motion_then_releases():
    engine = GestureEngine(
        GestureConfig(
            pointer_min_cutoff=100.0,
            pointer_beta=0.0,
            max_observation_gap_seconds=1.0,
        )
    )
    arm(engine)
    assert kinds(stabilize(engine, Pose.PINCH, x=0.5)) == [ActionKind.LEFT_DOWN]

    settled = engine.update(sample(Pose.PINCH, x=0.505, pinch_ratio=0.4), 1.2)
    dragged = engine.update(sample(Pose.PINCH, x=0.53, pinch_ratio=0.4), 1.25)
    released = engine.update(sample(Pose.POINTER, x=0.53), 1.3)

    assert settled == []
    assert kinds(dragged) == [ActionKind.MOVE_POINTER]
    assert kinds(released) == [ActionKind.LEFT_UP]


def test_stable_pinch_entry_emits_down_without_pointer_move():
    engine = GestureEngine(
        GestureConfig(stability_seconds=0.05, max_observation_gap_seconds=1.0)
    )
    arm(engine)
    stabilize(engine, Pose.POINTER, start=1.0, x=0.5)

    first_pinch = engine.update(sample(Pose.PINCH, x=0.5, pinch_ratio=0.4), 1.07)
    engaged = engine.update(sample(Pose.PINCH, x=0.5, pinch_ratio=0.4), 1.13)

    assert first_pinch == []
    assert kinds(engaged) == [ActionKind.LEFT_DOWN]


def test_pointer_moves_normally_above_pinch_approach_threshold():
    engine = GestureEngine(
        GestureConfig(
            pointer_min_cutoff=100.0,
            pointer_beta=0.0,
            max_observation_gap_seconds=1.0,
        )
    )
    arm(engine)
    stabilize(engine, Pose.POINTER, start=1.0, x=0.5, pinch_ratio=1.0)

    actions = engine.update(sample(Pose.POINTER, x=0.51, pinch_ratio=0.8), 1.2)

    assert kinds(actions) == [ActionKind.MOVE_POINTER]


def test_pinch_lock_config_defaults_are_positive_and_ordered():
    config = GestureConfig()

    assert config.pinch_approach_palms == pytest.approx(0.75)
    assert config.pinch_drag_release_palms == pytest.approx(0.08)
    assert config.pinch_approach_palms > config.pinch_threshold_palms


@pytest.mark.parametrize(
    "gesture_values",
    [
        {"pinch_approach_palms": 0.0},
        {"pinch_approach_palms": float("nan")},
        {"pinch_approach_palms": 0.42},
        {"pinch_drag_release_palms": 0.0},
        {"pinch_drag_release_palms": float("nan")},
    ],
)
def test_invalid_pinch_lock_config_is_rejected(tmp_path, gesture_values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"gestures": gesture_values}))

    with pytest.raises(ValueError):
        load_config(path)
