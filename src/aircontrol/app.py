from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from aircontrol.arena import ArenaSession, effective_t1
from aircontrol.calibration import CalibrationRunner, StepInfo
from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon, default_store_path
from aircontrol.gate import GateThresholds
from aircontrol.ipc import IpcServer
from aircontrol.matcher import DtwMatcher
from aircontrol.model import ensure_hand_model
from aircontrol.overlay import GestureOverlay
from aircontrol.privacy import ensure_metrics_consent
from aircontrol.profile import CalibrationProfile, load_active_profile, save_profile
from aircontrol.recording import RecordingSession
from aircontrol.segmentation import SegmentationMachine
from aircontrol.store import Store
from aircontrol.trajectory import frame_from_observation
from aircontrol.vision import AsyncVisionWorker, CameraError


_HEADLESS_SLEEP_SECONDS = 0.005
_PREVIEW_INTERVAL_SECONDS = 1.0 / 15.0
_PREVIEW_JPEG_QUALITY = 70


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
    headless = config.ipc.enabled
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
            require_ipc=config.ipc.enabled,
        )
        daemon.start()

        if not headless:
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
        if not headless:
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
        if headless:
            print(
                f"AirControl is running in {mode} mode. "
                "Use the app UI to arm, undo, or quit."
            )
        else:
            print(
                f"AirControl is running in {mode} mode. "
                "Open palm arms; fist pauses; Q quits."
            )

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
        last_preview_at: float | None = None
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
                    if not headless and not window_sized_for_camera:
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

            preview_due = (
                headless
                and daemon.preview_enabled
                and (
                    last_preview_at is None
                    or now - last_preview_at >= _PREVIEW_INTERVAL_SECONDS
                )
            )
            if not headless or preview_due:
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
                resized = _resize_preview(
                    rendered,
                    config.display.preview_width,
                )
                if headless:
                    last_preview_at = now
                    encoded, jpeg = cv2.imencode(
                        ".jpg",
                        resized,
                        [
                            int(cv2.IMWRITE_JPEG_QUALITY),
                            _PREVIEW_JPEG_QUALITY,
                        ],
                    )
                    if encoded:
                        daemon.broadcast_preview(jpeg.tobytes())
                else:
                    cv2.imshow(config.display.window_name, resized)

            if headless:
                time.sleep(_HEADLESS_SLEEP_SECONDS)
                continue

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


def _open_store_with_profile(config: AppConfig) -> tuple[Store, CalibrationProfile] | None:
    """Open the configured store and load the active profile, or explain how."""
    configured = config.store.db_path
    store_path = (
        Path(configured).expanduser() if configured else default_store_path()
    )
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(store_path)
    profile = load_active_profile(store)
    if profile is None:
        store.close()
        print(
            "No active calibration profile found. "
            "Run calibration first: aircontrol --calibrate (or calibrate.cmd)."
        )
        return None
    return store, profile


