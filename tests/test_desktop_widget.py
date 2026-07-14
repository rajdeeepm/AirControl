from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from aircontrol import cli, desktop, widget
from aircontrol.config import AppConfig


BUILD_COMMAND = "npm --prefix ui install && npm --prefix ui run build"


def _raise_import_error() -> None:
    raise ImportError("optional GUI dependency is not installed")


def test_desktop_and_widget_import_without_optional_gui_dependencies() -> None:
    assert "webview" not in vars(desktop)
    assert "tkinter" not in vars(widget)


def test_desktop_missing_dist_returns_2_before_starting_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_dist = tmp_path / "missing-dist"
    monkeypatch.setattr(desktop, "DIST_DIR", missing_dist)
    monkeypatch.setattr(
        desktop,
        "_import_webview",
        lambda: pytest.fail("pywebview must not be imported before the build check"),
    )

    result = desktop.run_app(AppConfig.defaults(), tmp_path, practice=True)

    assert result == 2
    output = capsys.readouterr()
    assert BUILD_COMMAND in output.out + output.err


def test_desktop_missing_pywebview_returns_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    monkeypatch.setattr(desktop, "DIST_DIR", dist_dir)
    monkeypatch.setattr(desktop, "_import_webview", _raise_import_error)
    monkeypatch.setattr(
        desktop,
        "create_server",
        lambda *_args, **_kwargs: pytest.fail(
            "the UI server must not start without pywebview"
        ),
    )

    result = desktop.run_app(AppConfig.defaults(), tmp_path, practice=True)

    assert result == 2
    captured = capsys.readouterr()
    output = (captured.out + captured.err).lower()
    assert "pywebview" in output
    assert "setup.cmd" in output


class _FakeServer:
    server_port = 43123

    def __init__(self) -> None:
        self.serve_thread: threading.Thread | None = None
        self.shutdown_called = False
        self.closed = False
        self._stop = threading.Event()

    def serve_forever(self) -> None:
        self.serve_thread = threading.current_thread()
        self._stop.wait(timeout=2.0)

    def shutdown(self) -> None:
        self.shutdown_called = True
        self._stop.set()

    def server_close(self) -> None:
        self.closed = True


def test_desktop_runs_server_and_daemon_off_main_thread_and_stops_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    server = _FakeServer()
    create_calls: list[tuple[Path, int]] = []
    run_calls: list[dict[str, object]] = []
    run_thread: list[threading.Thread] = []
    window_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    main_thread = threading.current_thread()

    def create_server(directory: Path, port: int) -> _FakeServer:
        create_calls.append((directory, port))
        return server

    def run(**kwargs: object) -> int:
        run_calls.append(kwargs)
        run_thread.append(threading.current_thread())
        should_stop = kwargs["should_stop"]
        assert callable(should_stop)
        deadline = time.monotonic() + 2.0
        while not should_stop() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert should_stop()
        return 0

    class Webview:
        @staticmethod
        def create_window(*args: object, **kwargs: object) -> object:
            window_calls.append((args, kwargs))
            return object()

        @staticmethod
        def start() -> None:
            assert threading.current_thread() is main_thread

    import aircontrol.app as app_module

    monkeypatch.setattr(desktop, "DIST_DIR", dist_dir)
    monkeypatch.setattr(desktop, "create_server", create_server)
    monkeypatch.setattr(desktop, "_import_webview", lambda: Webview)
    monkeypatch.setattr(app_module, "run", run)

    config = AppConfig.defaults()
    config.ipc.enabled = True
    result = desktop.run_app(config, tmp_path, practice=True, model_override="model")

    assert result == 0
    assert create_calls == [(dist_dir, 0)]
    assert run_thread and run_thread[0] is not main_thread
    assert run_calls[0]["config"] is config
    assert run_calls[0]["config_directory"] == tmp_path
    assert run_calls[0]["practice"] is True
    assert run_calls[0]["model_override"] == "model"
    assert window_calls == [
        (
            ("AirControl", "http://127.0.0.1:43123/"),
            {"width": 1180, "height": 760, "min_size": (900, 600)},
        )
    ]
    assert server.serve_thread is not None
    assert server.serve_thread.daemon is True
    assert server.shutdown_called is True
    assert server.closed is True


