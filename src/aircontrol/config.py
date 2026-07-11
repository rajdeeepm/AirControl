from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CameraConfig:
    index: int = 0
    width: int = 960
    height: int = 540
    fps: int = 30
    mirror: bool = True


@dataclass(slots=True)
class TrackingConfig:
    model_path: str = "models/hand_landmarker.task"
    max_hands: int = 1
    min_detection_confidence: float = 0.6
    min_presence_confidence: float = 0.6
    min_tracking_confidence: float = 0.6
    watchdog_seconds: float = 0.35
    startup_timeout_seconds: float = 20.0
    shutdown_timeout_seconds: float = 2.0


@dataclass(slots=True)
class GestureConfig:
    stability_seconds: float = 0.14
    arm_hold_seconds: float = 0.7
    pause_hold_seconds: float = 0.55
    hold_motion_limit_palms: float = 0.35
    lost_hand_grace_seconds: float = 0.22
    auto_pause_seconds: float = 3.0
    extended_finger_angle: float = 135.0
    extended_dip_angle: float = 125.0
    extended_length_ratio: float = 0.72
    finger_reach_ratio: float = 0.9
    curled_finger_angle: float = 125.0
    curled_dip_angle: float = 115.0
    curled_length_ratio: float = 0.68
    curled_tip_reach_ratio: float = 1.1
    pinch_threshold_palms: float = 0.42
    min_palm_size: float = 0.035
    min_palm_width_ratio: float = 0.16
    min_palm_orientation: float = 0.08
    require_palm_facing: bool = True
    max_observation_gap_seconds: float = 0.2
    pointer_smoothing: float = 0.36
    pointer_deadzone_palms: float = 0.018
    pointer_max_step_palms: float = 0.2
    scroll_notches_per_palm: float = 7.0
    max_scroll_notches_per_frame: float = 3.0
    natural_scroll: bool = True
    swipe_threshold_palms: float = 0.9
    swipe_axis_ratio: float = 1.6
    swipe_max_seconds: float = 0.8


@dataclass(slots=True)
class InputConfig:
    pointer_pixels_per_palm: float = 760.0


@dataclass(slots=True)
class DisplayConfig:
    window_name: str = "AirControl — Local Gesture HUD"
    preview_width: int = 640
    always_on_top: bool = True
    show_landmarks: bool = True


@dataclass(slots=True)
class AppConfig:
    camera: CameraConfig
    tracking: TrackingConfig
    gestures: GestureConfig
    input: InputConfig
    display: DisplayConfig

    @classmethod
    def defaults(cls) -> "AppConfig":
        return cls(
            camera=CameraConfig(),
            tracking=TrackingConfig(),
            gestures=GestureConfig(),
            input=InputConfig(),
            display=DisplayConfig(),
        )


def _merge_dataclass(target: Any, values: dict[str, Any], prefix: str = "") -> None:
    known = {field.name for field in fields(target)}
    unknown = set(values) - known
    if unknown:
        names = ", ".join(sorted(f"{prefix}{name}" for name in unknown))
        raise ValueError(f"Unknown configuration setting(s): {names}")

    for name, value in values.items():
        current = getattr(target, name)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"Configuration section '{prefix}{name}' must be an object")
            _merge_dataclass(current, value, prefix=f"{prefix}{name}.")
        else:
            setattr(target, name, value)


def _validate(config: AppConfig) -> None:
    if config.camera.width < 320 or config.camera.height < 240:
        raise ValueError("Camera resolution must be at least 320×240")
    if config.camera.fps <= 0:
        raise ValueError("camera.fps must be positive")
    if config.tracking.max_hands != 1:
        raise ValueError("This MVP intentionally accepts exactly one control hand")
    for name in (
        "min_detection_confidence",
        "min_presence_confidence",
        "min_tracking_confidence",
    ):
        value = getattr(config.tracking, name)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"tracking.{name} must be between 0 and 1")
    for name in ("watchdog_seconds", "startup_timeout_seconds", "shutdown_timeout_seconds"):
        if getattr(config.tracking, name) <= 0:
            raise ValueError(f"tracking.{name} must be positive")
    if not 0.0 < config.gestures.pointer_smoothing <= 1.0:
        raise ValueError("gestures.pointer_smoothing must be in (0, 1]")
    positive = (
        "stability_seconds",
        "arm_hold_seconds",
        "pause_hold_seconds",
        "hold_motion_limit_palms",
        "lost_hand_grace_seconds",
        "auto_pause_seconds",
        "extended_length_ratio",
        "finger_reach_ratio",
        "curled_length_ratio",
        "curled_tip_reach_ratio",
        "pinch_threshold_palms",
        "min_palm_size",
        "max_observation_gap_seconds",
        "pointer_max_step_palms",
        "scroll_notches_per_palm",
        "max_scroll_notches_per_frame",
        "swipe_threshold_palms",
        "swipe_axis_ratio",
        "swipe_max_seconds",
    )
    for name in positive:
        if getattr(config.gestures, name) <= 0:
            raise ValueError(f"gestures.{name} must be positive")
    for name in (
        "extended_finger_angle",
        "extended_dip_angle",
        "curled_finger_angle",
        "curled_dip_angle",
    ):
        value = getattr(config.gestures, name)
        if not 0.0 < value <= 180.0:
            raise ValueError(f"gestures.{name} must be in (0, 180]")
    for name in ("extended_length_ratio", "curled_length_ratio"):
        value = getattr(config.gestures, name)
        if not 0.0 < value <= 1.0:
            raise ValueError(f"gestures.{name} must be in (0, 1]")
    if not 0.0 <= config.gestures.min_palm_width_ratio <= 2.0:
        raise ValueError("gestures.min_palm_width_ratio must be between 0 and 2")
    if not 0.0 <= config.gestures.min_palm_orientation <= 2.0:
        raise ValueError("gestures.min_palm_orientation must be between 0 and 2")
    if config.gestures.pointer_deadzone_palms < 0:
        raise ValueError("gestures.pointer_deadzone_palms cannot be negative")
    if config.input.pointer_pixels_per_palm <= 0:
        raise ValueError("input.pointer_pixels_per_palm must be positive")


def load_config(path: str | Path | None = None) -> AppConfig:
    config = AppConfig.defaults()
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        try:
            values = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {config_path}: {exc}") from exc
        if not isinstance(values, dict):
            raise ValueError("The configuration file must contain a JSON object")
        _merge_dataclass(config, values)
    _validate(config)
    return config
