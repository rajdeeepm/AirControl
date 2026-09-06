from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from aircontrol.clutch import (
    AlwaysOnClutch,
    ClutchState,
    SpatialZoneClutch,
    WakePoseClutch,
    build_clutch,
)
from aircontrol.config import AppConfig, ClutchConfig, GestureConfig, load_config
from aircontrol.domain import ActionKind, GestureSample, Point2D, Pose
from aircontrol.engine import GestureEngine
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
)


def sample(
    pose: Pose,
    x: float = 0.5,
    y: float = 0.5,
    palm: float = 0.1,
) -> GestureSample:
    return GestureSample(
        pose=pose,
        pointer=Point2D(x, y),
        center=Point2D(x, y),
        palm_size=palm,
        extended_fingers=(False, False, False, False),
        pinch_ratio=1.0,
    )


def profile_with_volume(volume: InteractionVolume) -> CalibrationProfile:
    return CalibrationProfile(
        hand_size=0.1,
        volume=volume,
        motion=MotionSignature(velocity_floor=0.2, velocity_ceiling=2.0),
        lighting=LightingProfile(
            mean_brightness=120.0,
            landmark_jitter=0.01,
            acceptable=True,
        ),
        incidental_features=(),
        created_at=1.0,
    )


def test_clutch_state_is_frozen_and_slotted() -> None:
    state = ClutchState(armed=False, progress=0.0, status_text="Paused")

    assert not hasattr(state, "__dict__")
    with pytest.raises(FrozenInstanceError):
        state.armed = True


def test_wake_pose_clutch_defaults_to_no_inactivity_window() -> None:
    assert AppConfig.defaults().clutch.window_seconds is None


def test_wake_pose_arms_after_steady_hold_inside_interaction_volume() -> None:
    volume = InteractionVolume(x_min=0.2, x_max=0.8, y_min=0.2, y_max=0.8)
    clutch = WakePoseClutch(
        GestureConfig(),
        volume,
        hold_seconds=0.4,
        window_seconds=1.0,
    )

    assert not clutch.update(sample(Pose.OPEN_PALM, x=0.9), 0.0).armed
    assert not clutch.update(sample(Pose.OPEN_PALM, x=0.9), 0.5).armed
    assert not clutch.update(sample(Pose.OPEN_PALM), 0.6).armed
    halfway = clutch.update(sample(Pose.OPEN_PALM), 0.8)
    armed = clutch.update(sample(Pose.OPEN_PALM), 1.01)

    assert halfway.progress == pytest.approx(0.5)
    assert not halfway.armed
    assert armed.armed


def test_wake_pose_without_a_volume_allows_any_center() -> None:
    clutch = WakePoseClutch(
        GestureConfig(),
        None,
        hold_seconds=0.2,
        window_seconds=1.0,
    )

    clutch.update(sample(Pose.OPEN_PALM, x=0.99, y=0.01), 0.0)

    assert clutch.update(sample(Pose.OPEN_PALM, x=0.99, y=0.01), 0.21).armed


def test_wake_pose_motion_beyond_limit_restarts_hold_timer() -> None:
    config = GestureConfig(hold_motion_limit_palms=0.3)
    clutch = WakePoseClutch(
        config,
        None,
        hold_seconds=0.4,
        window_seconds=1.0,
    )

    clutch.update(sample(Pose.OPEN_PALM, x=0.5), 0.0)
    moved = clutch.update(sample(Pose.OPEN_PALM, x=0.54), 0.3)
    too_soon = clutch.update(sample(Pose.OPEN_PALM, x=0.54), 0.41)
    armed = clutch.update(sample(Pose.OPEN_PALM, x=0.54), 0.71)

    assert moved.progress == 0.0
    assert not too_soon.armed
    assert too_soon.progress == pytest.approx(0.275)
    assert armed.armed


def test_wake_pose_arming_stays_latched_past_former_window() -> None:
    clutch = WakePoseClutch(
        GestureConfig(),
        None,
        hold_seconds=0.2,
        window_seconds=1.0,
    )
    clutch.update(sample(Pose.OPEN_PALM), 0.0)
    assert clutch.update(sample(Pose.OPEN_PALM), 0.21).armed

    assert clutch.update(sample(Pose.POINTER), 1.2).armed
    assert clutch.update(sample(Pose.POINTER), 100.0).armed
    assert clutch.update(sample(Pose.POINTER), 1_000.0).armed


