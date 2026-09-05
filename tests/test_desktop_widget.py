from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aircontrol import cli, desktop, widget
from aircontrol.config import AppConfig
from aircontrol.store import Store
from aircontrol import privacy
from aircontrol.resources import launcher


BUILD_COMMAND = "npm --prefix ui install && npm --prefix ui run build"



@pytest.fixture(autouse=True)
def _consent_already_granted(tmp_path: Path) -> None:
    """Record consent in the config directory these tests use.

    run_app settles the privacy notice on the main thread before it opens a
    window. These tests are about threads and windows, so grant consent up
    front -- through the real gate rather than by stubbing it out, so the
    ordering stays covered.
    """
    (tmp_path / privacy.CONSENT_FILE).write_text(
        json.dumps({"accepted": True, "notice_version": privacy.CONSENT_VERSION})
        + "\n",
        encoding="utf-8",
    )

def _raise_import_error() -> None:
    raise ImportError("optional GUI dependency is not installed")


def test_desktop_and_widget_import_without_optional_gui_dependencies() -> None:
    assert "webview" not in vars(desktop)
    assert "webview" not in vars(widget)
    assert "ctypes" not in vars(desktop)
    assert "ctypes" not in vars(widget)
    assert "tkinter" not in vars(widget)


@pytest.mark.parametrize("module", (desktop, widget))
def test_windows_app_user_model_id_is_best_effort(
    module: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Shell32:
        @staticmethod
        def SetCurrentProcessExplicitAppUserModelID(value: str) -> None:
            calls.append(value)
            raise OSError("simulated unsupported Windows shell")

    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(shell32=Shell32()),
    )
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    getattr(module, "_set_windows_app_user_model_id")()

    assert calls == ["AirControl.App"]


@pytest.mark.parametrize("module", (desktop, widget))
def test_app_user_model_id_is_a_noop_outside_windows(
    module: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(
            shell32=SimpleNamespace(
                SetCurrentProcessExplicitAppUserModelID=calls.append,
            )
        )
    )
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix"))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    getattr(module, "_set_windows_app_user_model_id")()

    assert calls == []


@pytest.mark.parametrize("module", (desktop, widget))
def test_webview_start_falls_back_when_icon_keyword_is_unsupported(
    module: object,
) -> None:
    start_calls = 0

    class Webview:
        @staticmethod
        def start() -> None:
            nonlocal start_calls
            start_calls += 1

    getattr(module, "_start_webview")(Webview)

    assert start_calls == 1


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
    assert launcher("setup") in output


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


@pytest.mark.parametrize(
    ("airy_enabled", "expected_window_count"),
    [(True, 2), (False, 1)],
)
def test_desktop_runs_server_and_daemon_off_main_thread_and_stops_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    airy_enabled: bool,
    expected_window_count: int,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    server = _FakeServer()
    create_calls: list[tuple[Path, int]] = []
    run_calls: list[dict[str, object]] = []
    run_thread: list[threading.Thread] = []
    window_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    start_calls: list[dict[str, object]] = []
    app_identity_calls = 0
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
        settings = {"DRAG_REGION_DIRECT_TARGET_ONLY": False}

        @staticmethod
        def create_window(*args: object, **kwargs: object) -> object:
            window_calls.append((args, kwargs))
            return object()

        @staticmethod
        def start(**kwargs: object) -> None:
            start_calls.append(kwargs)
            assert threading.current_thread() is main_thread

    def set_app_identity() -> None:
        nonlocal app_identity_calls
        app_identity_calls += 1

    import aircontrol.app as app_module

    monkeypatch.setattr(desktop, "DIST_DIR", dist_dir)
    monkeypatch.setattr(desktop, "create_server", create_server)
    monkeypatch.setattr(desktop, "_import_webview", lambda: Webview)
    monkeypatch.setattr(desktop, "_load_airy_enabled", lambda _config: airy_enabled)
    monkeypatch.setattr(desktop, "_set_windows_app_user_model_id", set_app_identity)
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
    assert window_calls[0] == (
        ("AirControl", "http://127.0.0.1:43123/"),
        {"width": 1180, "height": 760, "min_size": (900, 600)},
    )
    assert len(window_calls) == expected_window_count
    assert app_identity_calls == 1
    assert start_calls == [{"icon": str(desktop.AIRY_ICON)}]
    if airy_enabled:
        airy_args, airy_kwargs = window_calls[1]
        assert airy_args[0] == "Airy"
        assert isinstance(airy_args[1], str)
        assert airy_args[1] == widget.AIRY_HTML.resolve().as_uri()
        assert "?" not in airy_args[1]
        assert airy_kwargs["width"] == 190
        assert airy_kwargs["height"] == 220
        assert airy_kwargs["frameless"] is True
        assert airy_kwargs["on_top"] is True
        assert airy_kwargs["resizable"] is False
        assert airy_kwargs["easy_drag"] is False
        assert airy_kwargs["transparent"] is True
        airy_api = airy_kwargs["js_api"]
        assert isinstance(airy_api, widget.AiryApi)
        assert airy_api.get_bootstrap() == {
            "ws_url": f"ws://{config.ipc.host}:{config.ipc.port}",
            "native_drag": True,
        }
    assert server.serve_thread is not None
    assert server.serve_thread.daemon is True
    assert server.shutdown_called is True
    assert server.closed is True


