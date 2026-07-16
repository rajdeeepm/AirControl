from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2

from aircontrol.config import CameraConfig, TrackingConfig
from aircontrol.domain import HandObservation
from aircontrol.tracker import HandTracker


class CameraError(RuntimeError):
    pass


CAMERA_UNAVAILABLE_MESSAGE = (
    "Camera unavailable — it may be turned off, in use by another app, or "
    "blocked in Windows camera privacy settings. Turn it on there, then Retry."
)


@dataclass(frozen=True, slots=True)
class VisionSnapshot:
    sequence: int
    frame: object | None
    observation: HandObservation | None
    published_at: float | None
    error: BaseException | None
    observations: tuple[HandObservation, ...] = ()


def open_camera(config: CameraConfig):
    """Open the first backend that can actually return a frame."""
    backends = [cv2.CAP_ANY]
    if os.name == "nt":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    for backend in backends:
        capture = None
        accepted = False
        try:
            capture = cv2.VideoCapture(config.index, backend)
            if capture is None or not capture.isOpened():
                continue
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
            capture.set(cv2.CAP_PROP_FPS, config.fps)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            read_timeout = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
            if read_timeout is not None:
                capture.set(read_timeout, 750)
            success, first_frame = capture.read()
            if success and first_frame is not None:
                accepted = True
                return capture, first_frame
        except Exception:
            # Backend-specific OpenCV failures should not leak opaque errors to
            # the app. Try the next backend, then surface one actionable reason.
            pass
        finally:
            if capture is not None and not accepted:
                try:
                    capture.release()
                except Exception:
                    pass
    if os.name == "nt":
        raise CameraError(CAMERA_UNAVAILABLE_MESSAGE)
    raise CameraError(
        f"Camera {config.index} could not return a frame. Close other camera apps "
        "or change camera.index in config.json."
    )


def ensure_hud_frame_size(frame, minimum_height: int = 480):
    if frame.shape[0] >= minimum_height:
        return frame
    scale = minimum_height / frame.shape[0]
    width = max(1, round(frame.shape[1] * scale))
    return cv2.resize(frame, (width, minimum_height), interpolation=cv2.INTER_CUBIC)


class AsyncVisionWorker:
    """Owns blocking camera/inference calls so the safety loop never stalls."""

    def __init__(
        self,
        camera_config: CameraConfig,
        tracking_config: TrackingConfig,
        model_path: str | Path,
        *,
        tracker_factory: Callable[..., object] = HandTracker,
        camera_factory: Callable[[CameraConfig], tuple[object, object]] = open_camera,
    ) -> None:
        self.camera_config = camera_config
        self.tracking_config = tracking_config
        self.model_path = Path(model_path)
        self._tracker_factory = tracker_factory
        self._camera_factory = camera_factory
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._capture = None
        self._sequence = -1
        self._frame = None
        self._observation: HandObservation | None = None
        self._observations: tuple[HandObservation, ...] = ()
        self._published_at: float | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("vision worker has already started")
        self._thread = threading.Thread(target=self._run, name="aircontrol-vision", daemon=True)
        self._thread.start()

    def snapshot(self) -> VisionSnapshot:
        with self._lock:
            return VisionSnapshot(
                sequence=self._sequence,
                frame=self._frame,
                observation=self._observation,
                published_at=self._published_at,
                error=self._error,
                observations=self._observations,
            )

    def stop(self, timeout: float) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        timeout = max(0.0, timeout)
        started = time.monotonic()
        thread.join(timeout=min(timeout, 0.5))
        if thread.is_alive():
            capture = self._capture
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            remaining = max(0.0, timeout - (time.monotonic() - started))
            thread.join(timeout=remaining)
        return not thread.is_alive()

    def _publish(
        self,
        frame,
        observations: tuple[HandObservation, ...],
    ) -> None:
        with self._lock:
            self._sequence += 1
            self._frame = frame
            self._observations = observations
            self._observation = max(
                observations,
                key=lambda observation: observation.confidence,
                default=None,
            )
            self._published_at = time.monotonic()

    def _set_error(self, error: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = error

    def _run(self) -> None:
        tracker = None
        capture = None
        try:
            tracker = self._tracker_factory(
                self.tracking_config,
                self.model_path,
                input_is_mirrored=self.camera_config.mirror,
            )
            if self._stop.is_set():
                return
            capture, frame = self._camera_factory(self.camera_config)
            self._capture = capture
            started_at = time.monotonic()
            failed_frames = 0

            while not self._stop.is_set():
                if frame is None:
                    success, frame = capture.read()
                    if not success or frame is None:
                        failed_frames += 1
                        if failed_frames >= 12:
                            raise CameraError("The camera stopped returning frames")
                        time.sleep(0.02)
                        continue
                failed_frames = 0
                frame = ensure_hud_frame_size(frame)
                if self.camera_config.mirror:
                    frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                timestamp_ms = int((time.monotonic() - started_at) * 1000)
                observations = tracker.detect_hands(rgb_frame, timestamp_ms)
                self._publish(frame, observations)
                frame = None
        except BaseException as exc:
            if not self._stop.is_set():
                self._set_error(exc)
        finally:
            if tracker is not None:
                try:
                    tracker.close()
                except Exception as exc:
                    if not self._stop.is_set():
                        self._set_error(exc)
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            self._capture = None
            self._finished.set()
