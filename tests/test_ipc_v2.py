from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.ipc import (
    IpcProtocolError,
    ack_event,
    library_event,
    metrics_snapshot_event,
    parse_command,
    settings_event,
)
from aircontrol.metrics import MetricsSnapshot
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
    save_profile,
)
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory
from tests.test_matcher_integration import _add_gesture, _seed_two_gestures


def _command(name: str, *, id: str = "request-1", **fields: object) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "command",
        "name": name,
        "id": id,
        **fields,
    }


def _make_daemon(
    store: Store,
    *,
    config: AppConfig | None = None,
    ipc: object | None = None,
) -> Daemon:
    effective_config = config or AppConfig.defaults()
    controller = ActionController(
        effective_config.input.pointer_pixels_per_palm,
        practice=True,
    )
    return Daemon(
        effective_config,
        practice=True,
        controller=controller,
        store=store,
        ipc=ipc,
    )


class _RefreshSpy:
    def __init__(self) -> None:
        self.calls = 0

    def refresh(self) -> None:
        self.calls += 1


class _CaptureTransport:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.callback: Callable[[dict[str, Any]], None] | None = None

    def on_command(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self.callback = callback

    def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def send(self, command: dict[str, Any]) -> None:
        assert self.callback is not None
        self.callback(command)


@pytest.mark.parametrize(
    "payload",
    [
        _command("list_library"),
        _command(
            "set_mapping",
            gesture_id=1,
            action={"kind": "scroll", "amount": 2},
        ),
        _command(
            "set_mapping",
            gesture_id=1,
            action={"kind": "switch_next"},
            context="browser",
            enabled=False,
        ),
        _command("delete_gesture", gesture_id=1),
        _command("rename_gesture", gesture_id=1, new_name="Wave"),
        _command("get_metrics"),
        _command("get_settings"),
        _command("focus_dashboard"),
        {"v": 1, "type": "command", "name": "focus_dashboard"},
        _command("delete_everything"),
    ],
    ids=[
        "list-library",
        "set-mapping-default-context",
        "set-mapping-explicit-context",
        "delete-gesture",
        "rename-gesture",
        "get-metrics",
        "get-settings",
        "focus-dashboard-with-id",
        "focus-dashboard-without-id",
        "delete-everything",
    ],
)
def test_parse_command_accepts_new_commands(payload: dict[str, Any]) -> None:
    assert parse_command(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    "payload",
    [
        _command("list_library", id=1),
        _command("set_mapping", action={"kind": "scroll"}),
        _command("set_mapping", gesture_id=True, action={"kind": "scroll"}),
        _command("set_mapping", gesture_id=1),
        _command("set_mapping", gesture_id=1, action=[]),
        _command(
            "set_mapping",
            gesture_id=1,
            action={"kind": "scroll"},
            context=1,
        ),
        _command(
            "set_mapping",
            gesture_id=1,
            action={"kind": "scroll"},
            enabled=0,
        ),
        _command(
            "set_mapping",
            gesture_id=1,
            action={"kind": "scroll"},
            enabled="false",
        ),
        _command(
            "set_mapping",
            gesture_id=1,
            action={"kind": "scroll"},
            enabled=None,
        ),
        _command("delete_gesture"),
        _command("delete_gesture", gesture_id=False),
        _command("rename_gesture", new_name="Wave"),
        _command("rename_gesture", gesture_id=1),
        _command("rename_gesture", gesture_id=1, new_name=""),
        _command("rename_gesture", gesture_id=1, new_name=42),
        _command("get_metrics", id=None),
    ],
    ids=[
        "non-string-id",
        "set-mapping-missing-gesture",
        "set-mapping-bool-gesture",
        "set-mapping-missing-action",
        "set-mapping-invalid-action",
        "set-mapping-invalid-context",
        "set-mapping-integer-enabled",
        "set-mapping-string-enabled",
        "set-mapping-null-enabled",
        "delete-missing-gesture",
        "delete-bool-gesture",
        "rename-missing-gesture",
        "rename-missing-new-name",
        "rename-empty-new-name",
        "rename-invalid-new-name",
        "null-id",
    ],
)
def test_parse_command_rejects_missing_or_invalid_fields(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(IpcProtocolError):
        parse_command(json.dumps(payload))


def test_new_event_builders_have_v1_schema_and_echo_ids() -> None:
    assert library_event([{"id": 1}], "library-1") == {
        "v": 1,
        "type": "library",
        "gestures": [{"id": 1}],
        "id": "library-1",
    }
    assert metrics_snapshot_event(
        candidates_per_hour=1.0,
        armed_candidates_per_hour=2.0,
        fp_per_hour=3.0,
        latency_ms_p50=4.0,
        latency_ms_p95=5.0,
        cpu_pct=6.0,
        uptime_seconds=7.0,
        id="metrics-1",
    ) == {
        "v": 1,
        "type": "metrics_snapshot",
        "candidates_per_hour": 1.0,
        "armed_candidates_per_hour": 2.0,
        "fp_per_hour": 3.0,
        "latency_ms_p50": 4.0,
        "latency_ms_p95": 5.0,
        "cpu_pct": 6.0,
        "uptime_seconds": 7.0,
        "id": "metrics-1",
    }
    assert settings_event({"camera_index": 2}, "settings-1") == {
        "v": 1,
        "type": "settings",
        "payload": {"camera_index": 2},
        "id": "settings-1",
    }
    assert ack_event("ack-1", True) == {
        "v": 1,
        "type": "ack",
        "ok": True,
        "error": "",
        "id": "ack-1",
    }
    assert ack_event(None, False, "failed") == {
        "v": 1,
        "type": "ack",
        "ok": False,
        "error": "failed",
    }


def test_focus_dashboard_acks_and_sets_consumable_flag() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            assert daemon.focus_requested is False

            events = daemon.command(
                _command("focus_dashboard", id="focus-request")
            )

            assert events == [ack_event("focus-request", True)]
            assert daemon.focus_requested is True
            assert daemon.take_focus_request() is True
            assert daemon.focus_requested is False
            assert daemon.take_focus_request() is False
        finally:
            daemon.stop()


def test_list_library_returns_records_stats_mappings_and_downsampled_animation() -> None:
    with Store(":memory:") as store:
        gesture_ids = _seed_two_gestures(store)
        horizontal_id = gesture_ids["horizontal"]
        store.gesture_stats.record_confirm(horizontal_id)
        store.gesture_stats.record_confirm(horizontal_id)
        store.gesture_stats.record_reject(horizontal_id, offset_bump=0.04)

        source = store.exemplars.list(horizontal_id)[0]
        for exemplar_id, _trajectory in store.exemplars.list_with_ids(horizontal_id):
            store.exemplars.delete(exemplar_id)
        long_frames = tuple(
            LandmarkFrame(
                landmarks=source.frames[index % len(source.frames)].landmarks,
                handedness=source.handedness,
                timestamp=float(index),
            )
            for index in range(65)
        )
        store.exemplars.add(
            horizontal_id,
            Trajectory(frames=long_frames, handedness=source.handedness),
        )

        daemon = _make_daemon(store)
        try:
            events = daemon.command(_command("list_library", id="library-request"))
        finally:
            daemon.stop()

        assert len(events) == 1
        event = events[0]
        assert event["v"] == 1
        assert event["type"] == "library"
        assert event["id"] == "library-request"
        gestures = event["gestures"]
        assert len(gestures) == 2
        horizontal = next(
            gesture for gesture in gestures if gesture["id"] == horizontal_id
        )
        assert set(horizontal) == {
            "id",
            "name",
            "description",
            "exemplar_count",
            "confirms",
            "rejects",
            "threshold_offset",
            "mapping",
            "animation",
        }
        assert horizontal["name"] == "Horizontal"
        assert horizontal["description"] == ""
        assert horizontal["exemplar_count"] == 1
        assert horizontal["confirms"] == 2
        assert horizontal["rejects"] == 1
        assert horizontal["threshold_offset"] == pytest.approx(0.04)
        assert horizontal["mapping"] == {
            "kind": "switch_next",
            "enabled": True,
        }

        animation = horizontal["animation"]
        assert animation is not None
        assert len(animation["frames"]) == 30
        assert len(animation["timestamps"]) == len(animation["frames"])
        assert animation["timestamps"][0] == 0.0
        assert animation["timestamps"][-1] == 64.0
        assert all(len(frame) == 21 for frame in animation["frames"])
        assert all(
            isinstance(point, list) and len(point) == 3
            for frame in animation["frames"]
            for point in frame
        )


def test_set_mapping_upserts_default_and_explicit_contexts() -> None:
    with Store(":memory:") as store:
        gesture_id = _add_gesture(
            store,
            "Horizontal",
            "horizontal",
            {"kind": "switch_next"},
        )
        daemon = _make_daemon(store)
        try:
            default_events = daemon.command(
                _command(
                    "set_mapping",
                    id="mapping-global",
                    gesture_id=gesture_id,
                    action={"kind": "scroll", "amount": -2},
                    enabled=False,
                )
            )
            context_events = daemon.command(
                _command(
                    "set_mapping",
                    id="mapping-browser",
                    gesture_id=gesture_id,
                    action={"kind": "switch_previous"},
                    context="browser",
                )
            )
        finally:
            daemon.stop()

        assert default_events == [ack_event("mapping-global", True)]
        assert context_events == [ack_event("mapping-browser", True)]
        assert store.mappings.for_gesture(gesture_id).action == {
            "kind": "scroll",
            "amount": -2,
        }
        assert store.mappings.for_gesture(gesture_id).enabled is False
        assert store.mappings.for_gesture(gesture_id, "browser").action == {
            "kind": "switch_previous"
        }
        assert store.mappings.for_gesture(gesture_id, "browser").enabled is True

        daemon = _make_daemon(store)
        try:
            library = daemon.command(_command("list_library"))[0]
        finally:
            daemon.stop()

        mapping = next(
            gesture["mapping"]
            for gesture in library["gestures"]
            if gesture["id"] == gesture_id
        )
        assert mapping == {
            "kind": "scroll",
            "amount": -2,
            "enabled": False,
        }


def test_rename_and_delete_gesture_refresh_matcher_and_ack() -> None:
    with Store(":memory:") as store:
        gesture_ids = _seed_two_gestures(store)
        daemon = _make_daemon(store)
        matcher = _RefreshSpy()
        daemon.pipeline.matcher = matcher  # type: ignore[assignment]
        try:
            rename_events = daemon.command(
                _command(
                    "rename_gesture",
                    id="rename-1",
                    gesture_id=gesture_ids["horizontal"],
                    new_name="Sideways",
                )
            )
            delete_events = daemon.command(
                _command(
                    "delete_gesture",
                    id="delete-1",
                    gesture_id=gesture_ids["vertical"],
                )
            )
        finally:
            daemon.stop()

        renamed = store.gestures.get(gesture_ids["horizontal"])
        assert renamed is not None
        assert renamed.name == "Sideways"
        assert store.gestures.get(gesture_ids["vertical"]) is None
        assert rename_events == [ack_event("rename-1", True)]
        assert delete_events == [ack_event("delete-1", True)]
        assert matcher.calls == 2


def test_get_metrics_returns_complete_snapshot_with_correlation_id() -> None:
    snapshot = MetricsSnapshot(
        candidates_per_hour=11.0,
        armed_candidates_per_hour=9.0,
        fp_per_hour=1.5,
        latency_ms_p50=20.0,
        latency_ms_p95=45.0,
        cpu_pct=7.0,
        uptime_seconds=60.0,
    )

    class _FixedMetrics:
        def snapshot(self) -> MetricsSnapshot:
            return snapshot

    with Store(":memory:") as store:
        _seed_two_gestures(store)
        daemon = _make_daemon(store)
        daemon.metrics = _FixedMetrics()  # type: ignore[assignment]
        try:
            events = daemon.command(_command("get_metrics", id="metrics-request"))
        finally:
            daemon.stop()

    assert events == [
        metrics_snapshot_event(
            candidates_per_hour=11.0,
            armed_candidates_per_hour=9.0,
            fp_per_hour=1.5,
            latency_ms_p50=20.0,
            latency_ms_p95=45.0,
            cpu_pct=7.0,
            uptime_seconds=60.0,
            id="metrics-request",
        )
    ]


def test_get_settings_returns_effective_config_summary(tmp_path: Path) -> None:
    config = AppConfig.defaults()
    config.camera.index = 3
    config.store.db_path = str(tmp_path / "data" / "aircontrol.db")

    with Store(":memory:") as store:
        store.app_settings.set("click_mode", "two_hand")
        daemon = _make_daemon(store, config=config)
        try:
            events = daemon.command(_command("get_settings", id="settings-request"))
        finally:
            daemon.stop()

    assert events == [
        settings_event(
            {
                "clutch_mode": config.clutch.mode,
                "click_mode": "two_hand",
                "gate_thresholds": {"t1": 0.0, "t2": 0.0, "t3": 0.0},
                "camera_index": 3,
                "camera_state": "off",
                "camera_error": None,
                "store_db_path": str(Path(config.store.db_path).resolve()),
                "has_calibration_profile": False,
            },
            "settings-request",
        )
    ]


def test_get_settings_returns_active_calibration_summary() -> None:
    profile = CalibrationProfile(
        hand_size=0.23,
        volume=InteractionVolume(0.1, 0.9, 0.2, 0.8),
        motion=MotionSignature(velocity_floor=0.35, velocity_ceiling=2.4),
        lighting=LightingProfile(
            mean_brightness=128.5,
            landmark_jitter=0.012,
            acceptable=True,
        ),
        incidental_features=((0.1, 0.2, 0.3),),
        created_at=1_720_000_000.25,
    )

    with Store(":memory:") as store:
        save_profile(store, profile)
        daemon = _make_daemon(store)
        try:
            event = daemon.command(_command("get_settings"))[0]
        finally:
            daemon.stop()

    assert event["type"] == "settings"
    assert event["payload"]["has_calibration_profile"] is True
    assert event["payload"]["calibration"] == {
        "hand_size": 0.23,
        "lighting_acceptable": True,
        "created_at": 1_720_000_000.25,
    }


def test_delete_everything_wipes_all_tables_refreshes_and_acks() -> None:
    with Store(":memory:") as store:
        gesture_id = _add_gesture(
            store,
            "Horizontal",
            "horizontal",
            {"kind": "switch_next"},
        )
        store.gesture_stats.record_confirm(gesture_id)
        store.calibration.save("saved", {"hand_size": 1.0})
        daemon = _make_daemon(store)
        matcher = _RefreshSpy()
        daemon.pipeline.matcher = matcher  # type: ignore[assignment]
        try:
            events = daemon.command(
                _command("delete_everything", id="delete-all-request")
            )
        finally:
            daemon.stop()

        counts = {
            table: store._connection.execute(  # noqa: SLF001
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in (
                "gestures",
                "exemplars",
                "mappings",
                "calibration_profiles",
                "gesture_stats",
            )
        }

    assert events == [ack_event("delete-all-request", True)]
    assert counts == {
        "gestures": 0,
        "exemplars": 0,
        "mappings": 0,
        "calibration_profiles": 0,
        "gesture_stats": 0,
    }
    assert matcher.calls == 1


@pytest.mark.parametrize(
    "command",
    [
        _command("list_library", id="no-store-list"),
        _command(
            "set_mapping",
            id="no-store-map",
            gesture_id=1,
            action={"kind": "scroll"},
        ),
        _command("delete_gesture", id="no-store-delete", gesture_id=1),
        _command(
            "rename_gesture",
            id="no-store-rename",
            gesture_id=1,
            new_name="Wave",
        ),
        _command("get_metrics", id="no-store-metrics"),
        _command("delete_everything", id="no-store-delete-all"),
    ],
)
def test_new_handlers_ack_no_store(
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


def test_get_settings_without_store_reports_no_calibration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Daemon, "_open_configured_store", lambda self: None)
    config = AppConfig.defaults()
    config.store.db_path = str(tmp_path / "unavailable" / "aircontrol.db")
    daemon = Daemon(
        config,
        practice=True,
        controller=ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        ),
    )
    try:
        events = daemon.command(_command("get_settings", id="no-store-settings"))
    finally:
        daemon.stop()

    assert events == [
        settings_event(
            {
                "clutch_mode": config.clutch.mode,
                "click_mode": "single",
                "gate_thresholds": {"t1": 0.0, "t2": 0.0, "t3": 0.0},
                "camera_index": config.camera.index,
                "camera_state": "off",
                "camera_error": None,
                "store_db_path": str(Path(config.store.db_path).resolve()),
                "has_calibration_profile": False,
            },
            "no-store-settings",
        )
    ]


def test_ipc_routing_passes_full_command_dict_and_echoes_id() -> None:
    with Store(":memory:") as store:
        gesture_ids = _seed_two_gestures(store)
        transport = _CaptureTransport()
        daemon = _make_daemon(store, ipc=transport)
        try:
            transport.send(
                _command(
                    "set_mapping",
                    id="routed-request",
                    gesture_id=gesture_ids["horizontal"],
                    action={"kind": "scroll", "amount": 3},
                )
            )
        finally:
            daemon.stop()

        mapping = store.mappings.for_gesture(gesture_ids["horizontal"])

    assert transport.events == [ack_event("routed-request", True)]
    assert mapping is not None
    assert mapping.action == {"kind": "scroll", "amount": 3}


def test_daemon_string_command_form_remains_backward_compatible() -> None:
    with Store(":memory:") as store:
        daemon = _make_daemon(store)
        try:
            events = daemon.command(name="get_status")
        finally:
            daemon.stop()

    assert len(events) == 1
    assert events[0]["v"] == 1
    assert events[0]["type"] == "status"