def _draw_banner(frame, title: str, lines: list[str], footer: str):
    rendered = frame.copy()
    width = rendered.shape[1]
    cv2.rectangle(rendered, (0, 0), (width, 118), (18, 18, 18), -1)
    cv2.putText(
        rendered,
        title,
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
            (18, 58 + index * 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (230, 230, 230),
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        rendered,
        footer,
        (18, 110),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (80, 220, 140),
        1,
        cv2.LINE_AA,
    )
    return rendered


def _gesture_name(store: Store, gesture_id: int | None) -> str:
    if gesture_id is None:
        return "(no match)"
    record = store.gestures.get(gesture_id)
    return record.name if record is not None else f"gesture {gesture_id}"


class _HudCapture:
    """Shared camera + preview scaffolding for the recording/arena HUD loops."""

    def __init__(self, config: AppConfig, model_path: Path):
        self.config = config
        self.worker = AsyncVisionWorker(config.camera, config.tracking, model_path)
        self.window_created = False
        self.started_at = time.monotonic()
        self.last_result_at = self.started_at
        self.last_sequence = -1
        placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
        placeholder[:] = (24, 20, 16)
        self.current_frame = placeholder
        self.window_sized = False

    def open_window(self) -> None:
        cv2.namedWindow(self.config.display.window_name, cv2.WINDOW_NORMAL)
        self.window_created = True
        cv2.moveWindow(self.config.display.window_name, 30, 30)
        preview_height = round(self.config.display.preview_width * 480 / 640)
        cv2.resizeWindow(
            self.config.display.window_name,
            self.config.display.preview_width,
            preview_height,
        )
        self.worker.start()

    def poll(self, now: float):
        """Return the newest observation, or None when there is no new frame."""
        snapshot = self.worker.snapshot()
        if snapshot.error is not None:
            raise CameraError(
                f"Vision pipeline stopped: {snapshot.error}"
            ) from snapshot.error
        if snapshot.sequence != self.last_sequence and snapshot.frame is not None:
            self.last_sequence = snapshot.sequence
            self.last_result_at = now
            self.current_frame = snapshot.frame
            if not self.window_sized:
                preview_height = round(
                    self.config.display.preview_width
                    * self.current_frame.shape[0]
                    / self.current_frame.shape[1]
                )
                cv2.resizeWindow(
                    self.config.display.window_name,
                    self.config.display.preview_width,
                    preview_height,
                )
                self.window_sized = True
            return snapshot.observation, True
        if self.last_sequence < 0:
            if now - self.started_at >= self.config.tracking.startup_timeout_seconds:
                raise CameraError("Camera and hand tracking did not start in time")
        elif now - self.last_result_at >= self.config.tracking.watchdog_seconds:
            raise CameraError("Vision feed stalled")
        return None, False

    def show(self, rendered) -> int:
        cv2.imshow(
            self.config.display.window_name,
            _resize_preview(rendered, self.config.display.preview_width),
        )
        return cv2.waitKey(1) & 0xFF

    def window_closed(self) -> bool:
        return not _window_is_open(self.config.display.window_name)

    def close(self) -> None:
        if not self.worker.stop(self.config.tracking.shutdown_timeout_seconds):
            print(
                "AirControl cleanup warning: vision worker did not stop before timeout"
            )
        if self.window_created:
            try:
                cv2.destroyWindow(self.config.display.window_name)
            except cv2.error:
                pass


def record_gesture(
    config: AppConfig,
    config_directory: Path,
    name: str,
    model_override: str | None = None,
) -> int:
    """Guided custom-gesture recording with live take confirmation."""
    opened = _open_store_with_profile(config)
    if opened is None:
        return 2
    store, profile = opened

    capture: _HudCapture | None = None
    try:
        ensure_metrics_consent(config_directory)
        model_setting = model_override or config.tracking.model_path
        model_path = Path(model_setting)
        if not model_path.is_absolute():
            model_path = config_directory / model_path
        model_path = ensure_hand_model(model_path, progress=print)

        session = RecordingSession(name, store, profile, config)
        rec = session.rec_config
        capture = _HudCapture(config, model_path)
        capture.open_window()
        print(
            f"Recording '{name}'. Perform the gesture; Space keeps a take, "
            "X discards it, Q cancels."
        )

        pending_frames: int | None = None
        outcome = None
        while True:
            now = time.monotonic()
            observation, fresh = capture.poll(now)
            if fresh:
                frame = (
                    frame_from_observation(observation, now)
                    if observation is not None
                    else None
                )
                event = session.feed(frame, now)
                if event is not None:
                    pending_frames = event.frame_count

            if pending_frames is not None:
                status = (
                    f"Take captured ({pending_frames} frames) — "
                    "SPACE keep · X discard"
                )
            else:
                status = "Perform the gesture deliberately, then pause."
            rendered = _draw_banner(
                capture.current_frame,
                f"RECORD: {name}",
                [
                    f"Takes kept: {session.takes_confirmed}"
                    f"/{rec.min_takes}-{rec.max_takes}",
                    status,
                ],
                "SPACE keep take · X discard · Q cancel",
            )
            key = capture.show(rendered)
            if key in (ord("q"), ord("Q"), 27):
                print("Recording cancelled; nothing was saved.")
                return 0
            if key in (ord(" "), 10, 13) and pending_frames is not None:
                session.confirm_take()
                pending_frames = None
                if session.takes_confirmed >= rec.min_takes:
                    outcome = session.finish()
                    break
            if key in (ord("x"), ord("X")) and pending_frames is not None:
                session.discard_take()
                pending_frames = None
            if capture.window_closed():
                print("Recording cancelled; nothing was saved.")
                return 0

        if outcome is not None and outcome.saved:
            print(
                f"Gesture '{name}' saved with {session.takes_confirmed} takes. "
                "Practice it now: aircontrol --arena"
            )
            return 0
        reason = outcome.reason if outcome is not None else "unknown"
        if outcome is not None and outcome.conflict_gesture_id is not None:
            conflict = _gesture_name(store, outcome.conflict_gesture_id)
            print(
                f"Refused to save '{name}': {reason} "
                f"(closest existing gesture: {conflict}). "
                "Make the motion larger, slower, or more distinct and try again."
            )
        else:
            print(
                f"Refused to save '{name}': {reason}. "
                "Make the motion more consistent and distinct, then try again."
            )
        return 3
    finally:
        if capture is not None:
            capture.close()
        store.close()


def arena(
    config: AppConfig,
    config_directory: Path,
    stress: bool,
    model_override: str | None = None,
) -> int:
    """Practice arena: live match feedback with confirm/reject learning."""
    opened = _open_store_with_profile(config)
    if opened is None:
        return 2
    store, profile = opened

    capture: _HudCapture | None = None
    try:
        ensure_metrics_consent(config_directory)
        model_setting = model_override or config.tracking.model_path
        model_path = Path(model_setting)
        if not model_path.is_absolute():
            model_path = config_directory / model_path
        model_path = ensure_hand_model(model_path, progress=print)

        matcher = DtwMatcher(store)
        matcher.refresh()
        if not store.gestures.list():
            print(
                "The gesture library is empty. Record one first: "
                "aircontrol --record-gesture NAME"
            )
        segmentation = SegmentationMachine(profile.motion)
        session = ArenaSession(store)
        base_thresholds = GateThresholds()
        if stress:
            session.start_stress(60.0)
            print(
                "Stress test: work normally for 60 seconds; "
                "any fire counts against the gestures."
            )

        capture = _HudCapture(config, model_path)
        capture.open_window()
        print(
            "Practice arena running. Space confirms a match, X rejects it, "
            "Q quits."
        )

        prompt_line = "Perform any gesture from your library."
        can_judge = False
        while True:
            now = time.monotonic()
            observation, fresh = capture.poll(now)
            if fresh:
                frame = (
                    frame_from_observation(observation, now)
                    if observation is not None
                    else None
                )
                segment = segmentation.update(frame, armed=True, now=now)
                if segment is not None:
                    result = matcher.match(segment.trajectory)
                    offset = (
                        store.gesture_stats.get(result.gesture_id).threshold_offset
                        if result.gesture_id is not None
                        else 0.0
                    )
                    fired = (
                        result.gesture_id is not None
                        and result.top1 >= effective_t1(base_thresholds, offset)
                    )
                    prompt = session.observe(segment, result, fired)
                    top_name = _gesture_name(store, prompt.gesture_id)
                    runner_name = _gesture_name(store, prompt.runner_up_id)
                    prompt_line = (
                        f"{prompt.top1:.2f} {top_name} | "
                        f"runner-up {prompt.top2:.2f} {runner_name}"
                    )
                    can_judge = True

            if stress and session.stress_active:
                status_line = (
                    f"STRESS MODE — fires so far: {session.stress_fires}"
                )
            elif stress:
                status_line = (
                    f"Stress finished — false fires: {session.stress_fires}"
                )
            else:
                status_line = "SPACE = that was right · X = wrong"
            rendered = _draw_banner(
                capture.current_frame,
                "PRACTICE ARENA",
                [prompt_line, status_line],
                "SPACE confirm · X reject · Q quit",
            )
            key = capture.show(rendered)
            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (ord(" "), 10, 13) and can_judge:
                session.confirm()
                matcher.refresh()
                can_judge = False
            if key in (ord("x"), ord("X")) and can_judge:
                session.reject()
                can_judge = False
            if capture.window_closed():
                break

        summary = session.summary()
        print("Arena summary:")
        for key_id, value in summary.items():
            if key_id == "stress_fires":
                continue
            name = _gesture_name(store, int(key_id))
            print(
                f"  {name}: confirms={value['confirms']} "
                f"rejects={value['rejects']} "
                f"threshold_offset={value['threshold_offset']:.2f}"
            )
        if stress:
            print(f"  stress fires: {summary['stress_fires']}")
        return 0
    finally:
        if capture is not None:
            capture.close()
        store.close()
