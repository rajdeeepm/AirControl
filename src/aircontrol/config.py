from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

from aircontrol.resources import is_frozen, resource_dir, user_data_dir


DEFAULT_CONFIG_NAME = "config.json"


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
    click_engage_palms: float = 0.45
    click_release_palms: float = 0.60
    pinch_approach_palms: float = 0.75
    pinch_drag_release_palms: float = 0.08
    min_palm_size: float = 0.035
    min_palm_width_ratio: float = 0.16
    min_palm_orientation: float = 0.08
    require_palm_facing: bool = True
    max_observation_gap_seconds: float = 0.2
    pointer_min_cutoff: float = 1.0
    pointer_beta: float = 0.02
    pointer_dcutoff: float = 1.0
    # Retained so existing config.json files remain loadable. Pointer motion
    # now uses the One-Euro parameters above and does not consult these knobs.
    pointer_smoothing: float = 0.36
    pointer_deadzone_palms: float = 0.018
    pointer_max_step_palms: float = 0.2
    scroll_notches_per_palm: float = 7.0
    max_scroll_notches_per_frame: float = 3.0
    natural_scroll: bool = True
    swipe_threshold_palms: float = 0.9
    swipe_axis_ratio: float = 1.6
    swipe_max_seconds: float = 0.8
    swipe_rest_epsilon_palms: float = 0.15
    fold_reach_ratio: float = 0.88
    scroll_separation_ratio: float = 1.3
    fist_reach_max: float = 1.2


@dataclass(slots=True)
class InputConfig:
    pointer_pixels_per_palm: float = 760.0
    allow_risky_hotkeys: bool = False


@dataclass(slots=True)
class DisplayConfig:
    window_name: str = "AirControl — Local Gesture HUD"
    preview_width: int = 640
    always_on_top: bool = True
    show_landmarks: bool = True


@dataclass(slots=True)
class PipelineConfig:
    resample_length: int = 45
    buffer_capacity: int = 75


@dataclass(slots=True)
class StoreConfig:
    db_path: str = ""


@dataclass(slots=True)
class MetricsConfig:
    cpu_sampling: bool = True


@dataclass(slots=True)
class IpcConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass(frozen=True, slots=True)
class ClutchConfig:
    mode: str = "wake_pose"
    hold_seconds: float = 0.4
    window_seconds: float | None = None
    plane_y: float = 0.5
    acknowledged_expert_mode: bool = False


@dataclass(slots=True)
class CalibrationConfig:
    negative_seconds: float = 30.0
    snapshot_seconds: float = 3.0
    motion_reps: int = 2


@dataclass(slots=True)
class AppConfig:
    camera: CameraConfig
    tracking: TrackingConfig
    gestures: GestureConfig
    input: InputConfig
    display: DisplayConfig
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    ipc: IpcConfig = field(default_factory=IpcConfig)
    clutch: ClutchConfig = field(default_factory=ClutchConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)

    @classmethod
    def defaults(cls) -> "AppConfig":
        return cls(
            camera=CameraConfig(),
            tracking=TrackingConfig(),
            gestures=GestureConfig(),
            input=InputConfig(),
            display=DisplayConfig(),
        )


def _merge_dataclass(target: Any, values: dict[str, Any], prefix: str = "") -> Any:
    known = {field.name for field in fields(target)}
    unknown = set(values) - known
    if unknown:
        names = ", ".join(sorted(f"{prefix}{name}" for name in unknown))
        raise ValueError(f"Unknown configuration setting(s): {names}")

    frozen = type(target).__dataclass_params__.frozen
    replacements: dict[str, Any] = {}
    for name, value in values.items():
        current = getattr(target, name)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"Configuration section '{prefix}{name}' must be an object")
            merged = _merge_dataclass(current, value, prefix=f"{prefix}{name}.")
            if frozen:
                replacements[name] = merged
            elif merged is not current:
                setattr(target, name, merged)
        elif frozen:
            replacements[name] = value
        else:
            setattr(target, name, value)
    if frozen:
        return replace(target, **replacements)
    return target


