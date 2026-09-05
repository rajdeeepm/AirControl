"""Runtime paths for bundled read-only assets and per-user writable data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return whether AirControl is running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resource_dir() -> Path:
    """Return the root containing bundled assets or the source checkout."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def _platform_data_base() -> Path:
    """Return the platform's conventional per-user application-data root."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        configured_base = Path(local_app_data).expanduser() if local_app_data else None
        return (
            configured_base
            if configured_base is not None and configured_base.is_absolute()
            else Path.home() / "AppData" / "Local"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"

    data_home = os.environ.get("XDG_DATA_HOME")
    configured_base = Path(data_home).expanduser() if data_home else None
    return (
        configured_base
        if configured_base is not None and configured_base.is_absolute()
        else Path.home() / ".local" / "share"
    )


def launcher(name: str) -> str:
    """Spell a launcher script the way this platform ships it.

    ``launcher("setup")`` is ``setup.cmd`` on Windows and ``./setup.sh`` on
    macOS, so diagnostics never point at a file the user does not have.
    """
    return f"{name}.cmd" if os.name == "nt" else f"./{name}.sh"


def user_data_dir() -> Path:
    """Return and create AirControl's writable per-user data directory."""
    directory = _platform_data_base() / "AirControl"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


__all__ = ["is_frozen", "launcher", "resource_dir", "user_data_dir"]
