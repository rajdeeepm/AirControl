from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import aircontrol.app as app_module
from aircontrol.config import AppConfig
from aircontrol.domain import EngineStatus, Pose
from aircontrol.vision import AsyncVisionWorker, CameraError


FRIENDLY_CAMERA_ERROR = (
    "Camera unavailable — it may be turned off, in use by another app, or "
    "blocked in Windows camera privacy settings. Turn it on there, then Retry."
)


class _Controller:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.released = False
        self.closed = False

    def release_all(self) -> None:
        self.released = True

    def close(self) -> None:
        self.closed = True


class _RuntimeDaemon:
    def __init__(self, state: dict[str, Any], *_args: object, **_kwargs: object) -> None:
        self.preview_enabled = False
        self.quit_requested = False
        self._camera_enabled = False
        self._camera_state = "off"
        self._camera_error: str | None = None
        self._camera_restart_requested = False
        self.camera_history: list[tuple[str, str | None]] = []
        self.pause_reasons: list[str] = []
        self.started = False
        self.stopped = False
        self.pipeline = SimpleNamespace(
            engine=SimpleNamespace(
                armed=True,
                status=lambda: EngineStatus(
                    armed=self.pipeline.engine.armed,
                    raw_pose=Pose.NONE,
                    active_pose=Pose.NONE,
                    hold_progress=0.0,
                    hand_visible=False,
                    status_text="Ready",
                ),
            ),
            last_sample=None,
        )
        state["daemon"] = self

    @property
    def camera_enabled(self) -> bool:
        return self._camera_enabled

    @property
    def camera_state(self) -> str:
        return self._camera_state

    @property
    def camera_error(self) -> str | None:
        return self._camera_error

    @property
    def is_calibrating(self) -> bool:
        return False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def command(self, message: dict[str, Any] | str) -> list[dict[str, Any]]:
        assert isinstance(message, dict)
        name = message["name"]
        if name == "set_camera":
            self._camera_enabled = message["enabled"]
            self._camera_restart_requested = self._camera_enabled
            state = "starting" if self._camera_enabled else "off"
            self.set_camera_state(state)
        elif name == "retry_camera":
            self._camera_enabled = True
            self._camera_restart_requested = True
            self.set_camera_state("starting")
        else:
            raise AssertionError(f"unexpected command: {name}")
        return []

    def take_camera_restart_request(self) -> bool:
        requested = self._camera_restart_requested
        self._camera_restart_requested = False
        return requested

    def set_camera_state(
        self,
        state: str,
        error: str | None = None,
    ) -> list[dict[str, Any]]:
        self._camera_state = state
        self._camera_error = error
        self.camera_history.append((state, error))
        if state in {"off", "error"}:
            self._camera_restart_requested = False
            self.preview_enabled = False
            self.pipeline.engine.armed = False
            self.pause_reasons.append(
                "Paused - camera is off"
                if state == "off"
                else f"Paused - camera unavailable: {error}"
            )
        return []

    def feed(
        self,
        _observation: object,
        _now: float,
        _frame_brightness: float | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def force_pause(self, reason: str) -> list[dict[str, Any]]:
        self.pipeline.engine.armed = False
        self.pause_reasons.append(reason)
        return []

    def broadcast_preview(self, _jpeg: bytes) -> None:
        raise AssertionError("runtime camera tests should not stream previews")


class _Tracker:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.closed = False

    def detect_hands(
        self,
        _frame: object,
        _timestamp_ms: int,
    ) -> tuple[object, ...]:
        return ()

    def close(self) -> None:
        self.closed = True


class _RepeatingCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.released = False

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self.released:
            return False, None
        return True, self.frame.copy()

    def release(self) -> None:
        self.released = True


def _patch_run_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
    worker_factory: object,
) -> None:
    class DaemonFactory(_RuntimeDaemon):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(state, *args, **kwargs)

    monkeypatch.setattr(app_module, "ActionController", _Controller)
    monkeypatch.setattr(app_module, "Daemon", DaemonFactory)
    monkeypatch.setattr(app_module, "AsyncVisionWorker", worker_factory)
    monkeypatch.setattr(app_module, "IpcServer", lambda *_args: object())
    monkeypatch.setattr(app_module, "ensure_metrics_consent", lambda *_args: None)
    monkeypatch.setattr(
        app_module,
        "ensure_hand_model",
        lambda path, **_kwargs: path,
    )


def _headless_config() -> AppConfig:
    config = AppConfig.defaults()
    config.ipc.enabled = True
    return config


def test_headless_open_failure_is_nonfatal_and_retry_recovers(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, Any] = {"workers": [], "attempts": 0, "polls": 0}
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    def worker_factory(camera_config, tracking_config, model_path):
        attempt = state["attempts"]
        state["attempts"] += 1

        def camera_factory(_config):
            if attempt == 0:
                raise CameraError(FRIENDLY_CAMERA_ERROR)
            capture = _RepeatingCapture(frame)
            state["capture"] = capture
            return capture, frame.copy()

        worker = AsyncVisionWorker(
            camera_config,
            tracking_config,
            model_path,
            tracker_factory=_Tracker,
            camera_factory=camera_factory,
        )
        state["workers"].append(worker)
        return worker

    _patch_run_dependencies(monkeypatch, state, worker_factory)

    retried = False
    observed_nonfatal_error = False

    def should_stop() -> bool:
        nonlocal observed_nonfatal_error, retried
        state["polls"] += 1
        daemon = state["daemon"]
        if daemon.camera_state == "error" and not retried:
            assert daemon.started is True
            assert daemon.stopped is False
            assert daemon.pipeline.engine.armed is False
            observed_nonfatal_error = True
            retried = True
            daemon.command({"name": "retry_camera"})
        if daemon.camera_state == "active":
            return True
        assert state["polls"] < 1_000, "headless loop did not recover"
        return False

    result = app_module.run(
        _headless_config(),
        tmp_path,
        practice=True,
        should_stop=should_stop,
    )

    daemon = state["daemon"]
    assert result == 0
    assert state["attempts"] == 2
    assert observed_nonfatal_error is True
    assert daemon.started is True
    assert daemon.stopped is True
    assert daemon.pipeline.engine.armed is False
    assert ("error", FRIENDLY_CAMERA_ERROR) in daemon.camera_history
    assert daemon.camera_history[-1] == ("active", None)
    assert all(worker.stop(0.1) for worker in state["workers"])
    assert state["capture"].released is True


