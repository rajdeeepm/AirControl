"""Native pywebview shell for the built AirControl app UI."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from aircontrol.config import AppConfig
from scripts.serve_ui import BUILD_COMMAND, DIST_DIR, HOST, create_server


def _import_webview() -> Any:
    import webview

    return webview


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
        webview.create_window(
            "AirControl",
            url,
            width=1180,
            height=760,
            min_size=(900, 600),
        )
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
