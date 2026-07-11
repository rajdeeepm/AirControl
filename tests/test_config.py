import json

import pytest

from aircontrol.config import load_config


def test_partial_configuration_uses_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"camera": {"index": 2}, "gestures": {"natural_scroll": False}}))

    config = load_config(path)

    assert config.camera.index == 2
    assert config.camera.width == 960
    assert config.gestures.natural_scroll is False


def test_unknown_configuration_key_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"gestures": {"mystery_knob": 12}}))

    with pytest.raises(ValueError, match="gestures.mystery_knob"):
        load_config(path)


@pytest.mark.parametrize(
    "gesture_values",
    [
        {"extended_finger_angle": 181},
        {"extended_dip_angle": 0},
        {"pointer_deadzone_palms": -0.1},
        {"min_palm_width_ratio": 3.0},
    ],
)
def test_invalid_gesture_thresholds_are_rejected(tmp_path, gesture_values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"gestures": gesture_values}))

    with pytest.raises(ValueError):
        load_config(path)