def test_notify_gesture_does_not_change_latched_wake_pose_state() -> None:
    clutch = WakePoseClutch(
        GestureConfig(),
        None,
        hold_seconds=0.2,
        window_seconds=1.0,
    )
    clutch.update(sample(Pose.OPEN_PALM), 0.0)
    assert clutch.update(sample(Pose.OPEN_PALM), 0.21).armed

    clutch.notify_gesture(1.0)

    assert clutch.update(None, 1.8).armed
    assert clutch.update(None, 2.01).armed


def test_fist_hold_disarms_wake_pose_clutch() -> None:
    config = GestureConfig(pause_hold_seconds=0.3)
    clutch = WakePoseClutch(
        config,
        None,
        hold_seconds=0.2,
        window_seconds=0.1,
    )
    clutch.update(sample(Pose.OPEN_PALM), 0.0)
    assert clutch.update(sample(Pose.OPEN_PALM), 0.21).armed

    clutch.update(sample(Pose.FIST), 0.4)
    holding = clutch.update(sample(Pose.FIST), 0.69)
    paused = clutch.update(sample(Pose.FIST), 0.71)

    assert holding.armed
    assert holding.progress == pytest.approx(0.29 / 0.3)
    assert not paused.armed


def test_wake_pose_stays_armed_through_hand_absence_until_fist_hold() -> None:
    config = GestureConfig(auto_pause_seconds=0.5, pause_hold_seconds=0.3)
    clutch = WakePoseClutch(config, None, hold_seconds=0.2)
    clutch.update(sample(Pose.OPEN_PALM), 0.0)
    assert clutch.update(sample(Pose.OPEN_PALM), 0.21).armed

    absent_states = [
        clutch.update(None, 0.8),
        clutch.update(None, 5.0),
        clutch.update(None, 50.0),
    ]

    assert all(state.armed for state in absent_states)
    clutch.update(sample(Pose.FIST), 50.1)
    assert clutch.update(sample(Pose.FIST), 50.39).armed
    assert not clutch.update(sample(Pose.FIST), 50.41).armed


def test_spatial_zone_is_armed_only_above_plane() -> None:
    clutch = SpatialZoneClutch(plane_y=0.5)

    assert clutch.update(sample(Pose.POINTER, y=0.49), 0.0).armed
    assert not clutch.update(sample(Pose.POINTER, y=0.5), 0.1).armed
    assert not clutch.update(sample(Pose.POINTER, y=0.51), 0.2).armed
    assert not clutch.update(None, 0.3).armed


def test_always_on_clutch_is_always_armed() -> None:
    clutch = AlwaysOnClutch()

    assert clutch.update(None, 0.0).armed
    clutch.notify_gesture(0.1)
    clutch.reset()
    assert clutch.update(sample(Pose.NONE), 0.2).armed


def test_build_clutch_selects_configured_mode_and_profile_volume() -> None:
    volume = InteractionVolume(x_min=0.1, x_max=0.9, y_min=0.2, y_max=0.8)
    profile = profile_with_volume(volume)
    config = AppConfig.defaults()

    wake = build_clutch(config, profile)
    config.clutch = ClutchConfig(mode="spatial_zone", plane_y=0.35)
    spatial = build_clutch(config, profile)
    config.clutch = ClutchConfig(mode="always_on", acknowledged_expert_mode=True)
    always = build_clutch(config, profile)

    assert isinstance(wake, WakePoseClutch)
    assert wake.volume is volume
    assert isinstance(spatial, SpatialZoneClutch)
    assert spatial.plane_y == 0.35
    assert isinstance(always, AlwaysOnClutch)


def test_load_config_rejects_always_on_without_expert_acknowledgement(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"clutch": {"mode": "always_on"}}))

    with pytest.raises(ValueError, match="acknowledged_expert_mode"):
        load_config(path)


