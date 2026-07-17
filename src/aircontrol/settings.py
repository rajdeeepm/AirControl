from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from aircontrol.config import GestureConfig


_GESTURE_DEFAULTS = GestureConfig()
_POINTER_RESPONSIVENESS_RANGE = (0, 100)
_POINTER_RESPONSIVENESS_DEFAULT = round(_GESTURE_DEFAULTS.pointer_beta * 1000)
_CLICK_ENGAGE_RANGE = (0.20, 0.80)
_CLICK_RELEASE_RANGE = (0.30, 1.20)
_PINCH_APPROACH_RANGE = (0.43, 1.50)
_PINCH_DRAG_RELEASE_RANGE = (0.01, 0.50)
_HOLD_SECONDS_RANGE = (0.10, 3.0)
_SCROLL_SPEED_RANGE = (0, 100)
_SCROLL_SPEED_DEFAULT = round(
    (_GESTURE_DEFAULTS.scroll_notches_per_palm - 2.0) * 10
)
_SWIPE_DISTANCE_RANGE = (0.30, 2.0)

DEFAULTS: dict[str, Any] = {
    "sensitivity": 50,
    "smoothing": 64,
    "cursor_speed": 1.0,
    # 20 / 1000 preserves GestureConfig.pointer_beta's existing 0.02 default.
    "pointer_responsiveness": _POINTER_RESPONSIVENESS_DEFAULT,
    "click_engage": _GESTURE_DEFAULTS.click_engage_palms,
    "click_release": _GESTURE_DEFAULTS.click_release_palms,
    "pinch_approach": _GESTURE_DEFAULTS.pinch_approach_palms,
    "pinch_drag_release": _GESTURE_DEFAULTS.pinch_drag_release_palms,
    "arm_hold_seconds": _GESTURE_DEFAULTS.arm_hold_seconds,
    "pause_hold_seconds": _GESTURE_DEFAULTS.pause_hold_seconds,
    # 2 + 50 / 10 preserves the existing 7 notches-per-palm default.
    "scroll_speed": _SCROLL_SPEED_DEFAULT,
    "swipe_distance": _GESTURE_DEFAULTS.swipe_threshold_palms,
    "dominant_hand": "right",
    "click_mode": "single",
    "theme": "system",
    "airy_enabled": True,
    "airy_feedback_level": "full",
    "airy_sounds": False,
    "airy_animation_intensity": 80,
    "airy_size": "medium",
    "airy_on_top": True,
}


