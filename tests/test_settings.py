from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from aircontrol.config import AppConfig, load_config
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.ipc import (
    FakeTransport,
    IpcProtocolError,
    ack_event,
    app_settings_event,
    parse_command,
)
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline
from aircontrol.settings import (
    DEFAULTS,
    gate_t1,
    gesture_config_updates,
    load,
    pointer_beta,
    pointer_min_cutoff,
    pointer_pixels,
    reset,
    scroll_notches_per_palm,
    validate,
)
from aircontrol.store import AppSettingsRepo, Store


def _settings(**overrides: Any) -> dict[str, Any]:
    values = dict(DEFAULTS)
    values.update(overrides)
    return values


def _wire_command(name: str, **fields: Any) -> dict[str, Any]:
    return {"v": 1, "type": "command", "name": name, **fields}


def _make_pipeline(
    *,
    settings: dict[str, Any] | None = None,
) -> tuple[Pipeline, float]:
    config = AppConfig.defaults()
    base_pixels = config.input.pointer_pixels_per_palm
    pipeline = Pipeline(
        config,
        ActionController(base_pixels, practice=True),
        store=None,
        metrics=Metrics(),
        gate=ConfidenceGate(
            GateThresholds(
                t1_top1=0.91,
                t2_margin=0.12,
                t3_incidental=0.34,
            )
        ),
        settings=settings,
    )
    return pipeline, base_pixels


def _make_daemon(store: Store, *, ipc: object | None = None) -> Daemon:
    config = AppConfig.defaults()
    return Daemon(
        config,
        practice=True,
        controller=ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        ),
        store=store,
        ipc=ipc,
    )


def test_setting_derivations() -> None:
    assert gate_t1(0) == 0.95
    assert gate_t1(50) == pytest.approx(0.775)
    assert gate_t1(100) == pytest.approx(0.60)

    assert pointer_min_cutoff(0) == pytest.approx(2.0)
    assert pointer_min_cutoff(100) == pytest.approx(0.5)
    assert pointer_min_cutoff(64) == pytest.approx(1.04)

    assert pointer_pixels(760, 2.0) == 1520

    assert pointer_beta(0) == 0.0
    assert pointer_beta(20) == pytest.approx(0.02)
    assert pointer_beta(100) == pytest.approx(0.10)

    assert scroll_notches_per_palm(0) == pytest.approx(2.0)
    assert scroll_notches_per_palm(50) == pytest.approx(7.0)
    assert scroll_notches_per_palm(100) == pytest.approx(12.0)


def test_advanced_setting_defaults_match_gesture_config_defaults() -> None:
    gesture_defaults = AppConfig.defaults().gestures

    assert {
        key: DEFAULTS[key]
        for key in (
            "pointer_responsiveness",
            "click_engage",
            "click_release",
            "pinch_approach",
            "pinch_drag_release",
            "arm_hold_seconds",
            "pause_hold_seconds",
            "scroll_speed",
            "swipe_distance",
        )
    } == {
        "pointer_responsiveness": 20,
        "click_engage": gesture_defaults.click_engage_palms,
        "click_release": gesture_defaults.click_release_palms,
        "pinch_approach": gesture_defaults.pinch_approach_palms,
        "pinch_drag_release": gesture_defaults.pinch_drag_release_palms,
        "arm_hold_seconds": gesture_defaults.arm_hold_seconds,
        "pause_hold_seconds": gesture_defaults.pause_hold_seconds,
        "scroll_speed": 50,
        "swipe_distance": gesture_defaults.swipe_threshold_palms,
    }

    updates = gesture_config_updates(dict(DEFAULTS))
    assert updates == {
        "pointer_min_cutoff": pytest.approx(pointer_min_cutoff(DEFAULTS["smoothing"])),
        "pointer_beta": pytest.approx(gesture_defaults.pointer_beta),
        "click_engage_palms": pytest.approx(gesture_defaults.click_engage_palms),
        "click_release_palms": pytest.approx(gesture_defaults.click_release_palms),
        "pinch_approach_palms": pytest.approx(gesture_defaults.pinch_approach_palms),
        "pinch_drag_release_palms": pytest.approx(
            gesture_defaults.pinch_drag_release_palms
        ),
        "arm_hold_seconds": pytest.approx(gesture_defaults.arm_hold_seconds),
        "pause_hold_seconds": pytest.approx(gesture_defaults.pause_hold_seconds),
        "scroll_notches_per_palm": pytest.approx(
            gesture_defaults.scroll_notches_per_palm
        ),
        "swipe_threshold_palms": pytest.approx(
            gesture_defaults.swipe_threshold_palms
        ),
    }


