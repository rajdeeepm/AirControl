from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.engine import GestureEngine
from aircontrol.model import ensure_hand_model
from aircontrol.overlay import GestureOverlay
from aircontrol.privacy import ensure_metrics_consent
from aircontrol.recognizer import StaticPoseRecognizer
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
    worker: AsyncVisionWorker | None = None
    window_created = False
    engine = GestureEngine(config.gestures)
    recognizer = StaticPoseRecognizer(config.gestures)
    overlay = GestureOverlay(show_landmarks=config.display.show_landmarks)
    try:
        controller = ActionController(config.input.pointer_pixels_per_palm, practice=practice)

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
        starting_status = replace(engine.status(), status_text="Starting local vision...")
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
            now = time.monotonic()
            snapshot = worker.snapshot()
            if snapshot.error is not None:
                raise CameraError(f"Vision pipeline stopped: {snapshot.error}") from snapshot.error

            actions = []
            if snapshot.sequence != last_sequence and snapshot.frame is not None:
                last_sequence = snapshot.sequence
                current_frame = snapshot.frame
                current_observation = snapshot.observation
                current_sample = recognizer.recognize(current_observation)
                actions.extend(engine.update(current_sample, now))
                if previous_result_at is not None:
                    frame_interval = max(now - previous_result_at, 1e-6)
                    instantaneous_fps = 1.0 / frame_interval
                    fps = instantaneous_fps if fps == 0.0 else fps * 0.9 + instantaneous_fps * 0.1
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
                actions.extend(engine.update(None, now))
                if now - started_at >= config.tracking.startup_timeout_seconds:
                    raise CameraError("Camera and hand tracking did not start in time")
            elif now - last_result_at >= config.tracking.watchdog_seconds:
                current_observation = None
                current_sample = None
                actions.extend(engine.update(None, now))
                if not watchdog_paused:
                    actions.extend(engine.force_pause("Paused - vision feed stalled"))
                    watchdog_paused = True

            try:
                descriptions = controller.dispatch_all(actions)
            except OSError as exc:
                emergency_actions = engine.force_pause(f"Paused - Windows input failed: {exc}")
                try:
                    controller.dispatch_all(emergency_actions)
                except OSError:
                    pass
                try:
                    controller.release_all()
                except OSError:
                    pass
                raise RuntimeError("Windows input injection failed; AirControl stopped safely") from exc
            for description in descriptions:
                overlay.note_action(description)

            status = engine.status()
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
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                descriptions = controller.dispatch_all(engine.manual_toggle(now))
                overlay.note_action("ARMED MANUALLY" if engine.armed else "PAUSED MANUALLY")
                for description in descriptions:
                    overlay.note_action(description)
            if not _window_is_open(config.display.window_name):
                break
        return 0
    finally:
        if controller is not None:
            try:
                controller.dispatch_all(engine.force_pause("Stopped"))
                controller.release_all()
            except Exception as exc:
                print(f"AirControl cleanup warning: input release failed: {exc}")
            try:
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
