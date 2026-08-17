from __future__ import annotations

import asyncio
import builtins
import inspect
import json
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import aircontrol.app
from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.domain import EngineStatus, Pose
from aircontrol.ipc import (
    FakeTransport,
    IpcProtocolError,
    IpcServer,
    ack_event,
    parse_command,
)
from aircontrol.store import Store


@contextmanager
def _daemon(*, ipc: object | None = None) -> Iterator[Daemon]:
    config = AppConfig.defaults()
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


def test_parse_command_accepts_set_preview_with_boolean_enabled() -> None:
    payload = {
        "v": 1,
        "type": "command",
        "name": "set_preview",
        "enabled": True,
    }

    assert parse_command(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 1, "type": "command", "name": "set_preview"},
        {
            "v": 1,
            "type": "command",
            "name": "set_preview",
            "enabled": None,
        },
        {
            "v": 1,
            "type": "command",
            "name": "set_preview",
            "enabled": 1,
        },
        {
            "v": 1,
            "type": "command",
            "name": "set_preview",
            "enabled": "true",
        },
    ],
    ids=["missing", "null", "integer", "string"],
)
def test_parse_command_rejects_set_preview_without_boolean_enabled(
    payload: dict[str, object],
) -> None:
    with pytest.raises(IpcProtocolError):
        parse_command(json.dumps(payload))


def test_fake_transport_records_binary_broadcasts() -> None:
    transport = FakeTransport()

    transport.broadcast_binary(b"jpeg")

    assert transport.binary_messages == [b"jpeg"]


def test_ipc_server_broadcast_binary_sends_exact_bytes() -> None:
    payload = b"\xff\xd8jpeg\xff\xd9"

    async def exercise() -> list[object]:
        sent: list[object] = []
        delivered = asyncio.Event()

        class Client:
            async def send(self, data: object) -> None:
                sent.append(data)
                delivered.set()

        server = IpcServer(port=0)
        server._loop = asyncio.get_running_loop()
        server._broadcast_lock = asyncio.Lock()
        server._clients.add(Client())

        server.broadcast_binary(payload)
        await asyncio.wait_for(delivered.wait(), timeout=1.0)
        await asyncio.sleep(0)
        return sent

    assert asyncio.run(exercise()) == [payload]


def test_ipc_server_still_rejects_oversized_inbound_text() -> None:
    commands: list[dict[str, Any]] = []
    replies: list[str] = []

    class Client:
        async def send(self, data: str) -> None:
            replies.append(data)

    server = IpcServer(port=0)
    server.on_command(commands.append)
    raw = json.dumps(
        {
            "v": 1,
            "type": "command",
            "name": "get_status",
            "padding": "x" * 70_000,
        }
    )

    asyncio.run(server._handle_inbound(Client(), raw))

    assert commands == []
    assert len(replies) == 1
    assert json.loads(replies[0])["type"] == "error"


def test_fresh_daemon_disables_preview_by_default() -> None:
    with _daemon() as daemon:
        assert daemon.preview_enabled is False


def test_daemon_set_preview_updates_state_and_acknowledges() -> None:
    with _daemon() as daemon:
        enabled_events = daemon.command(
            {"name": "set_preview", "enabled": True, "id": "preview-on"}
        )

        assert daemon.preview_enabled is True
        assert enabled_events == [ack_event("preview-on", True)]

        disabled_events = daemon.command(
            {"name": "set_preview", "enabled": False, "id": "preview-off"}
        )

        assert daemon.preview_enabled is False
        assert disabled_events == [ack_event("preview-off", True)]


def test_daemon_broadcast_preview_is_gated_by_preview_state() -> None:
    transport = FakeTransport()
    with _daemon(ipc=transport) as daemon:
        daemon.broadcast_preview(b"disabled")
        assert transport.binary_messages == []

        daemon.preview_enabled = True
        daemon.broadcast_preview(b"jpeg")

        assert transport.binary_messages == [b"jpeg"]


