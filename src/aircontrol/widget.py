"""Airy, AirControl's frameless always-on-top companion window."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from aircontrol.config import AppConfig
from aircontrol.daemon import default_store_path
from aircontrol.resources import resource_dir


_WIDTH = 190
_HEIGHT = 220
_DEFAULT_POSITION = (32, 48)
_ACTIVE = "#67e8f9"
_INACTIVE = "#8b9bad"
_OFFLINE = "#6b7280"
_DEFAULT_WS_URL = "ws://127.0.0.1:8787"

AIRY_HTML = resource_dir() / "ui" / "airy" / "index.html"
AIRY_ICON = resource_dir() / "packaging" / "airy.ico"
_APP_USER_MODEL_ID = "AirControl.App"


def _import_webview() -> Any:
    import webview

    return webview


def _set_windows_app_user_model_id() -> None:
    """Give standalone Airy a stable taskbar identity on Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _APP_USER_MODEL_ID
        )
    except Exception:
        pass


def _start_webview(webview: Any) -> None:
    """Start pywebview with Airy's icon, falling back for older APIs."""
    try:
        webview.start(icon=str(AIRY_ICON))
    except TypeError:
        webview.start()


def widget_state(connected: bool, armed: bool) -> tuple[str, str]:
    """Map daemon connectivity and arming truth to a labelled Airy state."""
    if not connected:
        return "OFFLINE", _OFFLINE
    if armed:
        return "ACTIVE", _ACTIVE
    return "INACTIVE", _INACTIVE


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


def _coordinate(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("window coordinates must be numbers")
    return int(value)


class AiryApi:
    """Small Python bridge for closing Airy and its drag fallback."""

    def __init__(
        self,
        position_path: str | Path,
        initial_position: tuple[int, int],
        ws_url: str = _DEFAULT_WS_URL,
        native_drag: bool = False,
    ) -> None:
        self._position_path = Path(position_path)
        self._x, self._y = initial_position
        self._ws_url = ws_url
        self._native_drag = native_drag
        self._window: Any = None
        self._lock = threading.RLock()

    def bind_window(self, window: Any) -> None:
        with self._lock:
            self._window = window

    def get_bootstrap(self) -> dict[str, str | bool]:
        """Return the runtime values Airy needs after its local page loads."""
        return {
            "ws_url": self._ws_url,
            "native_drag": self._native_drag,
        }

    def on_moved(self, x: object, y: object) -> None:
        """Record a native move event without querying the closing window."""
        next_x = _coordinate(x)
        next_y = _coordinate(y)
        with self._lock:
            self._x = next_x
            self._y = next_y
        self._persist()

    def on_closing(self, *_args: object) -> None:
        """Persist the last move-event coordinates before native teardown."""
        self._persist()
        return None

    def move_window(self, x: object, y: object) -> bool:
        """Move Airy to absolute screen coordinates for the JS drag fallback."""
        next_x = _coordinate(x)
        next_y = _coordinate(y)
        with self._lock:
            self._x = next_x
            self._y = next_y
            window = self._window
        if window is None:
            return False
        window.move(next_x, next_y)
        return True

    def close_widget(self) -> bool:
        """Close only the Airy window, leaving an integrated app open."""
        self._persist()
        with self._lock:
            window = self._window
        if window is None:
            return False
        window.destroy()
        return True

    def _persist(self) -> None:
        with self._lock:
            x = self._x
            y = self._y
        try:
            save_widget_position(self._position_path, x, y)
        except OSError as exc:
            print(
                f"Airy position could not be saved: {exc}",
                file=sys.stderr,
            )


def _subscribe(window: Any, event_name: str, handler: Any) -> None:
    events = getattr(window, "events", None)
    event = getattr(events, event_name, None) if events is not None else None
    if event is None:
        return
    try:
        event += handler
    except (AttributeError, TypeError):
        return


def _enable_native_drag_regions(webview: Any) -> bool:
    settings = getattr(webview, "settings", None)
    try:
        if (
            settings is not None
            and "DRAG_REGION_DIRECT_TARGET_ONLY" in settings
        ):
            settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
            return True
    except (KeyError, TypeError):
        pass
    return False


def create_airy_window(
    webview: Any,
    config: AppConfig,
    ws_url: str | None = None,
) -> Any:
    """Create Airy inside an existing pywebview session without starting it."""
    asset = AIRY_HTML
    if not asset.is_file():
        raise FileNotFoundError(f"Airy UI asset not found: {asset}")

    position_path = default_store_path().parent / "widget.json"
    position = load_widget_position(position_path, _DEFAULT_POSITION)
    native_drag = _enable_native_drag_regions(webview)
    runtime_ws_url = ws_url or f"ws://{config.ipc.host}:{config.ipc.port}"
    api = AiryApi(
        position_path,
        initial_position=position,
        ws_url=runtime_ws_url,
        native_drag=native_drag,
    )
    url = asset.resolve().as_uri()
    options: dict[str, Any] = {
        "width": _WIDTH,
        "height": _HEIGHT,
        "min_size": (_WIDTH, _HEIGHT),
        "x": position[0],
        "y": position[1],
        "resizable": False,
        "frameless": True,
        "easy_drag": False,
        "on_top": True,
        "background_color": "#0b111c",
        "js_api": api,
        "transparent": True,
    }
    try:
        window = webview.create_window("Airy", url, **options)
    except TypeError:
        # Older pywebview backends may not accept the transparency flag. The
        # opaque background matches the card, so Airy remains usable.
        options.pop("transparent")
        window = webview.create_window("Airy", url, **options)

    api.bind_window(window)
    _subscribe(window, "moved", api.on_moved)
    _subscribe(window, "closing", api.on_closing)
    return window


def run_widget(config: AppConfig, ws_url: str | None = None) -> int:
    """Run Airy as a standalone pywebview companion."""
    _set_windows_app_user_model_id()
    if not AIRY_HTML.is_file():
        print(
            f"Airy could not start because its index.html asset is missing: "
            f"{AIRY_HTML}",
            file=sys.stderr,
        )
        return 2

    try:
        webview = _import_webview()
    except ImportError:
        print(
            "The Airy companion needs the 'pywebview' package -- "
            "run setup.cmd to repair your environment.",
            file=sys.stderr,
        )
        return 2

    try:
        create_airy_window(webview, config, ws_url=ws_url)
        _start_webview(webview)
    except Exception as exc:
        print(
            f"Airy could not open its desktop window: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


__all__ = [
    "AIRY_HTML",
    "AIRY_ICON",
    "AiryApi",
    "create_airy_window",
    "load_widget_position",
    "run_widget",
    "save_widget_position",
    "widget_state",
]
