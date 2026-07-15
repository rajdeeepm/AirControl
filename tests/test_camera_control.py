from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.ipc import (
    FakeTransport,
    IpcClient,
    IpcProtocolError,
    camera_event,
    parse_command,
)
from aircontrol.store import Store


def _command(name: str, **fields: object) -> dict[str, Any]:
    return {"v": 1, "type": "command", "name": name, **fields}


@contextmanager
def _daemon(
    *,
    ipc: object | None = None,
    runtime_camera: bool = False,
) -> Iterator[Daemon]:
    config = AppConfig.defaults()
    config.ipc.enabled = runtime_camera
    with Store(":memory:") as store:
        daemon = Daemon(
            config,
            practice=True,
            controller=ActionController(
                config.input.pointer_pixels_per_palm,
                practice=True,
            ),
            store=store,
            ipc=ipc,
        )
        try:
            yield daemon
        finally:
            daemon.stop()


def test_camera_event_has_v1_schema_and_echoes_id() -> None:
    assert camera_event("off") == {
        "v": 1,
        "type": "camera",
        "state": "off",
        "camera_error": None,
    }
    assert camera_event(
        "error",
        "Camera unavailable",
        "camera-request",
    ) == {
        "v": 1,
        "type": "camera",
        "state": "error",
        "camera_error": "Camera unavailable",
        "id": "camera-request",
    }


@pytest.mark.parametrize("state", ["off", "starting", "active", "error"])
def test_camera_event_accepts_each_camera_state(state: str) -> None:
    assert camera_event(state)["state"] == state


def test_camera_event_rejects_unknown_state() -> None:
    with pytest.raises(IpcProtocolError, match="camera state"):
        camera_event("stalled")


