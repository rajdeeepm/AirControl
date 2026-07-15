"""Native pywebview shell for the built AirControl app UI."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from aircontrol import settings as app_settings
from aircontrol.config import AppConfig
from aircontrol.daemon import default_store_path
from aircontrol.resources import resource_dir
from aircontrol.store import Store
from aircontrol.widget import create_airy_window
from scripts.serve_ui import BUILD_COMMAND, HOST, create_server


DIST_DIR = resource_dir() / "ui" / "dist"
AIRY_ICON = resource_dir() / "packaging" / "airy.ico"
_APP_USER_MODEL_ID = "AirControl.App"
_FOCUS_POLL_SECONDS = 0.05


def _import_webview() -> Any:
    import webview

    return webview


def _set_windows_app_user_model_id() -> None:
    """Give Windows a stable taskbar identity without affecting other platforms."""
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


def _load_airy_enabled(config: AppConfig) -> bool:
    """Read Airy's persisted visibility preference from the app store."""
    configured_path = config.store.db_path
    store_path = (
        Path(configured_path).expanduser()
        if configured_path
        else default_store_path()
    )
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with Store(store_path) as store:
            return app_settings.load(store)["airy_enabled"] is True
    except Exception as exc:
        print(
            "Airy's saved preference could not be read; showing the "
            f"companion by default ({type(exc).__name__}: {exc}).",
            file=sys.stderr,
        )
        return True


def _close_with_main(main_window: Any, airy_window: Any) -> None:
    events = getattr(main_window, "events", None)
    closed = getattr(events, "closed", None) if events is not None else None
    if closed is None:
        return

    def close_airy(*_args: object) -> None:
        try:
            airy_window.destroy()
        except Exception:
            pass

    try:
        closed += close_airy
    except (AttributeError, TypeError):
        pass


def _consume_focus_request(daemon: Any, window: Any) -> bool:
    """Consume one dashboard-focus request and best-effort raise the window."""
    try:
        requested = bool(daemon.take_focus_request())
    except Exception:
        return False

    if not requested:
        return False

    for method_name in ("show", "restore"):
        try:
            method = getattr(window, method_name, None)
            if callable(method):
                method()
        except Exception:
            pass
    return True


def run_app(
    config: AppConfig,
    config_directory: Path,
    practice: bool,
    model_override: str | None = None,
) -> int:
    """Run the daemon behind a native window that hosts the built UI."""
    _set_windows_app_user_model_id()
    if not DIST_DIR.is_dir():
        print(
            f"AirControl UI build not found at {DIST_DIR}. "
            f"Run `{BUILD_COMMAND}` first.",
            file=sys.stderr,
        )
        return 2

    try:
        webview = _import_webview()
    except ImportError:
        print(
            "The desktop app window needs the 'pywebview' package -- "
            "run setup.cmd to repair your environment.",
            file=sys.stderr,
        )
        return 2

    from aircontrol import app as app_module

    config.ipc.enabled = True
    airy_enabled = _load_airy_enabled(config)
    server = create_server(DIST_DIR, port=0)
    stop_requested = threading.Event()
    focus_poll_stopped = threading.Event()
    daemon_ready = threading.Event()
    daemon_results: list[int] = []
    daemon_errors: list[BaseException] = []
    captured_daemon: list[Any] = []
    original_daemon_factory = app_module.Daemon

    def capture_daemon(*args: Any, **kwargs: Any) -> Any:
        try:
            daemon = original_daemon_factory(*args, **kwargs)
            captured_daemon[:] = [daemon]
            daemon_ready.set()
            return daemon
        finally:
            if app_module.Daemon is capture_daemon:
                app_module.Daemon = original_daemon_factory

    def run_daemon() -> None:
        app_module.Daemon = capture_daemon
        try:
            daemon_results.append(
                app_module.run(
                    config=config,
                    config_directory=config_directory,
                    practice=practice,
                    model_override=model_override,
                    should_stop=stop_requested.is_set,
                )
            )
        except BaseException as exc:
            daemon_errors.append(exc)
        finally:
            if app_module.Daemon is capture_daemon:
                app_module.Daemon = original_daemon_factory
            daemon_ready.set()

    def poll_focus_requests(main_window: Any) -> None:
        while not focus_poll_stopped.is_set():
            if daemon_ready.wait(_FOCUS_POLL_SECONDS) and captured_daemon:
                _consume_focus_request(captured_daemon[0], main_window)
            focus_poll_stopped.wait(_FOCUS_POLL_SECONDS)

    server_thread = threading.Thread(
        target=server.serve_forever,
        name="aircontrol-ui-server",
        daemon=True,
    )
    daemon_thread = threading.Thread(
        target=run_daemon,
        name="aircontrol-daemon",
    )
    focus_thread: threading.Thread | None = None
    server_thread.start()
    daemon_thread.start()

    try:
        url = f"http://{HOST}:{server.server_port}/"
        main_window = webview.create_window(
            "AirControl",
            url,
            width=1180,
            height=760,
            min_size=(900, 600),
        )
        focus_thread = threading.Thread(
            target=poll_focus_requests,
            args=(main_window,),
            name="aircontrol-focus-poller",
            daemon=True,
        )
        focus_thread.start()
        if airy_enabled:
            try:
                airy_window = create_airy_window(
                    webview,
                    config,
                    ws_url=f"ws://{config.ipc.host}:{config.ipc.port}",
                )
            except FileNotFoundError as exc:
                print(f"Airy companion was not opened: {exc}", file=sys.stderr)
            else:
                _close_with_main(main_window, airy_window)
        _start_webview(webview)
    finally:
        focus_poll_stopped.set()
        stop_requested.set()
        try:
            server.shutdown()
        finally:
            if focus_thread is not None:
                focus_thread.join()
            daemon_thread.join()
            server_thread.join()
            server.server_close()

    if daemon_errors:
        error = daemon_errors[0]
        print(
            f"AirControl daemon stopped unexpectedly: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    return daemon_results[0] if daemon_results else 1
