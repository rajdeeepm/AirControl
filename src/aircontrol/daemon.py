"""Lifecycle and command boundary for the AirControl gesture pipeline."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.domain import HandObservation
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline, PipelineEvent
from aircontrol.profile import load_active_profile
from aircontrol.store import Store


logger = logging.getLogger(__name__)


def default_store_path() -> Path:
    """Return the platform's per-user AirControl database location."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        configured_base = Path(local_app_data).expanduser() if local_app_data else None
        base = (
            configured_base
            if configured_base is not None and configured_base.is_absolute()
            else Path.home() / "AppData" / "Local"
        )
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        data_home = os.environ.get("XDG_DATA_HOME")
        configured_base = Path(data_home).expanduser() if data_home else None
        base = (
            configured_base
            if configured_base is not None and configured_base.is_absolute()
            else Path.home() / ".local" / "share"
        )
    return base / "AirControl" / "aircontrol.db"


class Daemon:
    """Own a pipeline and expose its synchronous lifecycle and command surface."""

    def __init__(
        self,
        config: AppConfig,
        *,
        practice: bool,
        controller: ActionController,
        store: Store | None = None,
        ipc: Any = None,
    ) -> None:
        self.config = config
        self.practice = practice
        self.ipc = ipc
        self.quit_requested = False
        self._started = False
        self._stopped = False
        self._lock = threading.RLock()
        self._owns_store = store is None
        self.store = store if store is not None else self._open_configured_store()
        profile = load_active_profile(self.store) if self.store is not None else None
        self.metrics = Metrics()
        self.gate = ConfidenceGate(GateThresholds())
        self.pipeline = Pipeline(
            config,
            controller,
            store=self.store,
            metrics=self.metrics,
            gate=self.gate,
            profile=profile,
        )
        if self.ipc is not None:
            self.ipc.on_command(self._handle_ipc_command)

    def feed(
        self,
        observation: HandObservation | None,
        now: float,
    ) -> list[PipelineEvent]:
        with self._lock:
            self._ensure_running()
            events = self.pipeline.process(observation, now)
        self._broadcast(events)
        return events

    def command(self, name: str) -> list[PipelineEvent]:
        with self._lock:
            self._ensure_running()
            if name == "toggle_arm":
                events = self.pipeline.toggle_arm(time.monotonic())
            elif name == "pause":
                events = self.pipeline.force_pause("Paused manually")
            elif name == "undo":
                events = self.pipeline.undo()
            elif name == "refresh_matcher":
                if self.pipeline.matcher is not None:
                    self.pipeline.matcher.refresh()
                events = [self.pipeline.status()]
            elif name == "get_status":
                events = [self.pipeline.status()]
            elif name == "quit":
                self.quit_requested = True
                events = self.pipeline.force_pause("Stopped")
            else:
                raise ValueError(f"Unsupported daemon command: {name}")
        self._broadcast(events)
        return events

    def status(self) -> PipelineEvent:
        with self._lock:
            return self.pipeline.status()

    def force_pause(self, reason: str) -> list[PipelineEvent]:
        with self._lock:
            self._ensure_running()
            events = self.pipeline.force_pause(reason)
        self._broadcast(events)
        return events

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                raise RuntimeError("Daemon has already stopped")
            if self._started:
                return
            self._started = True
            start = getattr(self.ipc, "start", None)
        if start is None:
            return
        try:
            start()
        except Exception:
            logger.exception("IPC server could not start; continuing in embedded mode")

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        try:
            stop = getattr(self.ipc, "stop", None)
            if stop is not None:
                stop()
        finally:
            with self._lock:
                try:
                    self.pipeline.release()
                finally:
                    if self._owns_store and self.store is not None:
                        self.store.close()

    def _handle_ipc_command(self, command: dict[str, Any]) -> None:
        name = command.get("name")
        if isinstance(name, str):
            self.command(name)

    def _broadcast(self, events: list[PipelineEvent]) -> None:
        if self.ipc is None:
            return
        for event in events:
            self.ipc.broadcast(event)

    def _open_configured_store(self) -> Store | None:
        configured = self.config.store.db_path
        path = Path(configured).expanduser() if configured else default_store_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return Store(path)
        except Exception:
            logger.exception("Local store is unavailable; continuing without persistence")
            return None

    def _ensure_running(self) -> None:
        if self._stopped:
            raise RuntimeError("Daemon has stopped")


__all__ = ["Daemon", "default_store_path"]
