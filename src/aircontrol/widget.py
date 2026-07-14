"""Always-on-top armed/idle status widget for AirControl."""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from aircontrol.config import AppConfig
from aircontrol.daemon import default_store_path
from aircontrol.ipc import IpcClient, IpcProtocolError


_WIDTH = 132
_HEIGHT = 44
_BACKGROUND = "#171c22"
_TEXT = "#f4f6f8"
_ARMED = "#7dd3a0"
_IDLE = "#6b7280"
_OFFLINE = "#ef6b6b"
_STATE_POLL_MS = 75
_COMMAND_POLL_SECONDS = 0.075
_CONNECT_TIMEOUT_SECONDS = 2.0
_RETRY_INITIAL_SECONDS = 0.25
_RETRY_MAX_SECONDS = 3.0
_QUIT_SEND_TIMEOUT_SECONDS = 0.75
_DRAG_THRESHOLD_PIXELS = 4

StateUpdate = tuple[bool, bool]
PendingCommand = tuple[str, threading.Event]


def _import_tkinter() -> Any:
    import tkinter

    return tkinter


def widget_state(connected: bool, armed: bool) -> tuple[str, str]:
    """Map daemon state to the widget's visible text and dot color."""
    if not connected:
        return "OFFLINE", _OFFLINE
    if armed:
        return "ARMED", _ARMED
    return "IDLE", _IDLE