class _ScriptedWorker:
    scripts: list[list[SimpleNamespace]] = []
    instances: list[_ScriptedWorker] = []
    stop_callback: object | None = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.snapshots = iter(self.scripts.pop(0))
        self.last_snapshot: SimpleNamespace | None = None
        self.started = False
        self.stopped = False
        self.instances.append(self)

    def start(self) -> None:
        self.started = True

    def snapshot(self) -> SimpleNamespace:
        try:
            self.last_snapshot = next(self.snapshots)
        except StopIteration:
            assert self.last_snapshot is not None
        return self.last_snapshot

    def stop(self, _timeout: float) -> bool:
        callback = type(self).stop_callback
        if callable(callback):
            callback()
        self.stopped = True
        return True


def _run_until_camera_error(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    snapshots: list[SimpleNamespace],
    *,
    states_seen_at_stop: list[str] | None = None,
) -> _RuntimeDaemon:
    state: dict[str, Any] = {"polls": 0}
    _ScriptedWorker.scripts = [snapshots]
    _ScriptedWorker.instances = []
    _patch_run_dependencies(monkeypatch, state, _ScriptedWorker)
    _ScriptedWorker.stop_callback = (
        None
        if states_seen_at_stop is None
        else lambda: states_seen_at_stop.append(state["daemon"].camera_state)
    )

    def should_stop() -> bool:
        state["polls"] += 1
        daemon = state["daemon"]
        if daemon.camera_state == "error":
            return True
        assert state["polls"] < 20, "camera failure did not become recoverable state"
        return False

    assert app_module.run(
        config,
        tmp_path,
        practice=True,
        should_stop=should_stop,
    ) == 0
    assert _ScriptedWorker.instances[0].stopped is True
    return state["daemon"]


def test_headless_snapshot_error_becomes_recoverable_state(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states_seen_at_stop: list[str] = []
    daemon = _run_until_camera_error(
        tmp_path,
        monkeypatch,
        _headless_config(),
        [
            SimpleNamespace(
                sequence=-1,
                frame=None,
                observation=None,
                error=RuntimeError("tracker crashed"),
            )
        ],
        states_seen_at_stop=states_seen_at_stop,
    )

    assert daemon.camera_enabled is True
    assert daemon.camera_state == "error"
    assert daemon.camera_error == "Vision pipeline stopped: tracker crashed"
    assert daemon.pipeline.engine.armed is False
    assert daemon.preview_enabled is False
    assert states_seen_at_stop == ["error"]


def test_headless_startup_timeout_becomes_recoverable_state(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _headless_config()
    config.tracking.startup_timeout_seconds = 0.0

    daemon = _run_until_camera_error(
        tmp_path,
        monkeypatch,
        config,
        [
            SimpleNamespace(
                sequence=-1,
                frame=None,
                observation=None,
                error=None,
            )
        ],
    )

    assert daemon.camera_state == "error"
    assert daemon.camera_error == "Camera and hand tracking did not start in time"
    assert daemon.pipeline.engine.armed is False


def test_headless_watchdog_stall_becomes_recoverable_state(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _headless_config()
    config.tracking.watchdog_seconds = 0.0
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    stalled = SimpleNamespace(
        sequence=0,
        frame=frame,
        observation=None,
        error=None,
    )

    daemon = _run_until_camera_error(
        tmp_path,
        monkeypatch,
        config,
        [stalled, stalled],
    )

    assert ("active", None) in daemon.camera_history
    assert daemon.camera_state == "error"
    assert daemon.camera_error == "Camera feed stalled"
    assert daemon.pipeline.engine.armed is False


def test_headless_camera_off_stops_worker_without_stopping_daemon(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _headless_config()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    active = SimpleNamespace(
        sequence=0,
        frame=frame,
        observation=None,
        error=None,
    )
    state: dict[str, Any] = {"polls": 0, "turned_off": False}
    _ScriptedWorker.scripts = [[active]]
    _ScriptedWorker.instances = []
    _patch_run_dependencies(monkeypatch, state, _ScriptedWorker)

    def should_stop() -> bool:
        state["polls"] += 1
        daemon = state["daemon"]
        if daemon.camera_state == "active" and not state["turned_off"]:
            state["turned_off"] = True
            daemon.command({"name": "set_camera", "enabled": False})
            return False
        if (
            state["turned_off"]
            and daemon.camera_state == "off"
            and _ScriptedWorker.instances[0].stopped
        ):
            return True
        assert state["polls"] < 20, "camera off did not stop the worker"
        return False

    assert app_module.run(
        config,
        tmp_path,
        practice=True,
        should_stop=should_stop,
    ) == 0

    daemon = state["daemon"]
    assert daemon.started is True
    assert daemon.stopped is True
    assert daemon.camera_enabled is False
    assert daemon.camera_state == "off"
    assert daemon.pipeline.engine.armed is False
    assert _ScriptedWorker.instances[0].stopped is True
