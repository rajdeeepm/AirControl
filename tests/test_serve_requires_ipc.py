from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.store import Store


class _StubIpc:
    def __init__(self, start_error: Exception | None = None) -> None:
        self.start_error = start_error
        self.start_called = False
        self.callback: Callable[[dict[str, Any]], None] | None = None
        self.events: list[dict[str, Any]] = []

    def on_command(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self.callback = callback

    def start(self) -> None:
        self.start_called = True
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        pass

    def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _make_daemon(
    store: Store,
    ipc: _StubIpc,
    *,
    require_ipc: bool = False,
) -> Daemon:
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
        require_ipc=require_ipc,
    )


def test_required_ipc_import_error_explains_how_to_repair_venv() -> None:
    with Store(":memory:") as store:
        ipc = _StubIpc(ImportError("No module named websockets"))
        daemon = _make_daemon(store, ipc, require_ipc=True)
        try:
            with pytest.raises(RuntimeError) as excinfo:
                daemon.start()
        finally:
            daemon.stop()

    message = str(excinfo.value).lower()
    assert "websockets" in message
    assert "setup.cmd" in message


def test_required_ipc_os_error_names_startup_failure() -> None:
    with Store(":memory:") as store:
        ipc = _StubIpc(OSError("address already in use"))
        daemon = _make_daemon(store, ipc, require_ipc=True)
        try:
            with pytest.raises(RuntimeError) as excinfo:
                daemon.start()
        finally:
            daemon.stop()

    assert "address already in use" in str(excinfo.value).lower()


def test_optional_ipc_failure_remains_soft_and_daemon_still_works() -> None:
    with Store(":memory:") as store:
        ipc = _StubIpc(OSError("address already in use"))
        daemon = _make_daemon(store, ipc)
        try:
            daemon.start()
            events = daemon.feed(None, now=0.0)
            status = daemon.status()
        finally:
            daemon.stop()

    assert ipc.start_called
    assert events[-1]["type"] == "status"
    assert status["type"] == "status"


def test_required_working_ipc_starts_successfully() -> None:
    with Store(":memory:") as store:
        ipc = _StubIpc()
        daemon = _make_daemon(store, ipc, require_ipc=True)
        try:
            daemon.start()
        finally:
            daemon.stop()

    assert ipc.start_called
