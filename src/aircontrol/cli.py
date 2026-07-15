from __future__ import annotations

import argparse
import sys

from aircontrol.config import load_config, resolve_config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aircontrol",
        description="Control Windows with camera-visible hand gestures.",
    )
    parser.add_argument("--config", default="config.json", help="Path to the JSON settings file")
    parser.add_argument("--practice", action="store_true", help="Recognize gestures without controlling Windows")
    parser.add_argument(
        "--serve",
        action="store_true",
        help=(
            "Run with the app UI (starts the local UI server and enables the "
            "daemon's WebSocket)"
        ),
    )
    parser.add_argument(
        "--app",
        action="store_true",
        help="Open AirControl as a desktop application window",
    )
    parser.add_argument(
        "--widget",
        action="store_true",
        help="Show Airy, the always-on-top companion widget",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Run the guided calibration flow and save an active profile",
    )
    parser.add_argument(
        "--record-gesture",
        metavar="NAME",
        help="Record a new custom gesture with guided takes and blocking checks",
    )
    parser.add_argument(
        "--arena",
        action="store_true",
        help="Open the practice arena for live match feedback and learning",
    )
    parser.add_argument(
        "--stress",
        action="store_true",
        help="With --arena: run the 60-second false-fire stress test",
    )
    parser.add_argument("--camera", type=int, help="Override the camera index")
    parser.add_argument("--model", help="Use an existing MediaPipe hand model")
    return parser


def _validate_modes(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    modes = [
        name
        for name, active in (
            ("--calibrate", args.calibrate),
            ("--record-gesture", args.record_gesture is not None),
            ("--arena", args.arena),
            ("--practice", args.practice),
        )
        if active
    ]
    if len(modes) > 1:
        parser.error(f"{' and '.join(modes)} cannot be combined")
    if args.serve:
        incompatible = [
            name
            for name, active in (
                ("--calibrate", args.calibrate),
                ("--record-gesture", args.record_gesture is not None),
                ("--arena", args.arena),
            )
            if active
        ]
        if incompatible:
            parser.error(f"--serve and {incompatible[0]} cannot be combined")
    if args.app:
        incompatible = [
            name
            for name, active in (
                ("--calibrate", args.calibrate),
                ("--record-gesture", args.record_gesture is not None),
                ("--arena", args.arena),
            )
            if active
        ]
        if incompatible:
            parser.error(f"--app and {incompatible[0]} cannot be combined")
    if args.widget:
        incompatible = [
            name
            for name, active in (
                ("--app", args.app),
                ("--serve", args.serve),
                ("--calibrate", args.calibrate),
                ("--record-gesture", args.record_gesture is not None),
                ("--arena", args.arena),
                ("--practice", args.practice),
                ("--stress", args.stress),
            )
            if active
        ]
        if incompatible:
            parser.error(f"--widget and {incompatible[0]} cannot be combined")
    if args.stress and not args.arena:
        parser.error("--stress requires --arena")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_modes(parser, args)
    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
        if args.camera is not None:
            config.camera.index = args.camera
        if args.serve or args.app:
            config.ipc.enabled = True

        if args.widget:
            from aircontrol.widget import run_widget

            return run_widget(config)
        if args.app:
            from aircontrol.desktop import run_app

            return run_app(
                config=config,
                config_directory=config_path.parent,
                practice=args.practice,
                model_override=args.model,
            )

        from aircontrol.app import arena, calibrate, record_gesture, run

        if args.calibrate:
            return calibrate(
                config=config,
                config_directory=config_path.parent,
                model_override=args.model,
            )
        if args.record_gesture is not None:
            return record_gesture(
                config=config,
                config_directory=config_path.parent,
                name=args.record_gesture,
                model_override=args.model,
            )
        if args.arena:
            return arena(
                config=config,
                config_directory=config_path.parent,
                stress=args.stress,
                model_override=args.model,
            )

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