def test_desktop_reads_persisted_airy_preference(tmp_path: Path) -> None:
    config = AppConfig.defaults()
    config.store.db_path = str(tmp_path / "data" / "aircontrol.db")
    Path(config.store.db_path).parent.mkdir(parents=True)
    with Store(config.store.db_path) as store:
        store.app_settings.set("airy_enabled", False)

    assert desktop._load_airy_enabled(config) is False


def test_desktop_closes_airy_when_the_main_window_closes() -> None:
    handlers: list[object] = []

    class EventHook:
        def __iadd__(self, handler: object) -> EventHook:
            handlers.append(handler)
            return self

    class Events:
        closed = EventHook()

    class MainWindow:
        events = Events()

    class AiryWindow:
        def __init__(self) -> None:
            self.destroyed = False

        def destroy(self) -> None:
            self.destroyed = True

    airy_window = AiryWindow()
    desktop._close_with_main(MainWindow(), airy_window)

    assert len(handlers) == 1
    handler = handlers[0]
    assert callable(handler)
    handler()
    assert airy_window.destroyed is True


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
    monkeypatch.setattr(desktop, "_load_airy_enabled", lambda _config: False)
    monkeypatch.setattr(app_module, "run", run)

    result = desktop.run_app(AppConfig.defaults(), tmp_path, practice=True)

    assert result != 0
    assert window_closed.is_set()
    output = capsys.readouterr()
    assert "camera exploded" in output.out + output.err


def test_widget_missing_pywebview_returns_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(widget, "_import_webview", _raise_import_error)

    result = widget.run_widget(AppConfig.defaults())

    assert result == 2
    captured = capsys.readouterr()
    output = (captured.out + captured.err).lower()
    assert "pywebview" in output
    assert launcher("setup") in output


def test_widget_sets_app_identity_and_starts_with_airy_icon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_calls: list[dict[str, object]] = []
    identity_calls = 0

    class Webview:
        settings = {"DRAG_REGION_DIRECT_TARGET_ONLY": False}

        @staticmethod
        def create_window(*_args: object, **_kwargs: object) -> object:
            return object()

        @staticmethod
        def start(**kwargs: object) -> None:
            start_calls.append(kwargs)

    def set_app_identity() -> None:
        nonlocal identity_calls
        identity_calls += 1

    monkeypatch.setattr(widget, "_import_webview", lambda: Webview)
    monkeypatch.setattr(widget, "_set_windows_app_user_model_id", set_app_identity)
    monkeypatch.setattr(
        widget,
        "default_store_path",
        lambda: tmp_path / "AirControl" / "aircontrol.db",
    )

    result = widget.run_widget(AppConfig.defaults())

    assert result == 0
    assert identity_calls == 1
    assert start_calls == [{"icon": str(widget.AIRY_ICON)}]


def test_widget_missing_airy_asset_returns_clear_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(widget, "AIRY_HTML", tmp_path / "missing" / "index.html")
    monkeypatch.setattr(
        widget,
        "_import_webview",
        lambda: pytest.fail("pywebview must not be imported before the asset check"),
    )

    result = widget.run_widget(AppConfig.defaults())

    assert result == 2
    captured = capsys.readouterr()
    output = (captured.out + captured.err).lower()
    assert "airy" in output
    assert "index.html" in output


