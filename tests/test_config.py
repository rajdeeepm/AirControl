import json

import pytest

from aircontrol.config import AppConfig, load_config


def test_partial_configuration_uses_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"camera": {"index": 2}, "gestures": {"natural_scroll": False}}))

    config = load_config(path)

    assert config.camera.index == 2
    assert config.camera.width == 960
    assert config.gestures.natural_scroll is False


def test_new_sections_have_defaults_for_existing_config_files(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"camera": {"index": 2}}))

    config = load_config(path)

    assert config.pipeline.resample_length == 45
    assert config.pipeline.buffer_capacity == 75
    assert config.store.db_path == ""
    assert config.metrics.cpu_sampling is True
    assert config.ipc.enabled is False
    assert config.ipc.host == "127.0.0.1"
    assert config.ipc.port == 8787


def test_app_config_new_sections_have_default_factories():
    defaults = AppConfig.defaults()
    config = AppConfig(
        camera=defaults.camera,
        tracking=defaults.tracking,
        gestures=defaults.gestures,
        input=defaults.input,
        display=defaults.display,
    )

    assert config.pipeline.resample_length == 45
    assert config.store.db_path == ""
    assert config.metrics.cpu_sampling is True
    assert config.ipc.port == 8787


def test_tracking_max_hands_defaults_to_one():
    assert AppConfig.defaults().tracking.max_hands == 1


def test_pointer_filter_and_click_hysteresis_defaults_are_ordered():
    gestures = AppConfig.defaults().gestures

    assert gestures.pointer_min_cutoff == pytest.approx(1.0)
    assert gestures.pointer_beta == pytest.approx(0.02)
    assert gestures.pointer_dcutoff == pytest.approx(1.0)
    assert gestures.click_engage_palms == pytest.approx(0.45)
    assert gestures.click_release_palms == pytest.approx(0.60)
    assert gestures.click_engage_palms < gestures.click_release_palms


@pytest.mark.parametrize("max_hands", [1, 2])
def test_tracking_max_hands_accepts_one_or_two(tmp_path, max_hands):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tracking": {"max_hands": max_hands}}))

    config = load_config(path)

    assert config.tracking.max_hands == max_hands


@pytest.mark.parametrize(
    "max_hands",
    [0, 3, -1, True, 1.0, 2.0, "2", None],
)
def test_tracking_max_hands_rejects_other_values(tmp_path, max_hands):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tracking": {"max_hands": max_hands}}))

    with pytest.raises(ValueError, match="tracking.max_hands"):
        load_config(path)


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


@pytest.mark.parametrize(
    "gesture_values",
    [
        {"pointer_min_cutoff": 0.0},
        {"pointer_min_cutoff": float("nan")},
        {"pointer_min_cutoff": float("inf")},
        {"pointer_min_cutoff": True},
        {"pointer_beta": -0.01},
        {"pointer_beta": float("nan")},
        {"pointer_beta": float("inf")},
        {"pointer_beta": False},
        {"pointer_dcutoff": 0.0},
        {"pointer_dcutoff": float("nan")},
        {"pointer_dcutoff": float("inf")},
        {"pointer_dcutoff": True},
        {"click_engage_palms": 0.0},
        {"click_engage_palms": float("nan")},
        {"click_engage_palms": float("inf")},
        {"click_engage_palms": True},
        {"click_release_palms": 0.0},
        {"click_release_palms": float("nan")},
        {"click_release_palms": float("inf")},
        {"click_release_palms": True},
        {"click_engage_palms": 0.60, "click_release_palms": 0.60},
        {"click_engage_palms": 0.61, "click_release_palms": 0.60},
    ],
)
def test_invalid_pointer_filter_and_click_hysteresis_is_rejected(
    tmp_path,
    gesture_values,
):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"gestures": gesture_values}))

    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize(
    ("section", "values"),
    [
        ("pipeline", {"resample_length": 0}),
        ("pipeline", {"resample_length": 1.5}),
        ("pipeline", {"buffer_capacity": 0}),
        ("pipeline", {"buffer_capacity": True}),
        ("ipc", {"port": -1}),
        ("ipc", {"port": 65536}),
        ("ipc", {"port": 1.5}),
        ("ipc", {"host": "0.0.0.0"}),
    ],
)
def test_invalid_pipeline_and_ipc_settings_are_rejected(tmp_path, section, values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({section: values}))

    with pytest.raises(ValueError):
        load_config(path)


def test_pipeline_and_ephemeral_ipc_port_settings_are_merged(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "pipeline": {"resample_length": 60, "buffer_capacity": 90},
                "store": {"db_path": ":memory:"},
                "metrics": {"cpu_sampling": False},
                "ipc": {"enabled": True, "port": 0},
            }
        )
    )

    config = load_config(path)

    assert config.pipeline.resample_length == 60
    assert config.pipeline.buffer_capacity == 90
    assert config.store.db_path == ":memory:"
    assert config.metrics.cpu_sampling is False
    assert config.ipc.enabled is True
    assert config.ipc.port == 0