def _validate(config: AppConfig) -> None:
    if config.camera.width < 320 or config.camera.height < 240:
        raise ValueError("Camera resolution must be at least 320×240")
    if config.camera.fps <= 0:
        raise ValueError("camera.fps must be positive")
    max_hands = config.tracking.max_hands
    if (
        isinstance(max_hands, bool)
        or not isinstance(max_hands, int)
        or max_hands not in (1, 2)
    ):
        raise ValueError("tracking.max_hands must be either 1 or 2")
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
    for name in (
        "pointer_min_cutoff",
        "pointer_beta",
        "pointer_dcutoff",
        "click_engage_palms",
        "click_release_palms",
    ):
        value = getattr(config.gestures, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"gestures.{name} must be finite")
    if config.gestures.pointer_min_cutoff <= 0:
        raise ValueError("gestures.pointer_min_cutoff must be positive")
    if config.gestures.pointer_beta < 0:
        raise ValueError("gestures.pointer_beta cannot be negative")
    if config.gestures.pointer_dcutoff <= 0:
        raise ValueError("gestures.pointer_dcutoff must be positive")
    if config.gestures.click_engage_palms <= 0:
        raise ValueError("gestures.click_engage_palms must be positive")
    if config.gestures.click_release_palms <= 0:
        raise ValueError("gestures.click_release_palms must be positive")
    if not (
        config.gestures.click_engage_palms
        < config.gestures.click_release_palms
    ):
        raise ValueError(
            "gestures.click_engage_palms must be less than "
            "gestures.click_release_palms"
        )
    for name in (
        "arm_hold_seconds",
        "pause_hold_seconds",
        "scroll_notches_per_palm",
        "swipe_threshold_palms",
    ):
        value = getattr(config.gestures, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"gestures.{name} must be positive and finite")
    positive = (
        "stability_seconds",
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
        "max_scroll_notches_per_frame",
        "swipe_axis_ratio",
        "swipe_max_seconds",
        "swipe_rest_epsilon_palms",
    )
    for name in positive:
        if getattr(config.gestures, name) <= 0:
            raise ValueError(f"gestures.{name} must be positive")
    for name in ("pinch_approach_palms", "pinch_drag_release_palms"):
        value = getattr(config.gestures, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"gestures.{name} must be positive and finite")
    if not (
        config.gestures.pinch_approach_palms
        > config.gestures.pinch_threshold_palms
    ):
        raise ValueError(
            "gestures.pinch_approach_palms must be greater than "
            "gestures.pinch_threshold_palms"
        )
    for name in (
        "extended_finger_angle",
        "extended_dip_angle",
        "curled_finger_angle",
        "curled_dip_angle",
    ):
        value = getattr(config.gestures, name)
        if not 0.0 < value <= 180.0:
            raise ValueError(f"gestures.{name} must be in (0, 180]")
    for name in ("extended_length_ratio", "curled_length_ratio", "fold_reach_ratio"):
        value = getattr(config.gestures, name)
        if not 0.0 < value <= 1.0:
            raise ValueError(f"gestures.{name} must be in (0, 1]")
    if not config.gestures.scroll_separation_ratio > 1.0:
        raise ValueError("gestures.scroll_separation_ratio must be greater than 1")
    if not 0.0 < config.gestures.fist_reach_max <= 2.0:
        raise ValueError("gestures.fist_reach_max must be in (0, 2]")
    if not 0.0 <= config.gestures.min_palm_width_ratio <= 2.0:
        raise ValueError("gestures.min_palm_width_ratio must be between 0 and 2")
    if not 0.0 <= config.gestures.min_palm_orientation <= 2.0:
        raise ValueError("gestures.min_palm_orientation must be between 0 and 2")
    if config.gestures.pointer_deadzone_palms < 0:
        raise ValueError("gestures.pointer_deadzone_palms cannot be negative")
    if config.input.pointer_pixels_per_palm <= 0:
        raise ValueError("input.pointer_pixels_per_palm must be positive")
    if type(config.input.allow_risky_hotkeys) is not bool:
        raise ValueError("input.allow_risky_hotkeys must be a boolean")
    for name in ("resample_length", "buffer_capacity"):
        value = getattr(config.pipeline, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"pipeline.{name} must be an integer >= 1")
    if (
        isinstance(config.ipc.port, bool)
        or not isinstance(config.ipc.port, int)
        or not 0 <= config.ipc.port <= 65535
    ):
        raise ValueError("ipc.port must be an integer between 0 and 65535")
    if config.ipc.host != "127.0.0.1":
        raise ValueError("ipc.host must be '127.0.0.1'")
    if config.clutch.mode not in {"wake_pose", "spatial_zone", "always_on"}:
        raise ValueError(
            "clutch.mode must be one of: wake_pose, spatial_zone, always_on"
        )
    if (
        config.clutch.mode == "always_on"
        and config.clutch.acknowledged_expert_mode is not True
    ):
        raise ValueError(
            "clutch.acknowledged_expert_mode must be true for always_on mode"
        )
    if config.clutch.hold_seconds <= 0:
        raise ValueError("clutch.hold_seconds must be positive")
    window_seconds = config.clutch.window_seconds
    if window_seconds is not None and (
        isinstance(window_seconds, bool)
        or not isinstance(window_seconds, (int, float))
        or not math.isfinite(window_seconds)
        or window_seconds <= 0
    ):
        raise ValueError("clutch.window_seconds must be positive when set")
    if not 0.0 <= config.clutch.plane_y <= 1.0:
        raise ValueError("clutch.plane_y must be between 0 and 1")
    for name in ("negative_seconds", "snapshot_seconds"):
        value = getattr(config.calibration, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"calibration.{name} must be positive")
    if (
        isinstance(config.calibration.motion_reps, bool)
        or not isinstance(config.calibration.motion_reps, int)
        or config.calibration.motion_reps < 1
    ):
        raise ValueError("calibration.motion_reps must be a positive integer")


def resolve_config_path(path: str | Path = DEFAULT_CONFIG_NAME) -> Path:
    """Resolve the active config, seeding a writable copy when packaged."""
    requested = Path(path)
    if not is_frozen() or requested != Path(DEFAULT_CONFIG_NAME):
        return requested.resolve()

    destination = user_data_dir() / DEFAULT_CONFIG_NAME
    if not destination.exists():
        bundled_default = resource_dir() / DEFAULT_CONFIG_NAME
        if not bundled_default.is_file():
            raise FileNotFoundError(
                f"Bundled configuration file not found: {bundled_default}"
            )
        shutil.copyfile(bundled_default, destination)
    return destination


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
