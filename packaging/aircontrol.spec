# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


PROJECT_ROOT = Path(SPECPATH).resolve().parent

# Application-owned resources keep their repository-relative layout so
# aircontrol.resources.resource_dir() can resolve them in a frozen build.
datas = [
    (str(PROJECT_ROOT / "ui/dist"), "ui/dist"),
    (str(PROJECT_ROOT / "ui/airy"), "ui/airy"),
    (str(PROJECT_ROOT / "packaging/airy.ico"), "packaging"),
    (str(PROJECT_ROOT / "models/hand_landmarker.task"), "models"),
    (str(PROJECT_ROOT / "config.json"), "."),
]
datas += collect_data_files("mediapipe")
datas += collect_data_files("webview")

binaries = collect_dynamic_libs("mediapipe")
binaries += collect_dynamic_libs("webview")

# MediaPipe Tasks, pywebview's Windows backend, and websockets all load parts
# of their packages dynamically, outside PyInstaller's static import graph.
hiddenimports = sorted(
    set(
        ["cv2", "mediapipe", "websockets", "webview"]
        + collect_submodules("mediapipe")
        + collect_submodules("websockets")
        + collect_submodules("webview")
    )
)

a = Analysis(
    [str(PROJECT_ROOT / "aircontrol_app.py")],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AirControl",
    # Absolute, like every other path here: PyInstaller resolves a relative
    # icon path against the spec's own directory, so "packaging/airy.ico"
    # became packaging/packaging/airy.ico and failed the build.
    icon=str(PROJECT_ROOT / "packaging/airy.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AirControl",
)
