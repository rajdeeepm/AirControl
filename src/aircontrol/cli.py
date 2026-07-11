from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aircontrol.config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aircontrol",
        description="Control Windows with camera-visible hand gestures.",
    )
    parser.add_argument("--config", default="config.json", help="Path to the JSON settings file")
    parser.add_argument("--practice", action="store_true", help="Recognize gestures without controlling Windows")
    parser.add_argument("--camera", type=int, help="Override the camera index")
    parser.add_argument("--model", help="Use an existing MediaPipe hand model")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config).resolve()
    try:
        config = load_config(config_path)
        if args.camera is not None:
            config.camera.index = args.camera
        from aircontrol.app import run

        return run(
            config=config,
            config_directory=config_path.parent,
            practice=args.practice,
            model_override=args.model,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"AirControl could not start: {exc}", file=sys.stderr)
        return 1

