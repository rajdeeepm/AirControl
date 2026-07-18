"""Versioned loopback IPC messages and transports.

All protocol messages are JSON objects with ``"v": 1`` and a ``"type"``.
Servers accept command messages and broadcast status, action, candidate, and
metrics events.  The WebSocket dependency is imported only when a socket
transport is started or connected, so schema helpers and ``FakeTransport`` stay
usable without third-party packages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any


_PROTOCOL_VERSION = 1
_LOOPBACK_HOST = "127.0.0.1"
_DEFAULT_PORT = 8787
_MAX_MESSAGE_BYTES = 4 * 1_024 * 1_024
_MAX_INBOUND_TEXT_BYTES = 64 * 1_024
_START_TIMEOUT_SECONDS = 5.0
_STOP_TIMEOUT_SECONDS = 5.0
_STOP_POLL_SECONDS = 0.05
_COMMAND_NAMES = frozenset(
    {
        "toggle_arm",
        "pause",
        "undo",
        "quit",
        "get_status",
        "focus_dashboard",
        "refresh_matcher",
        "list_library",
        "set_mapping",
        "delete_gesture",
        "rename_gesture",
        "get_metrics",
        "get_settings",
        "get_app_settings",
        "set_app_setting",
        "reset_app_settings",
        "delete_everything",
        "set_preview",
        "set_camera",
        "retry_camera",
        "start_recording",
        "confirm_take",
        "discard_take",
        "finish_recording",
        "cancel_recording",
        "get_recording_state",
    }
)
_EVENT_TYPES = frozenset(
    {
        "status",
        "action",
        "candidate",
        "metrics",
        "error",
        "library",
        "metrics_snapshot",
        "settings",
        "app_settings",
        "ack",
        "camera",
        "recording",
    }
)
_RECORDING_PHASES = frozenset(
    {"inactive", "capturing", "pending_take", "saved", "refused"}
)

logger = logging.getLogger(__name__)

Message = dict[str, Any]
MessageCallback = Callable[[Message], None]


class IpcProtocolError(Exception):
    pass


def status_event(
    armed: bool,
    raw_pose: str,
    active_pose: str,
    hold_progress: float,
    hand_visible: bool,
    status_text: str,
) -> Message:
    return {
        "v": _PROTOCOL_VERSION,
        "type": "status",
        "armed": armed,
        "raw_pose": raw_pose,
        "active_pose": active_pose,
        "hold_progress": hold_progress,
        "hand_visible": hand_visible,
        "status_text": status_text,
    }


def action_event(
    kind: str,
    confidence: float,
    description: str,
    ts: float,
    category: str = "hotkey",
) -> Message:
    """Build an action event with a semantic category for feedback clients.

    ``kind`` remains the dispatched action kind. ``category`` is the coarser,
    derived meaning clients can use without guessing from descriptions.
    """
    return {
        "v": _PROTOCOL_VERSION,
        "type": "action",
        "kind": kind,
        "category": category,
        "confidence": confidence,
        "description": description,
        "ts": ts,
    }


def candidate_event(
    gate: str,
    reason: str,
    confidence: float,
    ts: float,
) -> Message:
    if gate not in {"fire", "abstain"}:
        raise IpcProtocolError("candidate gate must be 'fire' or 'abstain'")
    return {
        "v": _PROTOCOL_VERSION,
        "type": "candidate",
        "gate": gate,
        "reason": reason,
        "confidence": confidence,
        "ts": ts,
    }


def metrics_event(
    candidates_per_hour: float,
    fp_per_hour: float,
    latency_ms_p50: float,
    cpu_pct: float,
    ts: float,
) -> Message:
    return {
        "v": _PROTOCOL_VERSION,
        "type": "metrics",
        "candidates_per_hour": candidates_per_hour,
        "fp_per_hour": fp_per_hour,
        "latency_ms_p50": latency_ms_p50,
        "cpu_pct": cpu_pct,
        "ts": ts,
    }


def camera_event(
    state: str,
    error: str | None = None,
    id: str | None = None,
) -> Message:
    if state not in {"off", "starting", "active", "error"}:
        raise IpcProtocolError(
            "camera state must be 'off', 'starting', 'active', or 'error'"
        )
    event: Message = {
        "v": _PROTOCOL_VERSION,
        "type": "camera",
        "state": state,
        "camera_error": error,
    }
    return _with_correlation_id(event, id)


def recording_event(
    phase: str,
    name: str,
    takes_confirmed: int,
    min_takes: int,
    max_takes: int,
    pending_take: bool = False,
    pending_take_frames: int | None = None,
    outcome: dict[str, Any] | None = None,
    id: str | None = None,
) -> Message:
    if phase not in _RECORDING_PHASES:
        raise IpcProtocolError(
            "recording phase must be 'inactive', 'capturing', "
            "'pending_take', 'saved', or 'refused'"
        )
    event: Message = {
        "v": _PROTOCOL_VERSION,
        "type": "recording",
        "phase": phase,
        "name": name,
        "takes_confirmed": takes_confirmed,
        "min_takes": min_takes,
        "max_takes": max_takes,
        "pending_take": pending_take,
        "pending_take_frames": pending_take_frames,
        "outcome": outcome,
    }
    return _with_correlation_id(event, id)


def library_event(
    gestures: list[dict[str, Any]],
    id: str | None = None,
) -> Message:
    event: Message = {
        "v": _PROTOCOL_VERSION,
        "type": "library",
        "gestures": gestures,
    }
    return _with_correlation_id(event, id)


def metrics_snapshot_event(
    candidates_per_hour: float,
    armed_candidates_per_hour: float,
    fp_per_hour: float,
    latency_ms_p50: float,
    latency_ms_p95: float,
    cpu_pct: float,
    uptime_seconds: float,
    id: str | None = None,
) -> Message:
    event: Message = {
        "v": _PROTOCOL_VERSION,
        "type": "metrics_snapshot",
        "candidates_per_hour": candidates_per_hour,
        "armed_candidates_per_hour": armed_candidates_per_hour,
        "fp_per_hour": fp_per_hour,
        "latency_ms_p50": latency_ms_p50,
        "latency_ms_p95": latency_ms_p95,
        "cpu_pct": cpu_pct,
        "uptime_seconds": uptime_seconds,
    }
    return _with_correlation_id(event, id)


def settings_event(
    payload: dict[str, Any],
    id: str | None = None,
) -> Message:
    event: Message = {
        "v": _PROTOCOL_VERSION,
        "type": "settings",
        "payload": payload,
    }
    return _with_correlation_id(event, id)


def app_settings_event(
    settings: dict[str, Any],
    id: str | None = None,
) -> Message:
    event: Message = {
        "v": _PROTOCOL_VERSION,
        "type": "app_settings",
        "settings": settings,
    }
    return _with_correlation_id(event, id)


def ack_event(
    id: str | None,
    ok: bool,
    error: str = "",
) -> Message:
    event: Message = {
        "v": _PROTOCOL_VERSION,
        "type": "ack",
        "ok": ok,
        "error": error,
    }
    return _with_correlation_id(event, id)


def parse_command(raw: str) -> Message:
    """Parse and validate a v1 client command."""
    message = _decode_json_object(raw)
    _require_v1(message)
    return _validate_command(message)


class FakeTransport:
    """Synchronous in-process command and event pub/sub transport."""

    def __init__(self) -> None:
        self._subscribers: list[MessageCallback] = []
        self._command_callback: MessageCallback | None = None
        self.binary_messages: list[bytes] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: MessageCallback) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def broadcast(self, event: Message) -> None:
        _validate_message_envelope(event)
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(dict(event))
            except Exception:
                logger.exception("IPC event subscriber failed")

    def broadcast_binary(self, data: bytes) -> None:
        with self._lock:
            self.binary_messages.append(data)

    def on_command(self, callback: MessageCallback) -> None:
        with self._lock:
            self._command_callback = callback

    def send_command(self, name: str) -> None:
        command = _command_message(name)
        with self._lock:
            callback = self._command_callback
        if callback is not None:
            callback(command)


class IpcServer:
    """Loopback WebSocket server running on a private background event loop."""

    def __init__(self, host: str = _LOOPBACK_HOST, port: int = _DEFAULT_PORT) -> None:
        if host != _LOOPBACK_HOST:
            raise ValueError(f"IPC server host must be {_LOOPBACK_HOST}")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
            raise ValueError("IPC server port must be an integer from 0 to 65535")

        self.host = host
        self.port = port
        self._requested_port = port
        self._command_callback: MessageCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._clients: set[Any] = set()
        self._broadcast_lock: asyncio.Lock | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._startup_error: BaseException | None = None
        self._state_lock = threading.Lock()

    def on_command(self, callback: MessageCallback) -> None:
        self._command_callback = callback

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None:
                raise RuntimeError("IPC server has already started")
            self._ready.clear()
            self._stop_requested.clear()
            self._startup_error = None
            self._thread = threading.Thread(
                target=self._run,
                name="aircontrol-ipc",
                daemon=True,
            )
            thread = self._thread

        thread.start()
        if not self._ready.wait(_START_TIMEOUT_SECONDS):
            self.stop()
            raise RuntimeError("timed out starting IPC server")
        if self._startup_error is not None:
            error = self._startup_error
            thread.join(timeout=_STOP_TIMEOUT_SECONDS)
            with self._state_lock:
                self._thread = None
            raise error

    def stop(self) -> None:
        with self._state_lock:
            thread = self._thread
            loop = self._loop
        if thread is None:
            return
        self._stop_requested.set()
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:
                pass
        if threading.current_thread() is thread:
            return

        thread.join(timeout=_STOP_TIMEOUT_SECONDS)
        if thread.is_alive():
            raise RuntimeError("timed out stopping IPC server")
        with self._state_lock:
            self._thread = None

    def broadcast(self, event: Message) -> None:
        _validate_message_envelope(event)
        try:
            encoded = json.dumps(event, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise IpcProtocolError("event must be JSON serializable") from exc

        loop = self._loop
        if loop is None or not loop.is_running():
            return

        def schedule() -> None:
            task = asyncio.create_task(self._broadcast(encoded))
            task.add_done_callback(_log_background_task_error)

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            pass

    def broadcast_binary(self, data: bytes) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        def schedule() -> None:
            task = asyncio.create_task(self._broadcast(data))
            task.add_done_callback(_log_background_task_error)

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            pass

    async def _start_async(self) -> None:
        import websockets

        self._broadcast_lock = asyncio.Lock()
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self._requested_port,
            max_size=_MAX_MESSAGE_BYTES,
        )
        sockets = self._server.sockets
        if not sockets:
            raise RuntimeError("IPC server did not create a listening socket")
        self.port = int(sockets[0].getsockname()[1])

    async def _handle_connection(self, websocket: Any, *_legacy_path: object) -> None:
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                await self._handle_inbound(websocket, raw)
        except Exception:
            logger.debug("IPC client disconnected", exc_info=True)
        finally:
            self._clients.discard(websocket)

    async def _handle_inbound(self, websocket: Any, raw: object) -> None:
        try:
            message = _decode_inbound_json_object(raw)
        except IpcProtocolError as exc:
            logger.warning("Ignoring malformed IPC message: %s", exc)
            await self._send_error(websocket, exc)
            return

        try:
            _require_v1(message)
        except IpcProtocolError as exc:
            logger.warning("Rejecting IPC message: %s", exc)
            await self._send_error(websocket, exc)
            return

        if message.get("type") != "command":
            logger.warning(
                "Ignoring IPC message with unknown type %r",
                message.get("type"),
            )
            return

        try:
            command = _validate_command(message)
        except IpcProtocolError as exc:
            logger.warning("Ignoring malformed IPC command: %s", exc)
            await self._send_error(websocket, exc)
            return

        callback = self._command_callback
        if callback is not None:
            try:
                callback(command)
            except Exception:
                logger.exception("IPC command callback failed")

    async def _send_error(self, websocket: Any, error: IpcProtocolError) -> None:
        message = {
            "v": _PROTOCOL_VERSION,
            "type": "error",
            "message": str(error),
        }
        try:
            await websocket.send(json.dumps(message, separators=(",", ":")))
        except Exception:
            logger.debug("Could not send IPC protocol error", exc_info=True)

    async def _broadcast(self, encoded: str | bytes) -> None:
        broadcast_lock = self._broadcast_lock
        if broadcast_lock is None:
            return
        async with broadcast_lock:
            clients = tuple(self._clients)
            if not clients:
                return
            results = await asyncio.gather(
                *(client.send(encoded) for client in clients),
                return_exceptions=True,
            )
            for client, result in zip(clients, results):
                if isinstance(result, BaseException):
                    self._clients.discard(client)
                    logger.debug("IPC broadcast failed", exc_info=result)

    async def _shutdown_async(self) -> None:
        server = self._server
        if server is not None:
            server.close()
            await server.wait_closed()
        if self._clients:
            await asyncio.gather(
                *(client.close() for client in tuple(self._clients)),
                return_exceptions=True,
            )
        self._clients.clear()
        self._server = None
        self._broadcast_lock = None

    async def _wait_until_stopped(self) -> None:
        self._ready.set()
        while not self._stop_requested.is_set():
            await asyncio.sleep(_STOP_POLL_SECONDS)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_async())
            loop.run_until_complete(self._wait_until_stopped())
        except BaseException as exc:
            if not self._ready.is_set():
                self._startup_error = exc
            else:
                logger.exception("IPC server loop failed")
        finally:
            try:
                loop.run_until_complete(self._shutdown_async())
            except Exception:
                logger.exception("IPC server shutdown failed")

            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            with self._state_lock:
                self._loop = None
                if self._thread is threading.current_thread():
                    self._thread = None
            self._ready.set()
            asyncio.set_event_loop(None)
            loop.close()


class IpcClient:
    """Lazy async WebSocket client and event iterator."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._websocket: Any = None
        self._connect_lock = asyncio.Lock()
        self._closed = False

    async def send_command(self, name: str) -> None:
        command = _command_message(name)
        websocket = await self._ensure_connected()
        await websocket.send(json.dumps(command, separators=(",", ":")))

    def __aiter__(self) -> IpcClient:
        return self

    async def __anext__(self) -> Message:
        websocket = await self._ensure_connected()
        while True:
            try:
                raw = await websocket.recv()
            except Exception as exc:
                if _is_websocket_disconnect(exc):
                    raise StopAsyncIteration from None
                raise

            message = _decode_json_object(raw)
            _require_v1(message)
            message_type = message.get("type")
            if message_type not in _EVENT_TYPES:
                logger.warning(
                    "Ignoring IPC message with unknown type %r",
                    message_type,
                )
                continue
            return message

    async def close(self) -> None:
        self._closed = True
        websocket = self._websocket
        self._websocket = None
        if websocket is not None:
            await websocket.close()

    async def __aenter__(self) -> IpcClient:
        await self._ensure_connected()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def _ensure_connected(self) -> Any:
        if self._closed:
            raise RuntimeError("IPC client is closed")
        if self._websocket is not None:
            return self._websocket
        async with self._connect_lock:
            if self._websocket is None:
                import websockets

                self._websocket = await websockets.connect(
                    self.url,
                    max_size=_MAX_MESSAGE_BYTES,
                )
        return self._websocket


