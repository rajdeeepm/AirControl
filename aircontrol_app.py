"""PyInstaller entry point for the AirControl Windows application."""

from __future__ import annotations

import sys

from aircontrol.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["--app"]))
