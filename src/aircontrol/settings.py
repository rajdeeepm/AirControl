from __future__ import annotations

import math
from typing import Any


DEFAULTS: dict[str, Any] = {
    "sensitivity": 50,
    "smoothing": 64,
    "cursor_speed": 1.0,
    "dominant_hand": "right",
    "theme": "system",
    "airy_enabled": True,
    "airy_feedback_level": "full",
    "airy_sounds": False,
    "airy_animation_intensity": 80,
    "airy_size": "medium",
    "airy_on_top": True,
}


def validate(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise ValueError(f"unknown app setting: {key}")

    if key in {"sensitivity", "smoothing"}:
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError(f"{key} must be an integer between 0 and 100")
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

    if key == "dominant_hand":
        if not isinstance(value, str) or value not in {"left", "right"}:
            raise ValueError("dominant_hand must be 'left' or 'right'")
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


def load(store: Any) -> dict[str, Any]:
    settings = dict(DEFAULTS)
    for key, persisted_value in store.app_settings.all().items():
        if key not in DEFAULTS:
            continue
        try:
            settings[key] = validate(key, persisted_value)
        except ValueError:
            default = DEFAULTS[key]
            store.app_settings.set(key, default)
            settings[key] = default
    return settings


def gate_t1(sensitivity: int) -> float:
    return 0.95 - sensitivity / 100 * 0.35


def pointer_alpha(smoothing: int) -> float:
    return max(0.05, 1.0 - smoothing / 100)


def pointer_pixels(base_pixels: float, cursor_speed: float) -> float:
    return base_pixels * cursor_speed


__all__ = [
    "DEFAULTS",
    "gate_t1",
    "load",
    "pointer_alpha",
    "pointer_pixels",
    "validate",
]
