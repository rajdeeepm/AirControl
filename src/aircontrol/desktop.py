"""Native pywebview shell for the built AirControl app UI."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from aircontrol import settings as app_settings
from aircontrol.config import AppConfig
from aircontrol.daemon import default_store_path
from aircontrol.store import Store
from aircontrol.widget import create_airy_window
from scripts.serve_ui import BUILD_COMMAND, DIST_DIR, HOST, create_server


def _import_webview() -> Any:
    import webview

    return webview


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


def run_app(
    config: AppConfig,
    config_directory: Path,
    practice: bool,
    model_override: str | None = None,
) -> int:
    """Run the daemon behind a native window that hosts the built UI."""
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
    daemon_results: list[int] = []
    daemon_errors: list[BaseException] = []

    def run_daemon() -> None:
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

    server_thread = threading.Thread(
        target=server.serve_forever,
        name="aircontrol-ui-server",
        daemon=True,
    )
    daemon_thread = threading.Thread(
        target=run_daemon,
        name="aircontrol-daemon",
    )
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
        webview.start()
    finally:
        stop_requested.set()
        try:
            server.shutdown()
        finally:
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