def test_desktop_reports_daemon_thread_exception_after_window_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    server = _FakeServer()
    daemon_failed = threading.Event()
    window_closed = threading.Event()

    def run(**_kwargs: object) -> int:
        daemon_failed.set()
        raise RuntimeError("camera exploded")

    class Webview:
        @staticmethod
        def create_window(*_args: object, **_kwargs: object) -> object:
            return object()

        @staticmethod
        def start() -> None:
            assert daemon_failed.wait(timeout=2.0)
            window_closed.set()

    import aircontrol.app as app_module

    monkeypatch.setattr(desktop, "DIST_DIR", dist_dir)
    monkeypatch.setattr(desktop, "create_server", lambda *_a, **_k: server)
    monkeypatch.setattr(desktop, "_import_webview", lambda: Webview)
    monkeypatch.setattr(app_module, "run", run)

    result = desktop.run_app(AppConfig.defaults(), tmp_path, practice=True)

    assert result != 0
    assert window_closed.is_set()
    output = capsys.readouterr()
    assert "camera exploded" in output.out + output.err


def test_widget_missing_tkinter_returns_clear_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(widget, "_import_tkinter", _raise_import_error)

    result = widget.run_widget(AppConfig.defaults())

    assert result == 2
    captured = capsys.readouterr()
    output = (captured.out + captured.err).lower()
    assert "tkinter" in output


@pytest.mark.parametrize(
    ("connected", "armed", "expected_label"),
    [
        (True, True, "ARMED"),
        (True, False, "IDLE"),
        (False, True, "OFFLINE"),
        (False, False, "OFFLINE"),
    ],
)
def test_widget_state_mapping_always_includes_an_honest_text_label(
    connected: bool,
    armed: bool,
    expected_label: str,
) -> None:
    label, color = widget.widget_state(connected, armed)

    assert label == expected_label
    assert label in {"ARMED", "IDLE", "OFFLINE"}
    assert isinstance(color, str) and color
    if label == "ARMED":
        assert color == "#7dd3a0"


def test_widget_position_round_trip_and_fallbacks(tmp_path: Path) -> None:
    path = tmp_path / "AirControl" / "widget.json"
    default = (700, 500)

    assert widget.load_widget_position(path, default) == default

    widget.save_widget_position(path, 123, 456)

    assert json.loads(path.read_text(encoding="utf-8")) == {"x": 123, "y": 456}
    assert widget.load_widget_position(path, default) == (123, 456)

    path.write_text("{not valid JSON", encoding="utf-8")
    assert widget.load_widget_position(path, default) == default


def test_cli_help_lists_app_and_widget_with_help_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])

    assert excinfo.value.code == 0
    output = capsys.readouterr().out
    assert "--app" in output
    assert "Open AirControl as a desktop application window" in output
    assert "--widget" in output
    assert "Show the small always-on-top armed/idle status widget" in output


@pytest.mark.parametrize(
    "argv",
    [
        ["--app", "--calibrate"],
        ["--widget", "--practice"],
    ],
)
def test_cli_rejects_incompatible_desktop_modes(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)

    assert excinfo.value.code == 2


def test_cli_app_enables_ipc_and_routes_to_desktop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"ipc": {"enabled": False}}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def run_app(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(desktop, "run_app", run_app)

    result = cli.main(
        ["--config", str(config_file), "--app", "--practice"]
    )

    assert result == 0
    assert captured["config"].ipc.enabled is True
    assert captured["config_directory"] == tmp_path
    assert captured["practice"] is True
    assert captured["model_override"] is None
