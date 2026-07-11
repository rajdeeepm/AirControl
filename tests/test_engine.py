from aircontrol.config import GestureConfig
from aircontrol.domain import ActionKind, GestureSample, Point2D, Pose
from aircontrol.engine import GestureEngine


def sample(pose, x=0.5, y=0.5, palm=0.1):
    return GestureSample(
        pose=pose,
        pointer=Point2D(x, y),
        center=Point2D(x, y),
        palm_size=palm,
        extended_fingers=(False, False, False, False),
        pinch_ratio=1.0,
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
    engine = GestureEngine(GestureConfig(pointer_deadzone_palms=0.001))
    arm(engine)
    stabilize(engine, Pose.POINTER, x=0.5)
    actions = engine.update(sample(Pose.POINTER, x=0.6), 1.2)
    assert kinds(actions) == [ActionKind.MOVE_POINTER]
    assert 0 < actions[0].dx <= engine.config.pointer_max_step_palms


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