def _patch_headless_run_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    preview_enabled: bool,
    quit_after_feeds: int,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "broadcasts": [],
        "feed_observations": [],
        "frame": np.full((480, 960, 3), 17, dtype=np.uint8),
        "observation": object(),
        "sample": object(),
        "status": EngineStatus(
            armed=False,
            raw_pose=Pose.NONE,
            active_pose=Pose.NONE,
            hold_progress=0.0,
            hand_visible=False,
            status_text="Ready",
        ),
    }

    class Controller:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.released = False
            self.closed = False
            state["controller"] = self

        def release_all(self) -> None:
            self.released = True

        def close(self) -> None:
            self.closed = True

    class FakeDaemon:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.preview_enabled = preview_enabled
            self.quit_requested = False
            self.camera_enabled = False
            self.camera_state = "off"
            self.camera_error = None
            self.camera_restart_requested = False
            self.is_recording = False
            self.is_calibrating = False
            self.feed_calls = 0
            self.started = False
            self.stopped = False
            self.pause_reasons: list[str] = []
            self.pipeline = SimpleNamespace(
                engine=SimpleNamespace(status=lambda: state["status"]),
                last_sample=state["sample"],
            )
            state["daemon"] = self

        def start(self) -> None:
            self.started = True

        def command(self, message: dict[str, Any]) -> list[dict[str, Any]]:
            assert message == {"name": "set_camera", "enabled": True}
            self.camera_enabled = True
            self.camera_state = "starting"
            self.camera_restart_requested = True
            return []

        def take_camera_restart_request(self) -> bool:
            requested = self.camera_restart_requested
            self.camera_restart_requested = False
            return requested

        def set_camera_state(
            self,
            camera_state: str,
            camera_error: str | None = None,
        ) -> list[dict[str, Any]]:
            self.camera_state = camera_state
            self.camera_error = camera_error
            if camera_state in {"off", "error"}:
                self.preview_enabled = False
            return []

        def feed(
            self,
            observations: object,
            _now: float,
            _frame_brightness: float | None = None,
        ) -> list[dict[str, Any]]:
            state["feed_observations"].append(observations)
            self.feed_calls += 1
            if self.feed_calls >= quit_after_feeds:
                self.quit_requested = True
            return []

        def force_pause(self, reason: str) -> list[dict[str, Any]]:
            self.pause_reasons.append(reason)
            return []

        def stop(self) -> None:
            self.stopped = True

        def broadcast_preview(self, jpeg: bytes) -> None:
            state["broadcasts"].append(jpeg)

    class Worker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.sequence = -1
            self.started = False
            self.stopped = False
            state["worker"] = self

        def start(self) -> None:
            self.started = True

        def snapshot(self) -> SimpleNamespace:
            self.sequence += 1
            fields = {
                "sequence": self.sequence,
                "frame": state["frame"],
                "observation": state["observation"],
                "error": None,
            }
            if "observations" in state:
                fields["observations"] = state["observations"]
            return SimpleNamespace(**fields)

        def stop(self, _timeout: float) -> bool:
            self.stopped = True
            return True

    monkeypatch.setattr(aircontrol.app, "ActionController", Controller)
    monkeypatch.setattr(aircontrol.app, "Daemon", FakeDaemon)
    monkeypatch.setattr(aircontrol.app, "AsyncVisionWorker", Worker)
    monkeypatch.setattr(aircontrol.app, "IpcServer", lambda *_args: object())
    monkeypatch.setattr(aircontrol.app, "ensure_metrics_consent", lambda *_args: None)
    monkeypatch.setattr(
        aircontrol.app,
        "ensure_hand_model",
        lambda path, **_kwargs: path,
    )
    return state


