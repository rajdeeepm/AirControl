from __future__ import annotations

import asyncio
import errno
import json
import socket
import threading
from collections.abc import Callable
from typing import Any

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.ipc import IpcServer, parse_command


class _ThreadedCommandTransport:
    """Exercise daemon callbacks on the same kind of background thread as IPC."""

    def __init__(self) -> None:
        self._callback: Callable[[dict[str, Any]], None] | None = None
        self.events: list[dict[str, Any]] = []

    def on_command(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._callback = callback

    def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def dispatch_raw(
        self,
        raw: str,
    ) -> tuple[threading.Thread, list[BaseException]]:
        callback = self._callback
        if callback is None:
            raise RuntimeError("command callback is not configured")
        command = parse_command(raw)
        errors: list[BaseException] = []

        def dispatch() -> None:
            try:
                callback(command)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=dispatch, name="test-aircontrol-ipc")
        thread.start()
        return thread, errors


def _skip_when_loopback_is_forbidden() -> None:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        pytest.skip("test environment forbids loopback sockets")
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EPERM}:
            pytest.skip("test environment forbids loopback sockets")
        raise
    finally:
        probe.close()


def test_ui_query_commands_round_trip_with_correlation_ids(tmp_path) -> None:
    websockets = pytest.importorskip("websockets")
    _skip_when_loopback_is_forbidden()
    config = AppConfig.defaults()
    config.store.db_path = str(tmp_path / "aircontrol.db")
    config.ipc.enabled = True
    config.ipc.port = 0
    server = IpcServer(host=config.ipc.host, port=config.ipc.port)
    daemon = Daemon(
        config,
        practice=True,
        controller=ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        ),
        store=None,
        ipc=server,
        require_ipc=True,
    )

    async def exercise_socket() -> None:
        commands = (
            (
                {"v": 1, "type": "command", "name": "get_app_settings", "id": "r1"},
                "app_settings",
            ),
            (
                {"v": 1, "type": "command", "name": "get_settings", "id": "r2"},
                "settings",
            ),
            (
                {"v": 1, "type": "command", "name": "list_library", "id": "r3"},
                "library",
            ),
        )
        async with websockets.connect(
            f"ws://{config.ipc.host}:{server.port}"
        ) as websocket:
            for command, expected_type in commands:
                await websocket.send(json.dumps(command))
                raw_reply = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                reply = json.loads(raw_reply)
                assert reply["type"] == expected_type
                assert reply["id"] == command["id"]

    try:
        daemon.start()
        asyncio.run(exercise_socket())
    finally:
        daemon.stop()


def test_ui_query_commands_reply_from_ipc_background_thread(tmp_path) -> None:
    config = AppConfig.defaults()
    config.store.db_path = str(tmp_path / "aircontrol.db")
    transport = _ThreadedCommandTransport()
    daemon = Daemon(
        config,
        practice=True,
        controller=ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        ),
        store=None,
        ipc=transport,
    )
    commands = (
        ({"v": 1, "type": "command", "name": "get_app_settings", "id": "r1"}, "app_settings"),
        ({"v": 1, "type": "command", "name": "get_settings", "id": "r2"}, "settings"),
        ({"v": 1, "type": "command", "name": "list_library", "id": "r3"}, "library"),
    )

    try:
        daemon.start()
        for command, expected_type in commands:
            thread, errors = transport.dispatch_raw(json.dumps(command))
            thread.join(timeout=2.0)
            assert not thread.is_alive()
            assert errors == []
            assert any(
                event.get("type") == expected_type
                and event.get("id") == command["id"]
                for event in transport.events
            )
    finally:
        daemon.stop()
