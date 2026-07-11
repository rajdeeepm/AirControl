from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from aircontrol.calibration import CalibrationRunner, StepInfo
from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon, default_store_path
from aircontrol.ipc import IpcServer
from aircontrol.model import ensure_hand_model
from aircontrol.overlay import GestureOverlay
from aircontrol.privacy import ensure_metrics_consent
from aircontrol.profile import CalibrationProfile, save_profile
from aircontrol.store import Store
from aircontrol.vision import AsyncVisionWorker, CameraError


def _window_is_open(name: str) -> bool:
    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) >= 1
    except cv2.error:
        return False


def _resize_preview(frame, preview_width: int):
    if preview_width <= 0 or frame.shape[1] == preview_width:
        return frame
    scale = preview_width / frame.shape[1]
    height = max(1, round(frame.shape[0] * scale))
    return cv2.resize(frame, (preview_width, height), interpolation=cv2.INTER_AREA)


def _note_action_events(overlay: GestureOverlay, events: list[dict]) -> None:
    for event in events:
        if event.get("type") == "action" and event.get("description"):
            overlay.note_action(str(event["description"]))


def _draw_calibration_preview(frame, step: StepInfo):
    rendered = frame.copy()
    width = rendered.shape[1]
    cv2.rectangle(rendered, (0, 0), (width, 132), (18, 18, 18), -1)

    words = step.instruction.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > 72:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    cv2.putText(
        rendered,
        step.name.replace("_", " ").upper(),
        (18, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    for index, line in enumerate(lines[:2]):
        cv2.putText(
            rendered,
            line,
            (18, 55 + index * 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (230, 230, 230),
            1,
            cv2.LINE_AA,
        )

    progress = max(0.0, min(1.0, step.progress))
    bar_left = 18
    bar_right = max(bar_left + 1, width - 18)
    bar_top = 111
    cv2.rectangle(
        rendered,
        (bar_left, bar_top),
        (bar_right, 120),
        (80, 80, 80),
        1,
    )
    filled_right = bar_left + round((bar_right - bar_left) * progress)
    cv2.rectangle(
        rendered,
        (bar_left, bar_top),
        (filled_right, 120),
        (80, 200, 120),
        -1,
    )

    indicator = "RECORDING" if step.recording else "SPACE TO CONTINUE"
    indicator_color = (80, 80, 255) if step.recording else (80, 220, 140)
    text_size = cv2.getTextSize(
        indicator,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        1,
    )[0]
    cv2.putText(
        rendered,
        indicator,
        (max(18, width - text_size[0] - 18), 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        indicator_color,
        1,
        cv2.LINE_AA,
    )
    return rendered


def _print_calibration_summary(
    profile: CalibrationProfile,
    store_path: Path,
) -> None:
    lighting_status = (
        "acceptable" if profile.lighting.acceptable else "needs improvement"
    )
    print(f"Calibration complete. Active profile saved to {store_path}")
    print(f"  Hand size: {profile.hand_size:.4f}")
    print(
        "  Motion velocity: "
        f"{profile.motion.velocity_floor:.3f} - {profile.motion.velocity_ceiling:.3f} palms/s"
    )
    print(f"  Incidental feature rows: {len(profile.incidental_features)}")
    print(
        "  Lighting: "
        f"{profile.lighting.mean_brightness:.1f} brightness, "
        f"{profile.lighting.landmark_jitter:.4f} jitter ({lighting_status})"
    )


def _raise_dispatch_failure(
    daemon: Daemon,
    controller: ActionController,
    error: OSError,
) -> None:
    try:
        daemon.force_pause(f"Paused - Windows input failed: {error}")
    except OSError:
        pass
    try:
        controller.release_all()
    except OSError:
        pass
    raise RuntimeError("Windows input injection failed; AirControl stopped safely") from error


def run(
    config: AppConfig,
    config_directory: Path,
    practice: bool = False,
    model_override: str | None = None,
) -> int:
    if not practice and os.name != "nt":
        raise RuntimeError("Live control currently supports Windows; use --practice elsewhere")

    ensure_metrics_consent(config_directory)
    model_setting = model_override or config.tracking.model_path
    model_path = Path(model_setting)
    if not model_path.is_absolute():
        model_path = config_directory / model_path
    model_path = ensure_hand_model(model_path, progress=print)

    controller: ActionController | None = None
    daemon: Daemon | None = None
    worker: AsyncVisionWorker | None = None
    window_created = False
    overlay = GestureOverlay(show_landmarks=config.display.show_landmarks)
    try:
        controller = ActionController(config.input.pointer_pixels_per_palm, practice=practice)
        ipc = IpcServer(config.ipc.host, config.ipc.port) if config.ipc.enabled else None
        daemon = Daemon(
            config,
            practice=practice,
            controller=controller,
            store=None,
            ipc=ipc,
        )
        daemon.start()

        cv2.namedWindow(config.display.window_name, cv2.WINDOW_NORMAL)
        window_created = True
        cv2.moveWindow(config.display.window_name, 30, 30)
        preview_height = round(config.display.preview_width * 480 / 640)
        cv2.resizeWindow(config.display.window_name, config.display.preview_width, preview_height)
        if config.display.always_on_top:
            topmost_property = getattr(cv2, "WND_PROP_TOPMOST", None)
            if topmost_property is not None:
                try:
                    cv2.setWindowProperty(config.display.window_name, topmost_property, 1)
                except cv2.error:
                    pass

        placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
        placeholder[:] = (24, 20, 16)
        starting_status = replace(
            daemon.pipeline.engine.status(),
            status_text="Starting local vision...",
        )
        starting_frame = overlay.draw(
            placeholder, None, None, starting_status, 0.0, practice
        )
        cv2.imshow(
            config.display.window_name,
            _resize_preview(starting_frame, config.display.preview_width),
        )
        cv2.waitKey(1)

        worker = AsyncVisionWorker(config.camera, config.tracking, model_path)
        worker.start()
        mode = "practice" if practice else "live control"
        print(f"AirControl is running in {mode} mode. Open palm arms; fist pauses; Q quits.")

        started_at = time.monotonic()
        previous_result_at: float | None = None
        last_result_at = started_at
        last_sequence = -1
        current_frame = placeholder
        current_observation = None
        current_sample = None
        fps = 0.0
        watchdog_paused = False
        window_sized_for_camera = False
        while True:
            if daemon.quit_requested:
                break
            now = time.monotonic()
            snapshot = worker.snapshot()
            if snapshot.error is not None:
                raise CameraError(f"Vision pipeline stopped: {snapshot.error}") from snapshot.error

            try:
                events: list[dict] = []
                if snapshot.sequence != last_sequence and snapshot.frame is not None:
                    last_sequence = snapshot.sequence
                    current_frame = snapshot.frame
                    current_observation = snapshot.observation
                    events.extend(daemon.feed(current_observation, now))
                    current_sample = daemon.pipeline.last_sample
                    if previous_result_at is not None:
                        frame_interval = max(now - previous_result_at, 1e-6)
                        instantaneous_fps = 1.0 / frame_interval
                        fps = (
                            instantaneous_fps
                            if fps == 0.0
                            else fps * 0.9 + instantaneous_fps * 0.1
                        )
                    previous_result_at = now
                    last_result_at = now
                    watchdog_paused = False
                    if not window_sized_for_camera:
                        preview_height = round(
                            config.display.preview_width
                            * current_frame.shape[0]
                            / current_frame.shape[1]
                        )
                        cv2.resizeWindow(
                            config.display.window_name,
                            config.display.preview_width,
                            preview_height,
                        )
                        window_sized_for_camera = True
                elif last_sequence < 0:
                    events.extend(daemon.feed(None, now))
                    if now - started_at >= config.tracking.startup_timeout_seconds:
                        raise CameraError("Camera and hand tracking did not start in time")
                elif now - last_result_at >= config.tracking.watchdog_seconds:
                    current_observation = None
                    current_sample = None
                    events.extend(daemon.feed(None, now))
                    if not watchdog_paused:
                        events.extend(daemon.force_pause("Paused - vision feed stalled"))
                        watchdog_paused = True
            except OSError as exc:
                _raise_dispatch_failure(daemon, controller, exc)
            _note_action_events(overlay, events)

            status = daemon.pipeline.engine.status()
            if last_sequence < 0:
                status = replace(status, status_text="Starting local vision...")
            rendered = overlay.draw(
                frame=current_frame,
                observation=current_observation,
                sample=current_sample,
                status=status,
                fps=fps,
                practice=practice,
            )
            cv2.imshow(config.display.window_name, _resize_preview(rendered, config.display.preview_width))

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == 27:
                try:
                    events = daemon.command("undo")
                except OSError as exc:
                    _raise_dispatch_failure(daemon, controller, exc)
                _note_action_events(overlay, events)
                if not any(event.get("type") == "action" for event in events):
                    break
            if key == ord(" "):
                try:
                    events = daemon.command("toggle_arm")
                except OSError as exc:
                    _raise_dispatch_failure(daemon, controller, exc)
                overlay.note_action(
                    "ARMED MANUALLY" if daemon.pipeline.engine.armed else "PAUSED MANUALLY"
                )
                _note_action_events(overlay, events)
            if not _window_is_open(config.display.window_name):
                break
        return 0
    finally:
        if daemon is not None:
            try:
                daemon.force_pause("Stopped")
            except Exception as exc:
                print(f"AirControl cleanup warning: input release failed: {exc}")
            try:
                daemon.stop()
            except Exception as exc:
                print(f"AirControl cleanup warning: input sink close failed: {exc}")
        elif controller is not None:
            try:
                controller.release_all()
                controller.close()
            except Exception as exc:
                print(f"AirControl cleanup warning: input sink close failed: {exc}")
        if worker is not None and not worker.stop(config.tracking.shutdown_timeout_seconds):
            print("AirControl cleanup warning: vision worker did not stop before timeout")
        if window_created:
            try:
                cv2.destroyWindow(config.display.window_name)
            except cv2.error:
                pass


def calibrate(
    config: AppConfig,
    config_directory: Path,
    model_override: str | None = None,
) -> int:
    """Run the HUD-guided calibration flow and persist its completed profile."""
    ensure_metrics_consent(config_directory)
    model_setting = model_override or config.tracking.model_path
    model_path = Path(model_setting)
    if not model_path.is_absolute():
        model_path = config_directory / model_path
    model_path = ensure_hand_model(model_path, progress=print)

    runner = CalibrationRunner(config)
    worker: AsyncVisionWorker | None = None
    window_created = False
    try:
        cv2.namedWindow(config.display.window_name, cv2.WINDOW_NORMAL)
        window_created = True
        cv2.moveWindow(config.display.window_name, 30, 30)
        preview_height = round(config.display.preview_width * 480 / 640)
        cv2.resizeWindow(
            config.display.window_name,
            config.display.preview_width,
            preview_height,
        )
        if config.display.always_on_top:
            topmost_property = getattr(cv2, "WND_PROP_TOPMOST", None)
            if topmost_property is not None:
                try:
                    cv2.setWindowProperty(
                        config.display.window_name,
                        topmost_property,
                        1,
                    )
                except cv2.error:
                    pass

        placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
        placeholder[:] = (24, 20, 16)
        starting_frame = _draw_calibration_preview(
            placeholder,
            runner.current_step(),
        )
        cv2.putText(
            starting_frame,
            "Starting local vision...",
            (18, 165),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (230, 230, 230),
            2,
            cv2.LINE_AA,
        )
        cv2.imshow(
            config.display.window_name,
            _resize_preview(starting_frame, config.display.preview_width),
        )
        cv2.waitKey(1)

        worker = AsyncVisionWorker(config.camera, config.tracking, model_path)
        worker.start()
        print(
            "AirControl calibration is running. Follow the preview instructions; "
            "Space advances; Q or Esc cancels."
        )

        started_at = time.monotonic()
        last_result_at = started_at
        last_sequence = -1
        current_frame = placeholder
        window_sized_for_camera = False
        while not runner.is_complete():
            now = time.monotonic()
            snapshot = worker.snapshot()
            if snapshot.error is not None:
                raise CameraError(
                    f"Vision pipeline stopped: {snapshot.error}"
                ) from snapshot.error

            if snapshot.sequence != last_sequence and snapshot.frame is not None:
                last_sequence = snapshot.sequence
                last_result_at = now
                current_frame = snapshot.frame
                runner.feed(
                    snapshot.observation,
                    float(current_frame.mean()),
                    now,
                )
                if not window_sized_for_camera:
                    preview_height = round(
                        config.display.preview_width
                        * current_frame.shape[0]
                        / current_frame.shape[1]
                    )
                    cv2.resizeWindow(
                        config.display.window_name,
                        config.display.preview_width,
                        preview_height,
                    )
                    window_sized_for_camera = True
            elif last_sequence < 0 and (
                now - started_at >= config.tracking.startup_timeout_seconds
            ):
                raise CameraError("Camera and hand tracking did not start in time")
            elif (
                last_sequence >= 0
                and now - last_result_at >= config.tracking.watchdog_seconds
            ):
                raise CameraError("Vision feed stalled during calibration")

            if runner.is_complete():
                break

            rendered = _draw_calibration_preview(
                current_frame,
                runner.current_step(),
            )
            cv2.imshow(
                config.display.window_name,
                _resize_preview(rendered, config.display.preview_width),
            )

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                print("Calibration cancelled; no profile was saved.")
                return 0
            if key in (ord(" "), 10, 13):
                runner.advance()
            if not _window_is_open(config.display.window_name):
                print("Calibration cancelled; no profile was saved.")
                return 0

        profile = runner.result()
        configured = config.store.db_path
        store_path = (
            Path(configured).expanduser() if configured else default_store_path()
        )
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with Store(store_path) as store:
            save_profile(store, profile)
        _print_calibration_summary(profile, store_path)
        return 0
    finally:
        if worker is not None and not worker.stop(
            config.tracking.shutdown_timeout_seconds
        ):
            print("AirControl cleanup warning: vision worker did not stop before timeout")
        if window_created:
            try:
                cv2.destroyWindow(config.display.window_name)
            except cv2.error:
                pass
