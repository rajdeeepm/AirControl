from __future__ import annotations

import importlib
import threading
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


def _serve_ui() -> ModuleType:
    return importlib.import_module("scripts.serve_ui")


def _create_server_or_skip(serve_ui: ModuleType, directory: Path):
    try:
        return serve_ui.create_server(directory, port=0)
    except PermissionError:
        pytest.skip("test environment forbids loopback sockets")


def test_module_is_importable() -> None:
    assert _serve_ui().__name__ == "scripts.serve_ui"


def test_missing_dist_directory_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve_ui = _serve_ui()
    missing = tmp_path / "missing-dist"
    monkeypatch.setattr(serve_ui, "DIST_DIR", missing)

    with pytest.raises(SystemExit) as excinfo:
        serve_ui.main(["--no-browser"])

    assert excinfo.value.code == 2
    assert "npm --prefix ui install && npm --prefix ui run build" in capsys.readouterr().err


def test_server_returns_index_from_given_directory(tmp_path: Path) -> None:
    serve_ui = _serve_ui()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    expected = "<h1>AirControl test UI</h1>"
    (dist_dir / "index.html").write_text(expected, encoding="utf-8")

    server = _create_server_or_skip(serve_ui, dist_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            assert response.status == 200
            assert response.read().decode("utf-8") == expected
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_path_traversal_does_not_escape_served_directory(tmp_path: Path) -> None:
    serve_ui = _serve_ui()
    dist_dir = tmp_path / "ui" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("inside", encoding="utf-8")
    outside_contents = "outside setup sentinel"
    (tmp_path / "setup.cmd").write_text(outside_contents, encoding="utf-8")

    server = _create_server_or_skip(serve_ui, dist_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        try:
            with urlopen(
                f"http://127.0.0.1:{port}/../../setup.cmd",
                timeout=2,
            ) as response:
                body = response.read().decode("utf-8")
                assert body != outside_contents
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