def load_widget_position(
    path: str | Path,
    default: tuple[int, int],
) -> tuple[int, int]:
    """Load a persisted widget position, falling back on invalid input."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return default
    if not isinstance(payload, dict):
        return default
    x = payload.get("x")
    y = payload.get("y")
    if type(x) is not int or type(y) is not int:
        return default
    return x, y


def save_widget_position(path: str | Path, x: int, y: int) -> None:
    """Persist the widget position as a small per-user JSON document."""
    position_path = Path(path)
    position_path.parent.mkdir(parents=True, exist_ok=True)
    position_path.write_text(
        json.dumps({"x": int(x), "y": int(y)}, separators=(",", ":")),
        encoding="utf-8",
    )


def _put_latest(states: queue.Queue[StateUpdate], state: StateUpdate) -> None:
    try:
        states.put_nowait(state)
        return
    except queue.Full:
        pass
    try:
        states.get_nowait()
    except queue.Empty:
        pass
    try:
        states.put_nowait(state)
    except queue.Full:
        pass


def _discard_pending_commands(
    commands: queue.Queue[PendingCommand],
) -> None:
    while True:
        try:
            _name, completed = commands.get_nowait()
        except queue.Empty:
            return
        completed.set()


async def _consume_connection(
    client: IpcClient,
    states: queue.Queue[StateUpdate],
    commands: queue.Queue[PendingCommand],
    stop_requested: threading.Event,
) -> None:
    reader = asyncio.create_task(anext(client))
    try:
        while not stop_requested.is_set():
            while True:
                try:
                    name, completed = commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    await asyncio.wait_for(
                        client.send_command(name),
                        timeout=_CONNECT_TIMEOUT_SECONDS,
                    )
                finally:
                    completed.set()

            done, _pending = await asyncio.wait(
                {reader},
                timeout=_COMMAND_POLL_SECONDS,
            )
            if reader not in done:
                continue
            try:
                event = reader.result()
            except IpcProtocolError:
                reader = asyncio.create_task(anext(client))
                continue
            except StopAsyncIteration as exc:
                raise ConnectionError("AirControl IPC disconnected") from exc

            if event.get("type") == "status" and type(event.get("armed")) is bool:
                _put_latest(states, (True, event["armed"]))
            reader = asyncio.create_task(anext(client))
    finally:
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


async def _stop_aware_sleep(
    stop_requested: threading.Event,
    delay: float,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + delay
    while not stop_requested.is_set():
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.1, remaining))


async def _ipc_loop(
    url: str,
    states: queue.Queue[StateUpdate],
    commands: queue.Queue[PendingCommand],
    stop_requested: threading.Event,
) -> None:
    retry_delay = _RETRY_INITIAL_SECONDS
    while not stop_requested.is_set():
        _discard_pending_commands(commands)
        client = IpcClient(url)
        try:
            await asyncio.wait_for(
                client.send_command("get_status"),
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
            retry_delay = _RETRY_INITIAL_SECONDS
            await _consume_connection(
                client,
                states,
                commands,
                stop_requested,
            )
        except Exception:
            pass
        finally:
            try:
                await client.close()
            except Exception:
                pass
            _put_latest(states, (False, False))
            _discard_pending_commands(commands)

        if not stop_requested.is_set():
            await _stop_aware_sleep(stop_requested, retry_delay)
            retry_delay = min(retry_delay * 2.0, _RETRY_MAX_SECONDS)


def _run_ipc_thread(
    url: str,
    states: queue.Queue[StateUpdate],
    commands: queue.Queue[PendingCommand],
    stop_requested: threading.Event,
) -> None:
    asyncio.run(_ipc_loop(url, states, commands, stop_requested))


def _geometry(x: int, y: int) -> str:
    return f"{_WIDTH}x{_HEIGHT}{x:+d}{y:+d}"


def run_widget(config: AppConfig, ws_url: str | None = None) -> int:
    """Show the standalone Tk status badge and connect it to the daemon."""
    try:
        tk = _import_tkinter()
    except ImportError:
        print(
            "The AirControl status widget needs tkinter, which is not "
            "available in this Python installation.",
            file=sys.stderr,
        )
        return 2

    try:
        root = tk.Tk()
    except Exception as exc:
        tcl_error = getattr(tk, "TclError", None)
        if isinstance(tcl_error, type) and isinstance(exc, tcl_error):
            print(
                "The AirControl status widget could not open a desktop display.",
                file=sys.stderr,
            )
            return 2
        raise

    root.title("AirControl status")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.92)
    root.configure(background=_BACKGROUND)
    root.resizable(False, False)
    root.update_idletasks()

    default_position = (
        max(0, root.winfo_screenwidth() - _WIDTH - 24),
        max(0, root.winfo_screenheight() - _HEIGHT - 48),
    )
    position_path = default_store_path().parent / "widget.json"
    x, y = load_widget_position(position_path, default_position)
    root.geometry(_geometry(x, y))

    canvas = tk.Canvas(
        root,
        width=_WIDTH,
        height=_HEIGHT,
        background=_BACKGROUND,
        borderwidth=0,
        highlightthickness=0,
    )
    canvas.pack(fill="both", expand=True)
    dot = canvas.create_oval(14, 15, 28, 29, width=2)
    label_item = canvas.create_text(
        38,
        22,
        anchor="w",
        fill=_TEXT,
        font=("Segoe UI", 10, "bold"),
        text="OFFLINE",
    )

    states: queue.Queue[StateUpdate] = queue.Queue(maxsize=1)
    commands: queue.Queue[PendingCommand] = queue.Queue()
    stop_requested = threading.Event()
    connection = {"connected": False, "armed": False}
    drag: dict[str, int | bool] = {
        "pointer_x": 0,
        "pointer_y": 0,
        "window_x": x,
        "window_y": y,
        "moved": False,
    }
    closing = False

    url = ws_url or f"ws://{config.ipc.host}:{config.ipc.port}"
    ipc_thread = threading.Thread(
        target=_run_ipc_thread,
        args=(url, states, commands, stop_requested),
        name="aircontrol-widget-ipc",
        daemon=True,
    )

    def render_state(connected: bool, armed: bool) -> None:
        label, color = widget_state(connected, armed)
        connection["connected"] = connected
        connection["armed"] = armed
        canvas.itemconfigure(label_item, text=label)
        if label == "OFFLINE":
            canvas.itemconfigure(
                dot,
                fill=_BACKGROUND,
                outline=color,
                width=2,
            )
        else:
            canvas.itemconfigure(dot, fill=color, outline=color, width=1)

    def poll_states() -> None:
        latest: StateUpdate | None = None
        while True:
            try:
                latest = states.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            render_state(*latest)
        if not closing:
            root.after(_STATE_POLL_MS, poll_states)

    def save_position() -> None:
        try:
            save_widget_position(
                position_path,
                root.winfo_x(),
                root.winfo_y(),
            )
        except OSError as exc:
            print(f"AirControl widget position could not be saved: {exc}", file=sys.stderr)

    def close_widget() -> None:
        nonlocal closing
        if closing:
            return
        closing = True
        save_position()
        stop_requested.set()
        root.destroy()

    def send_command(name: str) -> threading.Event:
        completed = threading.Event()
        commands.put((name, completed))
        return completed

    def on_press(event: Any) -> None:
        drag["pointer_x"] = int(event.x_root)
        drag["pointer_y"] = int(event.y_root)
        drag["window_x"] = root.winfo_x()
        drag["window_y"] = root.winfo_y()
        drag["moved"] = False

    def on_motion(event: Any) -> None:
        dx = int(event.x_root) - int(drag["pointer_x"])
        dy = int(event.y_root) - int(drag["pointer_y"])
        if (
            abs(dx) >= _DRAG_THRESHOLD_PIXELS
            or abs(dy) >= _DRAG_THRESHOLD_PIXELS
        ):
            drag["moved"] = True
        if drag["moved"]:
            root.geometry(
                _geometry(
                    int(drag["window_x"]) + dx,
                    int(drag["window_y"]) + dy,
                )
            )

    def on_release(_event: Any) -> None:
        if drag["moved"]:
            save_position()
        elif connection["connected"]:
            send_command("toggle_arm")

    def quit_aircontrol() -> None:
        if not connection["connected"]:
            close_widget()
            return
        completed = send_command("quit")
        deadline = time.monotonic() + _QUIT_SEND_TIMEOUT_SECONDS

        def finish_when_sent() -> None:
            if completed.is_set() or time.monotonic() >= deadline:
                close_widget()
            else:
                root.after(25, finish_when_sent)

        finish_when_sent()

    menu = tk.Menu(root, tearoff=False)
    menu.add_command(label="Hide widget", command=close_widget)
    menu.add_command(label="Quit AirControl", command=quit_aircontrol)

    def open_menu(event: Any) -> None:
        try:
            menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    root.bind("<ButtonPress-1>", on_press)
    root.bind("<B1-Motion>", on_motion)
    root.bind("<ButtonRelease-1>", on_release)
    root.bind("<Button-3>", open_menu)
    root.protocol("WM_DELETE_WINDOW", close_widget)

    render_state(False, False)
    root.after(_STATE_POLL_MS, poll_states)
    ipc_thread.start()
    try:
        root.mainloop()
    finally:
        stop_requested.set()
        _discard_pending_commands(commands)
        ipc_thread.join(timeout=_CONNECT_TIMEOUT_SECONDS + 0.5)
        if not closing:
            save_position()
            try:
                root.destroy()
            except Exception:
                pass
    return 0


__all__ = [
    "load_widget_position",
    "run_widget",
    "save_widget_position",
    "widget_state",
]
