from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from aircontrol.config import AppConfig, GestureConfig
from aircontrol.domain import GestureSample, Point2D, Pose
from aircontrol.profile import CalibrationProfile, InteractionVolume


@dataclass(frozen=True, slots=True)
class ClutchState:
    armed: bool
    progress: float
    status_text: str


class ClutchStrategy(Protocol):
    def update(self, sample: GestureSample | None, now: float) -> ClutchState: ...

    def set_armed(self, armed: bool, now: float) -> None: ...

    def reset(self) -> None: ...

    def notify_gesture(self, now: float) -> None: ...


class WakePoseClutch:
    def __init__(
        self,
        config: GestureConfig,
        volume: InteractionVolume | None,
        *,
        hold_seconds: float = 0.4,
        window_seconds: float | None = None,
    ) -> None:
        if hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        if window_seconds is not None and window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.config = config
        self.volume = volume
        self.hold_seconds = hold_seconds
        # Kept for configuration/API compatibility. Wake-pose arming is now
        # latched and no longer expires after an inactivity window.
        self.window_seconds = window_seconds
        self._armed = False
        self._last_hand_seen_at: float | None = None
        self._hold_pose = Pose.NONE
        self._hold_since: float | None = None
        self._hold_anchor: Point2D | None = None
        self._hold_palm_size = 1.0

    def update(self, sample: GestureSample | None, now: float) -> ClutchState:
        if sample is not None:
            self._last_hand_seen_at = now
        if self._armed:
            if sample is None or sample.pose != Pose.FIST:
                self._reset_hold()
                return ClutchState(True, 0.0, "Armed — fist pauses control")
            return self._update_hold(
                sample,
                now,
                target=Pose.FIST,
                duration=self.config.pause_hold_seconds,
            )

        if (
            sample is None
            or sample.pose != Pose.OPEN_PALM
            or not self._inside_volume(sample)
        ):
            self._reset_hold()
            return self._disarmed_state()
        return self._update_hold(
            sample,
            now,
            target=Pose.OPEN_PALM,
            duration=self.hold_seconds,
        )

    def reset(self) -> None:
        self._disarm()

    def set_armed(self, armed: bool, now: float) -> None:
        if armed:
            self._armed = True
            self._last_hand_seen_at = now
            self._reset_hold()
        else:
            self._disarm()

    def notify_gesture(self, now: float) -> None:
        del now

    def _inside_volume(self, sample: GestureSample) -> bool:
        return self.volume is None or self.volume.contains(sample.center.x, sample.center.y)

    def _update_hold(
        self,
        sample: GestureSample,
        now: float,
        *,
        target: Pose,
        duration: float,
    ) -> ClutchState:
        if self._hold_pose != target or self._hold_since is None:
            self._start_hold(target, sample, now)
            return self._holding_state(target, 0.0)

        if self._hold_anchor is not None:
            motion = math.hypot(
                sample.center.x - self._hold_anchor.x,
                sample.center.y - self._hold_anchor.y,
            ) / self._hold_palm_size
            if motion > self.config.hold_motion_limit_palms:
                self._start_hold(target, sample, now)
                return self._holding_state(target, 0.0)

        progress = max(0.0, min(1.0, (now - self._hold_since) / duration))
        if progress < 1.0:
            return self._holding_state(target, progress)

        self._reset_hold()
        if target == Pose.OPEN_PALM:
            self._armed = True
            return ClutchState(True, 0.0, "Armed — fist pauses control")
        self._disarm()
        return self._disarmed_state()

    def _start_hold(self, target: Pose, sample: GestureSample, now: float) -> None:
        self._hold_pose = target
        self._hold_since = now
        self._hold_anchor = sample.center
        self._hold_palm_size = max(sample.palm_size, self.config.min_palm_size)

    @staticmethod
    def _holding_state(target: Pose, progress: float) -> ClutchState:
        if target == Pose.FIST:
            return ClutchState(True, progress, "Hold steady to pause")
        return ClutchState(False, progress, "Hold steady to arm")

    @staticmethod
    def _disarmed_state() -> ClutchState:
        return ClutchState(False, 0.0, "Show an open palm to start")

    def _disarm(self) -> None:
        self._armed = False
        self._last_hand_seen_at = None
        self._reset_hold()

    def _reset_hold(self) -> None:
        self._hold_pose = Pose.NONE
        self._hold_since = None
        self._hold_anchor = None
        self._hold_palm_size = 1.0


class SpatialZoneClutch:
    def __init__(self, plane_y: float) -> None:
        self.plane_y = plane_y

    def update(self, sample: GestureSample | None, now: float) -> ClutchState:
        del now
        armed = sample is not None and sample.center.y < self.plane_y
        text = "Armed — hand is above control plane" if armed else "Move hand above control plane"
        return ClutchState(armed, 0.0, text)

    def reset(self) -> None:
        return None

    def set_armed(self, armed: bool, now: float) -> None:
        del armed, now

    def notify_gesture(self, now: float) -> None:
        del now


class AlwaysOnClutch:
    def update(self, sample: GestureSample | None, now: float) -> ClutchState:
        del sample, now
        return ClutchState(True, 0.0, "Armed — always-on expert mode")

    def reset(self) -> None:
        return None

    def set_armed(self, armed: bool, now: float) -> None:
        del armed, now

    def notify_gesture(self, now: float) -> None:
        del now


def build_clutch(
    config: AppConfig,
    profile: CalibrationProfile | None,
) -> ClutchStrategy:
    clutch_config = config.clutch
    if clutch_config.mode == "wake_pose":
        return WakePoseClutch(
            config.gestures,
            None if profile is None else profile.volume,
            hold_seconds=clutch_config.hold_seconds,
            window_seconds=clutch_config.window_seconds,
        )
    if clutch_config.mode == "spatial_zone":
        return SpatialZoneClutch(clutch_config.plane_y)
    if clutch_config.mode == "always_on":
        if clutch_config.acknowledged_expert_mode is not True:
            raise ValueError(
                "clutch.acknowledged_expert_mode must be true for always_on mode"
            )
        return AlwaysOnClutch()
    raise ValueError(f"Unsupported clutch mode: {clutch_config.mode}")