def test_airy_setting_defaults() -> None:
    assert {
        key: DEFAULTS[key]
        for key in (
            "airy_enabled",
            "airy_feedback_level",
            "airy_sounds",
            "airy_animation_intensity",
            "airy_size",
            "airy_on_top",
        )
    } == {
        "airy_enabled": True,
        "airy_feedback_level": "full",
        "airy_sounds": False,
        "airy_animation_intensity": 80,
        "airy_size": "medium",
        "airy_on_top": True,
    }


def test_click_mode_defaults_to_single() -> None:
    assert DEFAULTS["click_mode"] == "single"
    assert DEFAULTS["drag_lock_enabled"] is True
    assert validate("drag_lock_enabled", False) is False

    with Store(":memory:") as store:
        assert load(store)["click_mode"] == "single"


@pytest.mark.parametrize(("key", "value"), DEFAULTS.items())
def test_validate_accepts_every_default(key: str, value: Any) -> None:
    validated = validate(key, value)

    assert validated == value
    assert type(validated) is type(value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("airy_feedback_level", "full"),
        ("airy_feedback_level", "subtle"),
        ("airy_feedback_level", "minimal"),
        ("airy_feedback_level", "hidden"),
        ("airy_animation_intensity", 0),
        ("airy_animation_intensity", 100),
        ("airy_size", "small"),
        ("airy_size", "medium"),
        ("airy_size", "large"),
    ],
)
def test_validate_accepts_airy_setting_boundaries_and_choices(
    key: str,
    value: Any,
) -> None:
    assert validate(key, value) == value


@pytest.mark.parametrize("value", ["single", "two_hand"])
def test_validate_accepts_click_modes(value: str) -> None:
    assert validate("click_mode", value) == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("pointer_responsiveness", 0),
        ("pointer_responsiveness", 100),
        ("click_engage", 0.20),
        ("click_engage", 0.59),
        ("click_release", 0.46),
        ("click_release", 1.20),
        ("pinch_approach", 0.43),
        ("pinch_approach", 1.50),
        ("pinch_drag_release", 0.01),
        ("pinch_drag_release", 0.50),
        ("arm_hold_seconds", 0.10),
        ("arm_hold_seconds", 3.0),
        ("pause_hold_seconds", 0.10),
        ("pause_hold_seconds", 3.0),
        ("scroll_speed", 0),
        ("scroll_speed", 100),
        ("swipe_distance", 0.30),
        ("swipe_distance", 2.0),
    ],
)
def test_validate_accepts_advanced_setting_boundaries(
    key: str,
    value: Any,
) -> None:
    assert validate(key, value) == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("click_engage", 0.20),
        ("click_release", 1),
        ("pinch_approach", 1),
        ("pinch_drag_release", 0.10),
        ("arm_hold_seconds", 1),
        ("pause_hold_seconds", 1),
        ("swipe_distance", 1),
    ],
)
def test_validate_normalizes_advanced_numbers_to_float(
    key: str,
    value: int | float,
) -> None:
    validated = validate(key, value)

    assert validated == float(value)
    assert type(validated) is float


def test_validate_enforces_click_threshold_order_for_an_update() -> None:
    current = _settings(click_engage=0.45, click_release=0.60)

    assert validate("click_engage", 0.59, settings=current) == 0.59
    assert validate("click_release", 0.46, settings=current) == 0.46
    assert validate(
        "click_engage",
        0.80,
        settings=_settings(click_release=0.90),
    ) == pytest.approx(0.80)
    assert validate(
        "click_release",
        0.30,
        settings=_settings(click_engage=0.20),
    ) == pytest.approx(0.30)
    with pytest.raises(ValueError, match="click_engage.*less than click_release"):
        validate("click_engage", 0.60, settings=current)
    with pytest.raises(ValueError, match="click_release.*greater than click_engage"):
        validate("click_release", 0.45, settings=current)


def test_validate_coerces_integer_cursor_speed_to_float() -> None:
    value = validate("cursor_speed", 2)

    assert value == 2.0
    assert type(value) is float