def _number_in_range(
    key: str,
    value: Any,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{key} must be a number between {minimum} and {maximum}")
    return float(value)


def _validate_value(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise ValueError(f"unknown app setting: {key}")

    if key in {
        "sensitivity",
        "smoothing",
        "pointer_responsiveness",
        "scroll_speed",
    }:
        minimum, maximum = (
            _POINTER_RESPONSIVENESS_RANGE
            if key == "pointer_responsiveness"
            else _SCROLL_SPEED_RANGE
            if key == "scroll_speed"
            else (0, 100)
        )
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(
                f"{key} must be an integer between {minimum} and {maximum}"
            )
        return value

    if key == "cursor_speed":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0.25 <= value <= 3.0
        ):
            raise ValueError("cursor_speed must be a number between 0.25 and 3.0")
        return float(value)

    advanced_ranges = {
        "click_engage": _CLICK_ENGAGE_RANGE,
        "click_release": _CLICK_RELEASE_RANGE,
        "pinch_approach": _PINCH_APPROACH_RANGE,
        "pinch_drag_release": _PINCH_DRAG_RELEASE_RANGE,
        "arm_hold_seconds": _HOLD_SECONDS_RANGE,
        "pause_hold_seconds": _HOLD_SECONDS_RANGE,
        "swipe_distance": _SWIPE_DISTANCE_RANGE,
    }
    if key in advanced_ranges:
        minimum, maximum = advanced_ranges[key]
        normalized = _number_in_range(key, value, minimum, maximum)
        if (
            key == "pinch_approach"
            and normalized <= _GESTURE_DEFAULTS.pinch_threshold_palms
        ):
            raise ValueError(
                "pinch_approach must be greater than the pinch fire threshold"
            )
        return normalized

    if key == "dominant_hand":
        if not isinstance(value, str) or value not in {"left", "right"}:
            raise ValueError("dominant_hand must be 'left' or 'right'")
        return value

    if key == "click_mode":
        if not isinstance(value, str) or value not in {"single", "two_hand"}:
            raise ValueError("click_mode must be 'single' or 'two_hand'")
        return value

    if key == "theme":
        if not isinstance(value, str) or value not in {"light", "dark", "system"}:
            raise ValueError("theme must be 'light', 'dark', or 'system'")
        return value

    if key == "airy_feedback_level":
        if not isinstance(value, str) or value not in {
            "full",
            "subtle",
            "minimal",
            "hidden",
        }:
            raise ValueError(
                "airy_feedback_level must be 'full', 'subtle', 'minimal', or 'hidden'"
            )
        return value

    if key == "airy_animation_intensity":
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError(
                "airy_animation_intensity must be an integer between 0 and 100"
            )
        return value

    if key == "airy_size":
        if not isinstance(value, str) or value not in {"small", "medium", "large"}:
            raise ValueError("airy_size must be 'small', 'medium', or 'large'")
        return value

    if key in {"airy_enabled", "airy_sounds", "airy_on_top"}:
        if type(value) is not bool:
            raise ValueError(f"{key} must be a boolean")
        return value

    raise AssertionError(f"missing validator for app setting: {key}")


def _validate_click_order(settings: Mapping[str, Any]) -> None:
    engage = settings["click_engage"]
    release = settings["click_release"]
    if engage >= release:
        raise ValueError("click_engage must be less than click_release")


def validate(
    key: str,
    value: Any,
    *,
    settings: Mapping[str, Any] | None = None,
) -> Any:
    """Validate one update, using current settings for pairwise constraints."""
    normalized = _validate_value(key, value)
    if key in {"click_engage", "click_release"}:
        candidate = dict(DEFAULTS)
        if settings is not None:
            candidate.update(settings)
        candidate[key] = normalized
        try:
            _validate_click_order(candidate)
        except ValueError as exc:
            if key == "click_release":
                raise ValueError(
                    "click_release must be greater than click_engage"
                ) from exc
            raise
    return normalized


def load(store: Any) -> dict[str, Any]:
    settings = dict(DEFAULTS)
    persisted = store.app_settings.all()
    for key, persisted_value in persisted.items():
        if key not in DEFAULTS:
            continue
        try:
            settings[key] = _validate_value(key, persisted_value)
        except ValueError:
            default = DEFAULTS[key]
            store.app_settings.set(key, default)
            settings[key] = default
    try:
        _validate_click_order(settings)
    except ValueError:
        for key in ("click_engage", "click_release"):
            default = DEFAULTS[key]
            store.app_settings.set(key, default)
            settings[key] = default
    return settings


def reset(store: Any) -> dict[str, Any]:
    """Persist and return a fresh copy of every app-setting default."""
    for key, value in DEFAULTS.items():
        store.app_settings.set(key, value)
    return dict(DEFAULTS)


def gate_t1(sensitivity: int) -> float:
    return 0.95 - sensitivity / 100 * 0.35


def pointer_min_cutoff(smoothing: int) -> float:
    return 2.0 - 1.5 * (smoothing / 100)


def pointer_pixels(base_pixels: float, cursor_speed: float) -> float:
    return base_pixels * cursor_speed


def pointer_beta(pointer_responsiveness: int) -> float:
    """Map 0..100 responsiveness monotonically to One-Euro beta 0..0.10."""
    return pointer_responsiveness / 1000.0


def scroll_notches_per_palm(scroll_speed: int) -> float:
    """Map 0..100 scroll speed monotonically to 2..12 notches per palm."""
    return 2.0 + scroll_speed / 10.0


def gesture_config_updates(settings: Mapping[str, Any]) -> dict[str, float]:
    """Translate validated UI settings into live GestureConfig field values."""
    return {
        "pointer_min_cutoff": pointer_min_cutoff(settings["smoothing"]),
        "pointer_beta": pointer_beta(settings["pointer_responsiveness"]),
        "click_engage_palms": settings["click_engage"],
        "click_release_palms": settings["click_release"],
        "pinch_approach_palms": settings["pinch_approach"],
        "pinch_drag_release_palms": settings["pinch_drag_release"],
        "arm_hold_seconds": settings["arm_hold_seconds"],
        "pause_hold_seconds": settings["pause_hold_seconds"],
        "scroll_notches_per_palm": scroll_notches_per_palm(
            settings["scroll_speed"]
        ),
        "swipe_threshold_palms": settings["swipe_distance"],
    }


__all__ = [
    "DEFAULTS",
    "gate_t1",
    "gesture_config_updates",
    "load",
    "pointer_beta",
    "pointer_min_cutoff",
    "pointer_pixels",
    "reset",
    "scroll_notches_per_palm",
    "validate",
]