def _decode_json_object(raw: object) -> Message:
    if not isinstance(raw, str):
        raise IpcProtocolError("IPC message must be JSON text")
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise IpcProtocolError("IPC message is not valid JSON") from exc
    if not isinstance(message, dict):
        raise IpcProtocolError("IPC message must be a JSON object")
    return message


def _decode_inbound_json_object(raw: object) -> Message:
    if (
        isinstance(raw, str)
        and len(raw.encode("utf-8")) > _MAX_INBOUND_TEXT_BYTES
    ):
        raise IpcProtocolError("IPC command exceeds the maximum size")
    return _decode_json_object(raw)


def _require_v1(message: Message) -> None:
    version = message.get("v")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise IpcProtocolError(f"unsupported IPC version: {version!r}")


def _validate_command(message: Message) -> Message:
    if message.get("type") != "command":
        raise IpcProtocolError("message type must be 'command'")
    name = message.get("name")
    if not isinstance(name, str) or name not in _COMMAND_NAMES:
        raise IpcProtocolError("command name is missing or unsupported")

    if "id" in message and not isinstance(message["id"], str):
        raise IpcProtocolError("command id must be a string")

    if name == "set_mapping":
        _require_gesture_id(message)
        if not isinstance(message.get("action"), dict):
            raise IpcProtocolError("set_mapping action must be an object")
        if "context" in message and not isinstance(message["context"], str):
            raise IpcProtocolError("set_mapping context must be a string")
        if "enabled" in message and type(message["enabled"]) is not bool:
            raise IpcProtocolError("set_mapping enabled must be a boolean")
    elif name == "delete_gesture":
        _require_gesture_id(message)
    elif name == "rename_gesture":
        _require_gesture_id(message)
        new_name = message.get("new_name")
        if not isinstance(new_name, str) or not new_name:
            raise IpcProtocolError("rename_gesture new_name must be non-empty")
    elif name == "set_app_setting":
        if not isinstance(message.get("key"), str):
            raise IpcProtocolError("set_app_setting key must be a string")
        if "value" not in message:
            raise IpcProtocolError("set_app_setting value is required")
    elif name == "set_preview":
        if not isinstance(message.get("enabled"), bool):
            raise IpcProtocolError("set_preview enabled must be a boolean")
    elif name == "set_camera":
        if not isinstance(message.get("enabled"), bool):
            raise IpcProtocolError("set_camera enabled must be a boolean")
    elif name == "start_recording":
        gesture_name = message.get("gesture_name")
        if not isinstance(gesture_name, str) or not gesture_name.strip():
            raise IpcProtocolError(
                "start_recording gesture_name must be non-empty"
            )
    return message


def _require_gesture_id(message: Message) -> int:
    gesture_id = message.get("gesture_id")
    if isinstance(gesture_id, bool) or not isinstance(gesture_id, int):
        raise IpcProtocolError("command gesture_id must be an integer")
    return gesture_id


def _with_correlation_id(event: Message, id: str | None) -> Message:
    if id is not None:
        event["id"] = id
    return event


def _command_message(name: str) -> Message:
    return _validate_command(
        {"v": _PROTOCOL_VERSION, "type": "command", "name": name}
    )


def _validate_message_envelope(message: object) -> None:
    if not isinstance(message, dict):
        raise IpcProtocolError("IPC message must be a dict")
    _require_v1(message)
    if not isinstance(message.get("type"), str):
        raise IpcProtocolError("IPC message type must be a string")


def _is_websocket_disconnect(error: BaseException) -> bool:
    try:
        import websockets
    except ImportError:
        return False
    return isinstance(error, websockets.exceptions.ConnectionClosed)


def _log_background_task_error(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "IPC background broadcast failed",
            exc_info=(type(error), error, error.__traceback__),
        )