def test_validate_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown"):
        validate("not_a_setting", 1)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("sensitivity", 101),
        ("sensitivity", -1),
        ("sensitivity", "x"),
        ("sensitivity", True),
        ("smoothing", 101),
        ("smoothing", -1),
        ("smoothing", "x"),
        ("smoothing", True),
        ("cursor_speed", 0.1),
        ("cursor_speed", 5.0),
        ("cursor_speed", True),
        ("dominant_hand", "up"),
        ("click_mode", "dual"),
        ("click_mode", False),
        ("drag_lock_enabled", "yes"),
        ("drag_lock_enabled", 1),
        ("theme", "neon"),
        ("airy_enabled", "yes"),
        ("airy_feedback_level", "loud"),
        ("airy_feedback_level", False),
        ("airy_sounds", "yes"),
        ("airy_sounds", 0),
        ("airy_animation_intensity", -1),
        ("airy_animation_intensity", 101),
        ("airy_animation_intensity", 80.0),
        ("airy_animation_intensity", True),
        ("airy_size", "huge"),
        ("airy_size", 1),
        ("airy_on_top", "yes"),
        ("airy_on_top", 1),
    ],
)
def test_validate_rejects_invalid_values(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        validate(key, value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("pointer_responsiveness", -1),
        ("pointer_responsiveness", 101),
        ("pointer_responsiveness", 20.0),
        ("pointer_responsiveness", True),
        ("click_engage", 0.19),
        ("click_engage", 0.81),
        ("click_engage", float("nan")),
        ("click_release", 0.29),
        ("click_release", 1.21),
        ("click_release", True),
        ("pinch_approach", 0.42),
        ("pinch_approach", 1.51),
        ("pinch_approach", float("inf")),
        ("pinch_drag_release", 0.0),
        ("pinch_drag_release", 0.51),
        ("pinch_drag_release", False),
        ("arm_hold_seconds", 0.09),
        ("arm_hold_seconds", 3.01),
        ("arm_hold_seconds", float("nan")),
        ("pause_hold_seconds", 0.09),
        ("pause_hold_seconds", 3.01),
        ("pause_hold_seconds", True),
        ("scroll_speed", -1),
        ("scroll_speed", 101),
        ("scroll_speed", 50.0),
        ("scroll_speed", False),
        ("swipe_distance", 0.29),
        ("swipe_distance", 2.01),
        ("swipe_distance", float("-inf")),
    ],
)
def test_validate_rejects_invalid_advanced_values(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        validate(key, value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("arm_hold_seconds", True),
        ("arm_hold_seconds", float("inf")),
        ("pause_hold_seconds", False),
        ("pause_hold_seconds", float("nan")),
        ("scroll_notches_per_palm", True),
        ("scroll_notches_per_palm", float("inf")),
        ("swipe_threshold_palms", False),
        ("swipe_threshold_palms", float("nan")),
    ],
)
def test_config_rejects_invalid_ui_exposed_gesture_numbers(
    tmp_path,
    field: str,
    value: Any,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"gestures": {field: value}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"gestures\.{field}"):
        load_config(path)


def test_fresh_store_has_schema_v3_and_app_settings_round_trip(tmp_path) -> None:
    with Store(tmp_path / "aircontrol.db") as store:
        assert store.schema_version == 4
        assert isinstance(store.app_settings, AppSettingsRepo)
        assert store.app_settings.get("missing") is None

        store.app_settings.set("airy_enabled", False)
        store.app_settings.set("cursor_speed", 1.5)
        store.app_settings.set("cursor_speed", 2.0)

        assert store.app_settings.get("airy_enabled") is False
        assert store.app_settings.get("cursor_speed") == 2.0
        assert store.app_settings.all() == {
            "airy_enabled": False,
            "cursor_speed": 2.0,
        }


def test_delete_everything_clears_app_settings(tmp_path) -> None:
    with Store(tmp_path / "aircontrol.db") as store:
        store.app_settings.set("theme", "dark")

        store.delete_everything()

        assert store.app_settings.all() == {}
        assert store.schema_version == 4


