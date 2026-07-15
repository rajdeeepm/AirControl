from __future__ import annotations

import importlib
import sys
from pathlib import Path

import aircontrol.privacy as privacy
import aircontrol.resources as resources
from aircontrol.config import AppConfig, load_config, resolve_config_path
from aircontrol.daemon import default_store_path
from aircontrol.model import resolve_hand_model_path
from aircontrol.privacy import _consent_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _not_frozen(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def _frozen(monkeypatch, bundle: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)


def test_resource_dir_uses_repo_root_when_not_frozen(monkeypatch) -> None:
    _not_frozen(monkeypatch)

    assert resources.is_frozen() is False
    assert resources.resource_dir() == PROJECT_ROOT


def test_resource_dir_uses_pyinstaller_bundle_when_frozen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _frozen(monkeypatch, bundle)

    assert resources.is_frozen() is True
    assert resources.resource_dir() == bundle


def test_user_data_dir_is_writable_and_independent_of_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_base = tmp_path / "per-user-data"
    monkeypatch.setattr(resources, "_platform_data_base", lambda: data_base)

    _not_frozen(monkeypatch)
    source_path = resources.user_data_dir()
    _frozen(monkeypatch, tmp_path / "bundle")
    frozen_path = resources.user_data_dir()

    assert source_path == data_base / "AirControl"
    assert frozen_path == source_path
    assert frozen_path.is_dir()


def test_default_store_path_stays_in_the_platform_user_data_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_base = tmp_path / "per-user-data"
    monkeypatch.setenv("LOCALAPPDATA", str(data_base))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_base))
    _frozen(monkeypatch, tmp_path / "bundle")

    assert default_store_path().parent == resources.user_data_dir()