def _forbid_highgui(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("headless run called an OpenCV window API")

    for name in (
        "namedWindow",
        "imshow",
        "moveWindow",
        "resizeWindow",
        "setWindowProperty",
        "waitKey",
    ):
        monkeypatch.setattr(aircontrol.app.cv2, name, fail)
    monkeypatch.setattr(aircontrol.app, "_window_is_open", fail)


def test_run_with_ipc_is_headless_and_yields_without_rendering(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig.defaults()
    config.ipc.enabled = True
    state = _patch_headless_run_dependencies(
        monkeypatch,
        preview_enabled=False,
        quit_after_feeds=1,
    )
    _forbid_highgui(monkeypatch)

    class Overlay:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def note_action(self, _text: str) -> None:
            pass

        def draw(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("disabled preview performed overlay work")

    sleeps: list[float] = []
    monkeypatch.setattr(aircontrol.app, "GestureOverlay", Overlay)
    monkeypatch.setattr(aircontrol.app.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        aircontrol.app.cv2,
        "imencode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled preview encoded a frame")
        ),
    )

    result = aircontrol.app.run(config, tmp_path, practice=True)

    assert result == 0
    assert sleeps == [0.005]
    assert state["daemon"].started is True
    assert state["daemon"].stopped is True
    assert state["daemon"].pause_reasons == ["Stopped"]
    assert state["worker"].started is True
    assert state["worker"].stopped is True
    assert state["feed_observations"] == [(state["observation"],)]


def test_run_broadcasts_annotated_jpeg_preview_at_most_fifteen_fps(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig.defaults()
    config.ipc.enabled = True
    config.display.preview_width = 320
    state = _patch_headless_run_dependencies(
        monkeypatch,
        preview_enabled=True,
        quit_after_feeds=4,
    )
    state["observations"] = (state["observation"], object())
    _forbid_highgui(monkeypatch)

    rendered = np.full((480, 960, 3), 33, dtype=np.uint8)
    resized = np.full((160, 320, 3), 44, dtype=np.uint8)
    draw_calls: list[dict[str, Any]] = []
    resize_calls: list[tuple[object, int]] = []
    encode_calls: list[tuple[str, object, list[int]]] = []

    class Overlay:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def note_action(self, _text: str) -> None:
            pass

        def draw(self, *args: object, **kwargs: Any) -> np.ndarray:
            assert args == ()
            draw_calls.append(kwargs)
            return rendered

    def resize_preview(frame: object, width: int) -> np.ndarray:
        resize_calls.append((frame, width))
        return resized

    def imencode(
        extension: str,
        image: object,
        params: list[int],
    ) -> tuple[bool, np.ndarray]:
        encode_calls.append((extension, image, params))
        return True, np.frombuffer(b"jpeg", dtype=np.uint8)

    moments = iter([100.0, 100.0, 100.02, 100.04, 100.07])
    sleeps: list[float] = []
    monkeypatch.setattr(aircontrol.app, "GestureOverlay", Overlay)
    monkeypatch.setattr(aircontrol.app, "_resize_preview", resize_preview)
    monkeypatch.setattr(aircontrol.app.cv2, "imencode", imencode)
    monkeypatch.setattr(aircontrol.app.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(aircontrol.app.time, "sleep", sleeps.append)

    result = aircontrol.app.run(config, tmp_path, practice=True)

    assert result == 0
    assert len(draw_calls) == 2
    for call in draw_calls:
        assert call["frame"] is state["frame"]
        assert call["observation"] is state["observation"]
        assert call["sample"] is state["sample"]
        assert call["status"] is state["status"]
        assert call["fps"] >= 0.0
        assert call["practice"] is True
    assert len(resize_calls) == 2
    assert all(frame is rendered and width == 320 for frame, width in resize_calls)
    assert len(encode_calls) == 2
    for extension, image, params in encode_calls:
        assert extension == ".jpg"
        assert image is resized
        assert params == [int(aircontrol.app.cv2.IMWRITE_JPEG_QUALITY), 70]
    assert state["broadcasts"] == [b"jpeg", b"jpeg"]
    assert state["feed_observations"] == [state["observations"]] * 4
    assert sleeps == [0.005, 0.005, 0.005, 0.005]


def test_preview_path_does_not_write_frames_to_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "cv2.imwrite" not in inspect.getsource(aircontrol.app)

    transport = FakeTransport()
    with _daemon(ipc=transport) as daemon:
        daemon.preview_enabled = True

        def fail_open(*args: object, **kwargs: object) -> object:
            raise AssertionError("preview attempted to open a file")

        with monkeypatch.context() as scoped:
            scoped.setattr(builtins, "open", fail_open)
            daemon.broadcast_preview(b"jpeg")

        assert transport.binary_messages == [b"jpeg"]