def test_create_airy_window_falls_back_when_transparency_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class Webview:
        settings = {"DRAG_REGION_DIRECT_TARGET_ONLY": False}

        @staticmethod
        def create_window(
            _title: str,
            _url: str,
            **kwargs: object,
        ) -> object:
            calls.append(kwargs)
            if "transparent" in kwargs:
                raise TypeError("transparent is unsupported")
            return object()

    monkeypatch.setattr(
        widget,
        "default_store_path",
        lambda: tmp_path / "AirControl" / "aircontrol.db",
    )

    widget.create_airy_window(Webview, AppConfig.defaults())

    assert calls[0]["transparent"] is True
    assert "transparent" not in calls[1]


@pytest.mark.parametrize(
    ("connected", "armed", "expected_label"),
    [
        (True, True, "ACTIVE"),
        (True, False, "INACTIVE"),
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
    assert label in {"ACTIVE", "INACTIVE", "OFFLINE"}
    assert isinstance(color, str) and color


def test_widget_position_round_trip_and_fallbacks(tmp_path: Path) -> None:
    path = tmp_path / "AirControl" / "widget.json"
    default = (700, 500)

    assert widget.load_widget_position(path, default) == default

    widget.save_widget_position(path, 123, 456)

    assert json.loads(path.read_text(encoding="utf-8")) == {"x": 123, "y": 456}
    assert widget.load_widget_position(path, default) == (123, 456)

    path.write_text("{not valid JSON", encoding="utf-8")
    assert widget.load_widget_position(path, default) == default


def test_airy_api_moves_closes_and_persists_the_latest_position(
    tmp_path: Path,
) -> None:
    path = tmp_path / "AirControl" / "widget.json"

    class Window:
        x = 100
        y = 200

        def __init__(self) -> None:
            self.moves: list[tuple[int, int]] = []
            self.destroyed = False

        def move(self, x: int, y: int) -> None:
            self.x = x
            self.y = y
            self.moves.append((x, y))

        def destroy(self) -> None:
            self.destroyed = True

    window = Window()
    api = widget.AiryApi(
        path,
        initial_position=(100, 200),
        ws_url="ws://localhost:9876",
        native_drag=False,
    )
    api.bind_window(window)

    assert api.get_bootstrap() == {
        "ws_url": "ws://localhost:9876",
        "native_drag": False,
    }

    api.on_moved(112, 224)
    assert widget.load_widget_position(path, (0, 0)) == (112, 224)

    api.move_window(130, 245)
    api.close_widget()

    assert window.moves == [(130, 245)]
    assert window.destroyed is True
    assert widget.load_widget_position(path, (0, 0)) == (130, 245)


def test_airy_asset_is_self_contained_and_names_every_state() -> None:
    source = widget.AIRY_HTML.read_text(encoding="utf-8")
    lowered = source.lower()

    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "Active" in source
    assert "Inactive" in source
    assert "Offline" in source
    assert "ws://127.0.0.1:8787" in source
    assert "pywebview-drag-region" in source
    assert "no-drag" in source
    assert "move_window" in source
    assert "prefers-reduced-motion" in source


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
    assert "Show Airy, the always-on-top companion widget" in output


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


def test_desktop_refuses_to_open_a_window_without_consent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The notice is settled before the window, so a decline is visible.

    Previously the daemon thread asked, behind an already-open window, and the
    UI reported only "daemon not connected" while the engine waited on stdin.
    """
    (tmp_path / privacy.CONSENT_FILE).unlink()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    monkeypatch.setattr(desktop, "DIST_DIR", dist_dir)
    monkeypatch.setattr(
        desktop,
        "_import_webview",
        lambda: pytest.fail("no window may open before consent is settled"),
    )
    monkeypatch.setattr(
        desktop,
        "create_server",
        lambda *_a, **_k: pytest.fail("the UI server must not start without consent"),
    )
    monkeypatch.setattr(
        privacy, "ensure_metrics_consent", _decline_consent, raising=True
    )
    monkeypatch.setattr(
        desktop, "ensure_metrics_consent", _decline_consent, raising=True
    )

    result = desktop.run_app(AppConfig.defaults(), tmp_path, practice=True)

    assert result == 2
    assert "privacy notice" in capsys.readouterr().err


def _decline_consent(*_args: object, **_kwargs: object) -> None:
    raise privacy.MetricsConsentDeclined(
        "AirControl needs you to accept its privacy notice once."
    )
