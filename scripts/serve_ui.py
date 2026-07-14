from __future__ import annotations

import argparse
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOST = "127.0.0.1"
DIST_DIR = Path(__file__).resolve().parents[1] / "ui" / "dist"
BUILD_COMMAND = "npm --prefix ui install && npm --prefix ui run build"


def create_server(directory: str | Path, port: int = 0) -> ThreadingHTTPServer:
    served_directory = Path(directory).resolve()
    handler = partial(
        SimpleHTTPRequestHandler,
        directory=str(served_directory),
    )
    return ThreadingHTTPServer((HOST, port), handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve the built AirControl app UI on this computer.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the UI in the default web browser",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port to use (default: choose an available port)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    dist_dir: str | Path | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    directory = Path(dist_dir) if dist_dir is not None else DIST_DIR
    if not directory.is_dir():
        parser.error(
            f"UI build not found at {directory}. Run `{BUILD_COMMAND}` first."
        )

    with create_server(directory, port=args.port) as server:
        url = f"http://{HOST}:{server.server_port}/"
        print(f"AirControl UI: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nAirControl UI server stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