def test_v2_database_upgrades_to_v3_without_losing_gestures(tmp_path) -> None:
    path = tmp_path / "aircontrol.db"
    now = 1_700_000_000.0
    gesture_row = (1, "Legacy Wave", "Preserve me", now, now)

    # A genuine v2 database predates both app_settings (added in v3) and the
    # gestures.kind column (added in v4), so it is built by hand here rather
    # than by rewinding a freshly migrated Store -- which would already have
    # the v4 column and make the migration below a no-op past v3.
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (2);

            CREATE TABLE gestures (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE exemplars (
                id INTEGER PRIMARY KEY,
                gesture_id INTEGER NOT NULL,
                trajectory BLOB NOT NULL,
                frame_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                handedness TEXT,
                FOREIGN KEY (gesture_id) REFERENCES gestures(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE mappings (
                id INTEGER PRIMARY KEY,
                gesture_id INTEGER NOT NULL,
                context TEXT NOT NULL DEFAULT 'global',
                action TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                UNIQUE(gesture_id, context),
                FOREIGN KEY (gesture_id) REFERENCES gestures(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE calibration_profiles (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                payload TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE gesture_stats (
                gesture_id INTEGER PRIMARY KEY
                    REFERENCES gestures(id) ON DELETE CASCADE,
                confirms INTEGER NOT NULL DEFAULT 0,
                rejects INTEGER NOT NULL DEFAULT 0,
                threshold_offset REAL NOT NULL DEFAULT 0.0,
                updated_at REAL NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO gestures (id, name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            gesture_row,
        )

    with Store(path) as upgraded:
        assert upgraded.schema_version == 4
        gesture = upgraded.gestures.get(1)
        assert gesture is not None
        assert gesture.name == "Legacy Wave"
        assert gesture.description == "Preserve me"
        assert gesture.kind == "motion"
        assert upgraded.app_settings.all() == {}

    with Store(path) as reopened:
        assert reopened.schema_version == 4
        assert reopened.gestures.get(1) == gesture


def test_load_returns_defaults_for_empty_store() -> None:
    with Store(":memory:") as store:
        loaded = load(store)

    assert loaded == DEFAULTS
    assert loaded is not DEFAULTS


def test_load_merges_persisted_values_over_defaults() -> None:
    with Store(":memory:") as store:
        store.app_settings.set("sensitivity", 82)
        store.app_settings.set("theme", "dark")
        store.app_settings.set("airy_feedback_level", "minimal")
        store.app_settings.set("airy_sounds", True)
        store.app_settings.set("click_mode", "two_hand")

        loaded = load(store)

    assert loaded == _settings(
        sensitivity=82,
        theme="dark",
        airy_feedback_level="minimal",
        airy_sounds=True,
        click_mode="two_hand",
    )


def test_load_ignores_unknown_and_repairs_invalid_persisted_values() -> None:
    with Store(":memory:") as store:
        store.app_settings.set("sensitivity", 101)
        store.app_settings.set("airy_animation_intensity", 101)
        store.app_settings.set("future_setting", "future-value")

        loaded = load(store)

        assert loaded == DEFAULTS
        assert store.app_settings.get("sensitivity") == DEFAULTS["sensitivity"]
        assert store.app_settings.get("airy_animation_intensity") == DEFAULTS[
            "airy_animation_intensity"
        ]


def test_load_accepts_jointly_valid_persisted_click_thresholds() -> None:
    with Store(":memory:") as store:
        store.app_settings.set("click_engage", 0.70)
        store.app_settings.set("click_release", 0.80)

        loaded = load(store)

    assert loaded == _settings(click_engage=0.70, click_release=0.80)


def test_load_repairs_inverted_persisted_click_thresholds() -> None:
    with Store(":memory:") as store:
        store.app_settings.set("click_engage", 0.70)
        store.app_settings.set("click_release", 0.65)

        loaded = load(store)

        assert loaded == DEFAULTS
        assert store.app_settings.get("click_engage") == DEFAULTS["click_engage"]
        assert store.app_settings.get("click_release") == DEFAULTS["click_release"]


def test_load_repairs_invalid_advanced_values() -> None:
    with Store(":memory:") as store:
        store.app_settings.set("pointer_responsiveness", 101)
        store.app_settings.set("pinch_approach", 0.42)
        store.app_settings.set("arm_hold_seconds", float("nan"))
        store.app_settings.set("scroll_speed", 50.0)

        loaded = load(store)

        assert loaded == DEFAULTS
        for key in (
            "pointer_responsiveness",
            "pinch_approach",
            "arm_hold_seconds",
            "scroll_speed",
        ):
            assert store.app_settings.get(key) == DEFAULTS[key]


def test_reset_persists_and_returns_fresh_defaults() -> None:
    with Store(":memory:") as store:
        store.app_settings.set("theme", "dark")
        store.app_settings.set("pointer_responsiveness", 80)
        store.app_settings.set("click_engage", 0.35)

        restored = reset(store)

        assert restored == DEFAULTS
        assert restored is not DEFAULTS
        assert store.app_settings.all() == DEFAULTS


def test_pipeline_apply_settings_updates_live_values_without_compounding() -> None:
    pipeline, base_pixels = _make_pipeline()
    values = _settings(sensitivity=100, smoothing=64, cursor_speed=2.0)
    try:
        assert pipeline.gate.thresholds == GateThresholds(
            t1_top1=0.91,
            t2_margin=0.12,
            t3_incidental=0.34,
        )
        assert pipeline.settings is None

        pipeline.apply_settings(values)

        assert pipeline.gate.thresholds.t1_top1 == pytest.approx(0.60)
        assert pipeline.gate.thresholds.t2_margin == 0.12
        assert pipeline.gate.thresholds.t3_incidental == 0.34
        assert pipeline.engine.config.pointer_min_cutoff == pytest.approx(1.04)
        assert pipeline.controller.pointer_pixels_per_palm == base_pixels * 2.0
        assert pipeline.settings == values

        pipeline.apply_settings(values)

        assert pipeline.controller.pointer_pixels_per_palm == base_pixels * 2.0
    finally:
        pipeline.release()


def test_pipeline_apply_settings_updates_advanced_gesture_config_live() -> None:
    pipeline, _ = _make_pipeline()
    values = _settings(
        pointer_responsiveness=80,
        click_engage=0.35,
        click_release=0.85,
        pinch_approach=1.10,
        pinch_drag_release=0.20,
        arm_hold_seconds=1.25,
        pause_hold_seconds=1.50,
        scroll_speed=90,
        swipe_distance=1.40,
    )
    try:
        pipeline.apply_settings(values)

        expected = {
            "pointer_beta": 0.08,
            "click_engage_palms": 0.35,
            "click_release_palms": 0.85,
            "pinch_approach_palms": 1.10,
            "pinch_drag_release_palms": 0.20,
            "arm_hold_seconds": 1.25,
            "pause_hold_seconds": 1.50,
            "scroll_notches_per_palm": 11.0,
            "swipe_threshold_palms": 1.40,
        }
        for field, value in expected.items():
            assert getattr(pipeline.engine.config, field) == pytest.approx(value)
            assert getattr(pipeline.config.gestures, field) == pytest.approx(value)
        assert pipeline.settings == values
    finally:
        pipeline.release()


def test_pipeline_applies_settings_at_construction() -> None:
    values = _settings(sensitivity=0, smoothing=100, cursor_speed=1.5)
    pipeline, base_pixels = _make_pipeline(settings=values)
    try:
        assert pipeline.gate.thresholds.t1_top1 == 0.95
        assert pipeline.engine.config.pointer_min_cutoff == pytest.approx(0.5)
        assert pipeline.controller.pointer_pixels_per_palm == base_pixels * 1.5
        assert pipeline.settings == values
    finally:
        pipeline.release()


def test_app_settings_event_contains_settings_and_echoes_id() -> None:
    values = _settings(theme="dark")

    assert app_settings_event(values, "settings-request") == {
        "v": 1,
        "type": "app_settings",
        "settings": values,
        "id": "settings-request",
    }


def test_daemon_get_app_settings_returns_defaults() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            events = daemon.command(
                {"name": "get_app_settings", "id": "get-settings"}
            )

            assert events == [
                app_settings_event(dict(DEFAULTS), "get-settings")
            ]
            assert daemon.pipeline.settings == DEFAULTS
            assert daemon.pipeline.gate.thresholds.t1_top1 == pytest.approx(0.775)
        finally:
            daemon.stop()


def test_daemon_set_app_setting_persists_applies_and_broadcasts(tmp_path) -> None:
    path = tmp_path / "aircontrol.db"
    store = Store(path)
    transport = FakeTransport()
    broadcasts: list[dict[str, Any]] = []
    transport.subscribe(broadcasts.append)
    daemon = _make_daemon(store, ipc=transport)
    try:
        events = daemon.command(
            {
                "name": "set_app_setting",
                "id": "set-sensitivity",
                "key": "sensitivity",
                "value": 100,
            }
        )

        assert events[0] == ack_event("set-sensitivity", True)
        assert events[1] == app_settings_event(
            _settings(sensitivity=100),
        )
        assert broadcasts == events
        assert store.app_settings.get("sensitivity") == 100
        assert daemon.pipeline.gate.thresholds.t1_top1 == pytest.approx(0.60)

        app_settings_broadcasts = sum(
            event["type"] == "app_settings" for event in broadcasts
        )
        invalid_events = daemon.command(
            {
                "name": "set_app_setting",
                "id": "invalid-sensitivity",
                "key": "sensitivity",
                "value": 101,
            }
        )

        assert len(invalid_events) == 1
        assert invalid_events[0]["type"] == "ack"
        assert invalid_events[0]["id"] == "invalid-sensitivity"
        assert invalid_events[0]["ok"] is False
        assert invalid_events[0]["error"]
        assert store.app_settings.get("sensitivity") == 100
        assert daemon.pipeline.gate.thresholds.t1_top1 == pytest.approx(0.60)
        assert sum(
            event["type"] == "app_settings" for event in broadcasts
        ) == app_settings_broadcasts
    finally:
        daemon.stop()
        store.close()

    with Store(path) as reopened:
        assert reopened.app_settings.get("sensitivity") == 100


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("airy_feedback_level", "subtle"),
        ("airy_sounds", True),
        ("airy_animation_intensity", 35),
        ("airy_size", "large"),
        ("airy_on_top", False),
    ],
)
def test_daemon_set_airy_setting_persists_applies_and_broadcasts(
    key: str,
    value: Any,
) -> None:
    with Store(":memory:") as store:
        transport = FakeTransport()
        broadcasts: list[dict[str, Any]] = []
        transport.subscribe(broadcasts.append)
        daemon = _make_daemon(store, ipc=transport)
        try:
            events = daemon.command(
                {
                    "name": "set_app_setting",
                    "id": f"set-{key}",
                    "key": key,
                    "value": value,
                }
            )

            assert events[0] == ack_event(f"set-{key}", True)
            assert events[1]["type"] == "app_settings"
            assert events[1]["settings"][key] == value
            assert broadcasts == events
            assert store.app_settings.get(key) == value
            assert daemon.pipeline.settings is not None
            assert daemon.pipeline.settings[key] == value
        finally:
            daemon.stop()


@pytest.mark.parametrize(
    "command",
    [
        {"name": "get_app_settings", "id": "get-no-store"},
        {
            "name": "set_app_setting",
            "id": "set-no-store",
            "key": "theme",
            "value": "dark",
        },
    ],
)
def test_daemon_app_setting_commands_ack_no_store(
    command: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Daemon, "_open_configured_store", lambda self: None)
    config = AppConfig.defaults()
    daemon = Daemon(
        config,
        practice=True,
        controller=ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        ),
    )
    try:
        events = daemon.command(command)
    finally:
        daemon.stop()

    assert events == [ack_event(command["id"], False, "no store")]


def test_parse_command_accepts_app_setting_commands() -> None:
    get_message = _wire_command("get_app_settings", id="get-settings")
    set_message = _wire_command(
        "set_app_setting",
        id="set-settings",
        key="theme",
        value="dark",
    )

    assert parse_command(json.dumps(get_message)) == get_message
    assert parse_command(json.dumps(set_message)) == set_message


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("airy_feedback_level", "hidden"),
        ("airy_sounds", True),
        ("airy_animation_intensity", 25),
        ("airy_size", "small"),
        ("airy_on_top", False),
    ],
)
def test_parse_command_accepts_new_airy_app_settings(
    key: str,
    value: Any,
) -> None:
    message = _wire_command("set_app_setting", key=key, value=value)

    assert parse_command(json.dumps(message)) == message


@pytest.mark.parametrize(
    "message",
    [
        _wire_command("set_app_setting", value=50),
        _wire_command("set_app_setting", key=123, value=50),
        _wire_command("set_app_setting", key="sensitivity"),
    ],
)
def test_parse_command_rejects_malformed_set_app_setting(
    message: dict[str, Any],
) -> None:
    with pytest.raises(IpcProtocolError):
        parse_command(json.dumps(message))