@pytest.mark.parametrize(
    "payload",
    [
        _command("set_camera", enabled=True, id="camera-on"),
        _command("set_camera", enabled=False, id="camera-off"),
        _command("retry_camera", id="camera-retry"),
    ],
    ids=["set-on", "set-off", "retry"],
)
def test_parse_command_accepts_camera_commands(payload: dict[str, Any]) -> None:
    assert parse_command(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    "payload",
    [
        _command("set_camera"),
        _command("set_camera", enabled=None),
        _command("set_camera", enabled=1),
        _command("set_camera", enabled="true"),
    ],
    ids=["missing", "null", "integer", "string"],
)
def test_parse_command_rejects_set_camera_without_boolean_enabled(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(IpcProtocolError, match="set_camera enabled"):
        parse_command(json.dumps(payload))


def test_ipc_client_accepts_camera_events() -> None:
    class WebSocket:
        async def recv(self) -> str:
            return json.dumps(camera_event("active"))

    async def receive() -> dict[str, Any]:
        client = IpcClient("ws://unused")
        client._websocket = WebSocket()
        return await client.__anext__()

    assert asyncio.run(receive()) == camera_event("active")


def test_fresh_daemon_camera_defaults_are_off() -> None:
    with _daemon() as daemon:
        assert daemon.camera_enabled is False
        assert daemon.camera_state == "off"
        assert daemon.camera_error is None
        assert daemon.take_camera_restart_request() is False


def test_set_camera_on_and_retry_request_restart_and_echo_id() -> None:
    with _daemon() as daemon:
        enabled_events = daemon.command(
            _command("set_camera", enabled=True, id="camera-on")
        )

        assert enabled_events == [camera_event("starting", id="camera-on")]
        assert daemon.camera_enabled is True
        assert daemon.camera_state == "starting"
        assert daemon.camera_error is None
        assert daemon.take_camera_restart_request() is True
        assert daemon.take_camera_restart_request() is False

        daemon.set_camera_state("error", "Camera unavailable")
        retry_events = daemon.command(_command("retry_camera", id="camera-retry"))

        assert retry_events == [camera_event("starting", id="camera-retry")]
        assert daemon.camera_enabled is True
        assert daemon.camera_state == "starting"
        assert daemon.camera_error is None
        assert daemon.take_camera_restart_request() is True


def test_set_camera_off_pauses_arming_disables_preview_and_clears_restart() -> None:
    with _daemon() as daemon:
        daemon.command(_command("set_camera", enabled=True))
        daemon.command("toggle_arm")
        assert daemon.status()["armed"] is True
        daemon.preview_enabled = True

        events = daemon.command(
            _command("set_camera", enabled=False, id="camera-off")
        )

        assert daemon.camera_enabled is False
        assert daemon.camera_state == "off"
        assert daemon.camera_error is None
        assert daemon.preview_enabled is False
        assert daemon.status()["armed"] is False
        assert daemon.take_camera_restart_request() is False
        assert camera_event("off", id="camera-off") in events


def test_runtime_camera_error_is_broadcast_and_safety_pauses() -> None:
    transport = FakeTransport()
    captured: list[dict[str, Any]] = []
    transport.subscribe(captured.append)

    with _daemon(ipc=transport) as daemon:
        daemon.command(_command("set_camera", enabled=True))
        assert daemon.take_camera_restart_request() is True
        daemon.set_camera_state("active")
        daemon.command("toggle_arm")
        assert daemon.status()["armed"] is True
        daemon.preview_enabled = True
        captured.clear()

        events = daemon.set_camera_state("error", "Camera unavailable")

        assert daemon.camera_enabled is True
        assert daemon.camera_state == "error"
        assert daemon.camera_error == "Camera unavailable"
        assert daemon.preview_enabled is False
        assert daemon.status()["armed"] is False
        assert camera_event("error", "Camera unavailable") in events
        assert captured == events


def test_runtime_camera_state_broadcasts_only_changes() -> None:
    transport = FakeTransport()
    captured: list[dict[str, Any]] = []
    transport.subscribe(captured.append)

    with _daemon(ipc=transport) as daemon:
        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        captured.clear()
        first = daemon.set_camera_state("active")
        second = daemon.set_camera_state("active")
        starting = daemon.set_camera_state("starting")

        assert first == [camera_event("active")]
        assert second == []
        assert starting == [camera_event("starting")]
        assert captured == [camera_event("active"), camera_event("starting")]


def test_pending_retry_is_not_overwritten_by_a_stale_failure() -> None:
    with _daemon() as daemon:
        daemon.command(_command("set_camera", enabled=True))

        events = daemon.set_camera_state("error", "failure from old worker")

        assert events == []
        assert daemon.camera_enabled is True
        assert daemon.camera_state == "starting"
        assert daemon.camera_error is None
        assert daemon.take_camera_restart_request() is True


def test_user_camera_off_is_not_overwritten_by_a_stale_active_report() -> None:
    with _daemon() as daemon:
        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        daemon.set_camera_state("active")
        daemon.command(_command("set_camera", enabled=False))

        events = daemon.set_camera_state("active")

        assert events == []
        assert daemon.camera_enabled is False
        assert daemon.camera_state == "off"


def test_runtime_camera_blocks_arm_and_stale_feed_until_active() -> None:
    with _daemon(runtime_camera=True) as daemon:
        blocked = daemon.command("toggle_arm")

        assert blocked[-1]["type"] == "status"
        assert blocked[-1]["armed"] is False

        def fail_process(_observation: object, _now: float) -> list[dict[str, Any]]:
            raise AssertionError("camera-inactive feed reached the pipeline")

        daemon.pipeline.process = fail_process  # type: ignore[method-assign]
        assert daemon.feed(None, 0.0) == []

        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        daemon.set_camera_state("active")
        active_toggle = daemon.command("toggle_arm")

        assert active_toggle[-1]["armed"] is True


def test_runtime_preview_cannot_enable_or_broadcast_without_active_camera() -> None:
    transport = FakeTransport()
    with _daemon(ipc=transport, runtime_camera=True) as daemon:
        daemon.command(
            _command("set_preview", enabled=True, id="preview-while-off")
        )
        daemon.broadcast_preview(b"stale-off-frame")

        assert daemon.preview_enabled is False
        assert transport.binary_messages == []

        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        daemon.set_camera_state("active")
        daemon.command(_command("set_preview", enabled=True))
        daemon.broadcast_preview(b"active-frame")

        assert daemon.preview_enabled is True
        assert transport.binary_messages == [b"active-frame"]

        daemon.command(_command("set_camera", enabled=False))
        daemon.command(_command("set_preview", enabled=True))
        daemon.broadcast_preview(b"stale-after-off-frame")

        assert daemon.preview_enabled is False
        assert transport.binary_messages == [b"active-frame"]


@pytest.mark.parametrize(
    "restart_command",
    [
        _command("set_camera", enabled=True, id="set-on-again"),
        _command("retry_camera", id="retry-active"),
    ],
    ids=["set-camera-true", "retry-camera"],
)
def test_active_camera_restart_immediately_pauses_and_disables_preview(
    restart_command: dict[str, Any],
) -> None:
    with _daemon(runtime_camera=True) as daemon:
        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        daemon.set_camera_state("active")
        daemon.command("toggle_arm")
        daemon.command(_command("set_preview", enabled=True))

        events = daemon.command(restart_command)

        assert daemon.camera_state == "starting"
        assert daemon.status()["armed"] is False
        assert daemon.preview_enabled is False
        assert events[-1]["type"] == "camera"
        assert events[-1]["state"] == "starting"


def test_camera_transition_broadcast_order_matches_locked_state_order() -> None:
    class BlockingTransport:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []
            self.active_broadcast_started = threading.Event()
            self.release_active_broadcast = threading.Event()

        def on_command(self, _callback: object) -> None:
            pass

        def broadcast(self, event: dict[str, Any]) -> None:
            if event.get("type") == "camera" and event.get("state") == "active":
                self.active_broadcast_started.set()
                assert self.release_active_broadcast.wait(timeout=1.0)
            self.events.append(event)

    transport = BlockingTransport()
    with _daemon(ipc=transport) as daemon:
        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        transport.events.clear()
        errors: list[BaseException] = []

        def report_active() -> None:
            try:
                daemon.set_camera_state("active")
            except BaseException as exc:  # pragma: no cover - assertion relay
                errors.append(exc)

        off_attempted = threading.Event()
        off_finished = threading.Event()

        def turn_off() -> None:
            off_attempted.set()
            try:
                daemon.command(_command("set_camera", enabled=False))
            except BaseException as exc:  # pragma: no cover - assertion relay
                errors.append(exc)
            finally:
                off_finished.set()

        active_thread = threading.Thread(target=report_active)
        active_thread.start()
        assert transport.active_broadcast_started.wait(timeout=1.0)

        off_thread = threading.Thread(target=turn_off)
        off_thread.start()
        assert off_attempted.wait(timeout=1.0)
        assert not off_finished.wait(timeout=0.05)

        transport.release_active_broadcast.set()
        active_thread.join(timeout=1.0)
        off_thread.join(timeout=1.0)

        assert not active_thread.is_alive()
        assert not off_thread.is_alive()
        assert errors == []
        assert [
            event["state"]
            for event in transport.events
            if event.get("type") == "camera"
        ] == ["active", "off"]


def test_arm_status_cannot_broadcast_after_later_camera_off_status() -> None:
    class BlockingTransport:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []
            self.armed_broadcast_started = threading.Event()
            self.release_armed_broadcast = threading.Event()

        def on_command(self, _callback: object) -> None:
            pass

        def broadcast(self, event: dict[str, Any]) -> None:
            if event.get("type") == "status" and event.get("armed") is True:
                self.armed_broadcast_started.set()
                assert self.release_armed_broadcast.wait(timeout=1.0)
            self.events.append(event)

    transport = BlockingTransport()
    with _daemon(ipc=transport, runtime_camera=True) as daemon:
        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        daemon.set_camera_state("active")
        transport.events.clear()
        errors: list[BaseException] = []

        def arm() -> None:
            try:
                daemon.command("toggle_arm")
            except BaseException as exc:  # pragma: no cover - assertion relay
                errors.append(exc)

        off_attempted = threading.Event()
        off_finished = threading.Event()

        def turn_off() -> None:
            off_attempted.set()
            try:
                daemon.command(_command("set_camera", enabled=False))
            except BaseException as exc:  # pragma: no cover - assertion relay
                errors.append(exc)
            finally:
                off_finished.set()

        arm_thread = threading.Thread(target=arm)
        arm_thread.start()
        assert transport.armed_broadcast_started.wait(timeout=1.0)

        off_thread = threading.Thread(target=turn_off)
        off_thread.start()
        assert off_attempted.wait(timeout=1.0)
        assert not off_finished.wait(timeout=0.05)

        transport.release_armed_broadcast.set()
        arm_thread.join(timeout=1.0)
        off_thread.join(timeout=1.0)

        assert not arm_thread.is_alive()
        assert not off_thread.is_alive()
        assert errors == []
        assert [
            event["armed"]
            for event in transport.events
            if event.get("type") == "status"
        ] == [True, False]
        assert daemon.camera_state == "off"
        assert daemon.status()["armed"] is False


def test_get_settings_includes_camera_state_even_without_store(
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
        daemon.command(_command("set_camera", enabled=True))
        daemon.take_camera_restart_request()
        daemon.set_camera_state("error", "Camera blocked")
        event = daemon.command(_command("get_settings", id="settings"))[0]
    finally:
        daemon.stop()

    assert event["type"] == "settings"
    assert event["id"] == "settings"
    assert event["payload"]["camera_state"] == "error"
    assert event["payload"]["camera_error"] == "Camera blocked"


def test_get_settings_defaults_include_camera_off_state() -> None:
    with _daemon() as daemon:
        event = daemon.command(_command("get_settings", id="settings"))[0]

    assert event["id"] == "settings"
    assert event["payload"]["camera_state"] == "off"
    assert event["payload"]["camera_error"] is None