def test_config_path_stays_at_the_requested_location_when_not_frozen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _not_frozen(monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text('{"camera":{"index":2}}', encoding="utf-8")

    resolved = resolve_config_path(config_path)

    assert resolved == config_path.resolve()
    assert resolved.read_text(encoding="utf-8") == '{"camera":{"index":2}}'


def test_default_config_is_seeded_once_in_user_data_when_frozen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    bundled_config = bundle / "config.json"
    bundled_config.write_text('{"camera":{"index":3}}', encoding="utf-8")
    data_base = tmp_path / "per-user-data"
    monkeypatch.setattr(resources, "_platform_data_base", lambda: data_base)
    _frozen(monkeypatch, bundle)

    resolved = resolve_config_path("config.json")

    assert resolved == data_base / "AirControl" / "config.json"
    assert resolved.read_text(encoding="utf-8") == bundled_config.read_text(encoding="utf-8")
    assert load_config(resolved).camera.index == 3

    resolved.write_text('{"camera":{"index":7}}', encoding="utf-8")
    assert resolve_config_path("config.json").read_text(encoding="utf-8") == (
        '{"camera":{"index":7}}'
    )


def test_consent_path_moves_only_when_frozen(tmp_path: Path, monkeypatch) -> None:
    config_directory = tmp_path / "source-config"
    data_base = tmp_path / "per-user-data"
    monkeypatch.setattr(resources, "_platform_data_base", lambda: data_base)

    _not_frozen(monkeypatch)
    assert _consent_path(config_directory) == (
        config_directory / ".aircontrol-consent.json"
    )

    _frozen(monkeypatch, tmp_path / "bundle")
    assert _consent_path(config_directory) == (
        data_base / "AirControl" / ".aircontrol-consent.json"
    )


def test_model_default_resolves_against_the_correct_read_only_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relative_model = AppConfig.defaults().tracking.model_path
    config_directory = tmp_path / "source-config"

    _not_frozen(monkeypatch)
    assert resolve_hand_model_path(relative_model, config_directory) == (
        config_directory / "models" / "hand_landmarker.task"
    )

    bundle = tmp_path / "bundle"
    _frozen(monkeypatch, bundle)
    assert resolve_hand_model_path(relative_model, config_directory) == (
        bundle / "models" / "hand_landmarker.task"
    )
    assert resolve_hand_model_path("custom.task", config_directory) == (
        config_directory / "custom.task"
    )


def test_frozen_default_consent_uses_a_native_dialog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_base = tmp_path / "per-user-data"
    monkeypatch.setattr(resources, "_platform_data_base", lambda: data_base)
    _frozen(monkeypatch, tmp_path / "bundle")
    dialog_calls: list[bool] = []
    monkeypatch.setattr(
        privacy,
        "_request_frozen_consent",
        lambda: dialog_calls.append(True) or True,
    )

    privacy.ensure_metrics_consent(tmp_path / "ignored-config-directory")

    assert dialog_calls == [True]
    assert privacy.has_metrics_consent(tmp_path / "ignored-config-directory")


def test_ui_and_airy_assets_follow_resource_dir_when_frozen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import aircontrol.desktop as desktop
    import aircontrol.widget as widget
    import scripts.serve_ui as serve_ui

    bundle = tmp_path / "bundle"
    with monkeypatch.context() as frozen:
        _frozen(frozen, bundle)
        importlib.reload(serve_ui)
        importlib.reload(widget)
        importlib.reload(desktop)

        assert serve_ui.DIST_DIR == bundle / "ui" / "dist"
        assert desktop.DIST_DIR == bundle / "ui" / "dist"
        assert desktop.AIRY_ICON == bundle / "packaging" / "airy.ico"
        assert widget.AIRY_HTML == bundle / "ui" / "airy" / "index.html"
        assert widget.AIRY_ICON == bundle / "packaging" / "airy.ico"

    importlib.reload(serve_ui)
    importlib.reload(widget)
    importlib.reload(desktop)

    assert serve_ui.DIST_DIR == PROJECT_ROOT / "ui" / "dist"
    assert desktop.DIST_DIR == PROJECT_ROOT / "ui" / "dist"
    assert desktop.AIRY_ICON == PROJECT_ROOT / "packaging" / "airy.ico"
    assert widget.AIRY_HTML == PROJECT_ROOT / "ui" / "airy" / "index.html"
    assert widget.AIRY_ICON == PROJECT_ROOT / "packaging" / "airy.ico"


def test_packaging_and_release_contract_files_reference_bundled_assets() -> None:
    spec_path = PROJECT_ROOT / "packaging" / "aircontrol.spec"
    installer_path = PROJECT_ROOT / "packaging" / "installer.iss"
    release_path = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
    ci_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    entry_path = PROJECT_ROOT / "aircontrol_app.py"
    ui_index_path = PROJECT_ROOT / "ui" / "index.html"
    public_icon_path = PROJECT_ROOT / "ui" / "public" / "airy-icon.png"
    source_icon_path = PROJECT_ROOT / "packaging" / "airy-256.png"

    for path in (spec_path, installer_path, release_path, ci_path, entry_path):
        assert path.is_file(), f"required shipping file is missing: {path}"

    spec = spec_path.read_text(encoding="utf-8")
    assert '(str(PROJECT_ROOT / "packaging/airy.ico"), "packaging")' in spec
    assert 'icon="packaging/airy.ico"' in spec
    for required in (
        "ui/dist",
        "ui/airy",
        "models/hand_landmarker.task",
        "config.json",
        'collect_data_files("mediapipe")',
        'collect_dynamic_libs("mediapipe")',
        "webview",
        "websockets",
        "console=False",
        "COLLECT(",
    ):
        assert required in spec

    installer = installer_path.read_text(encoding="utf-8")
    assert r"SetupIconFile=..\packaging\airy.ico" in installer
    assert installer.count(r'IconFilename: "{app}\{#AppExeName}"') == 3
    for required in (
        '#define AppName "AirControl"',
        "AppName={#AppName}",
        "PrivilegesRequired=lowest",
        r"DefaultDirName={localappdata}\Programs\{#AppName}",
        "AirControl-Setup",
        "desktopicon",
        "runatlogin",
    ):
        assert required in installer

    ui_index = ui_index_path.read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/png" href="/airy-icon.png" />' in ui_index
    assert public_icon_path.read_bytes() == source_icon_path.read_bytes()

    release = release_path.read_text(encoding="utf-8")
    for required in (
        "windows-latest",
        'python-version: "3.11"',
        "npm --prefix ui run build",
        "ensure_hand_model",
        "packaging/aircontrol.spec",
        "installer.iss",
        "actions/upload-artifact",
        "softprops/action-gh-release",
    ):
        assert required in release

    ci = ci_path.read_text(encoding="utf-8")
    assert "env/bin/python" not in ci
    assert "pytest -p no:cacheprovider" in ci
    assert "vitest" in ci.lower()
    assert "npm --prefix ui run build" in ci