@pytest.mark.parametrize(
    "clutch_values",
    [
        {"mode": "unsupported"},
        {"hold_seconds": 0.0},
        {"hold_seconds": -0.1},
        {"window_seconds": 0.0},
        {"window_seconds": -0.1},
        {"plane_y": -0.01},
        {"plane_y": 1.01},
    ],
)
def test_load_config_rejects_invalid_clutch_settings(tmp_path, clutch_values) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"clutch": clutch_values}))

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_accepts_acknowledged_always_on_mode(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "clutch": {
                    "mode": "always_on",
                    "acknowledged_expert_mode": True,
                }
            }
        )
    )

    config = load_config(path)

    assert config.clutch.mode == "always_on"
    assert config.clutch.acknowledged_expert_mode is True


def test_engine_wake_pose_arming_stays_latched_past_former_window() -> None:
    config = GestureConfig(max_observation_gap_seconds=1.0)
    clutch = WakePoseClutch(
        config,
        None,
        hold_seconds=0.2,
        window_seconds=0.5,
    )
    engine = GestureEngine(config, clutch=clutch)

    engine.update(sample(Pose.OPEN_PALM), 0.0)
    engine.update(sample(Pose.OPEN_PALM), 0.1)
    assert engine.status().hold_progress == pytest.approx(0.5)
    engine.update(sample(Pose.OPEN_PALM), 0.21)
    assert engine.armed

    engine.update(sample(Pose.POINTER), 0.72)

    assert engine.armed


def test_engine_missing_hand_does_not_auto_pause_latched_clutch() -> None:
    config = GestureConfig(
        auto_pause_seconds=0.5,
        lost_hand_grace_seconds=0.1,
        max_observation_gap_seconds=10.0,
    )
    clutch = WakePoseClutch(
        config,
        None,
        hold_seconds=0.2,
        window_seconds=0.1,
    )
    engine = GestureEngine(config, clutch=clutch)

    engine.update(sample(Pose.OPEN_PALM), 0.0)
    engine.update(sample(Pose.OPEN_PALM), 0.21)
    engine.update(sample(Pose.POINTER), 0.3)

    actions = engine.update(None, 0.81)

    assert actions == []
    assert engine.armed
    assert engine.status().status_text == "Armed, fist pauses control"

    engine.update(sample(Pose.POINTER), 0.9)

    assert engine.armed


def test_engine_notifies_clutch_for_actions_and_cleans_up_on_disarm() -> None:
    class RecordingClutch:
        def __init__(self) -> None:
            self.state = ClutchState(True, 0.25, "Ready")
            self.notifications: list[float] = []

        def update(self, sample: GestureSample | None, now: float) -> ClutchState:
            return self.state

        def reset(self) -> None:
            self.state = ClutchState(False, 0.0, "Paused")

        def notify_gesture(self, now: float) -> None:
            self.notifications.append(now)

    clutch = RecordingClutch()
    config = GestureConfig(stability_seconds=0.1)
    engine = GestureEngine(config, clutch=clutch)

    engine.update(sample(Pose.PINCH), 0.0)
    actions = engine.update(sample(Pose.PINCH), 0.11)

    assert [action.kind for action in actions] == [ActionKind.LEFT_DOWN]
    assert clutch.notifications == [0.11]
    assert engine.status().status_text == "Ready"
    assert engine.status().hold_progress == 0.25

    clutch.state = ClutchState(False, 0.0, "Paused")
    actions = engine.update(sample(Pose.POINTER), 0.2)

    assert [action.kind for action in actions] == [ActionKind.LEFT_UP]
    assert clutch.notifications == [0.11]
    assert not engine.armed


def test_engine_without_clutch_preserves_legacy_open_palm_and_fist_holds() -> None:
    config = GestureConfig(
        arm_hold_seconds=0.2,
        pause_hold_seconds=0.15,
        max_observation_gap_seconds=1.0,
    )
    engine = GestureEngine(config)

    engine.update(sample(Pose.OPEN_PALM), 0.0)
    engine.update(sample(Pose.OPEN_PALM), 0.1)
    assert engine.status().hold_progress == pytest.approx(0.5)
    engine.update(sample(Pose.OPEN_PALM), 0.21)
    assert engine.armed

    engine.update(sample(Pose.FIST), 0.3)
    engine.update(sample(Pose.FIST), 0.46)

    assert not engine.armed
