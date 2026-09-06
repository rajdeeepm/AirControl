import time

import numpy as np
import pytest
from aircontrol.config import AppConfig
from aircontrol.domain import HandObservation, Point3D
from aircontrol.vision import (
    AsyncVisionWorker,
    CameraError,
    VisionSnapshot,
    open_camera,
)
import aircontrol.vision as vision_module


class RepeatingCapture:
    def __init__(self, frame):
        self.frame = frame
        self.released = False

    def read(self):
        if self.released:
            return False, None
        return True, self.frame.copy()

    def release(self):
        self.released = True


class FakeTracker:
    instances = []
    observations = (
        HandObservation(
            landmarks=(Point3D(0.1, 0.2, 0.3),),
            handedness="Left",
            confidence=0.4,
        ),
        HandObservation(
            landmarks=(Point3D(0.6, 0.7, 0.8),),
            handedness="Right",
            confidence=0.9,
        ),
    )

    def __init__(self, *_args, **_kwargs):
        self.closed = False
        self.instances.append(self)

    def detect_hands(self, _frame, _timestamp):
        return self.observations

    def close(self):
        self.closed = True


def test_worker_upscales_small_frames_and_stops_cleanly(tmp_path):
    config = AppConfig.defaults()
    raw_frame = np.zeros((240, 320, 3), dtype=np.uint8)
    capture = RepeatingCapture(raw_frame)
    worker = AsyncVisionWorker(
        config.camera,
        config.tracking,
        tmp_path / "model.task",
        tracker_factory=FakeTracker,
        camera_factory=lambda _config: (capture, raw_frame.copy()),
    )
    worker.start()

    deadline = time.monotonic() + 2.0
    snapshot = worker.snapshot()
    while snapshot.sequence < 0 and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = worker.snapshot()

    assert snapshot.error is None
    assert snapshot.sequence >= 0
    assert snapshot.frame.shape[:2] == (480, 640)
    assert snapshot.observations == FakeTracker.observations
    assert snapshot.observation is FakeTracker.observations[1]
    assert worker.stop(1.0)
    assert capture.released
    assert FakeTracker.instances[-1].closed


def test_snapshot_observations_default_is_backward_compatible() -> None:
    snapshot = VisionSnapshot(-1, None, None, None, None)

    assert snapshot.observations == ()


def test_camera_backend_must_return_a_frame_before_it_is_accepted(monkeypatch):
    config = AppConfig.defaults().camera
    good_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    created = []

    class BackendCapture:
        def __init__(self, succeeds):
            self.succeeds = succeeds
            self.released = False

        def isOpened(self):
            return True

        def set(self, *_args):
            return True

        def read(self):
            return (True, good_frame) if self.succeeds else (False, None)

        def release(self):
            self.released = True

    def fake_video_capture(_index, _backend):
        capture = BackendCapture(succeeds=len(created) == 1)
        created.append(capture)
        return capture

    monkeypatch.setattr(vision_module.os, "name", "nt")
    monkeypatch.setattr(vision_module.cv2, "VideoCapture", fake_video_capture)

    capture, frame = open_camera(config)

    assert created[0].released
    assert capture is created[1]
    assert frame is good_frame


def test_open_camera_failure_has_actionable_windows_permission_message(monkeypatch):
    config = AppConfig.defaults().camera

    class UnavailableCapture:
        def __init__(self):
            self.released = False

        def isOpened(self):
            return False

        def release(self):
            self.released = True

    captures = []

    def fake_video_capture(_index, _backend):
        capture = UnavailableCapture()
        captures.append(capture)
        return capture

    monkeypatch.setattr(vision_module.os, "name", "nt")
    monkeypatch.setattr(vision_module.cv2, "VideoCapture", fake_video_capture)

    with pytest.raises(CameraError) as exc_info:
        open_camera(config)

    assert str(exc_info.value) == (
        "Camera unavailable, it may be turned off, in use by another app, or "
        "blocked in Windows camera privacy settings. Turn it on there, then Retry."
    )
    assert len(captures) == 3
    assert all(capture.released for capture in captures)


def test_open_camera_none_device_has_actionable_message(monkeypatch):
    config = AppConfig.defaults().camera
    monkeypatch.setattr(vision_module.os, "name", "nt")
    monkeypatch.setattr(
        vision_module.cv2,
        "VideoCapture",
        lambda _index, _backend: None,
    )

    with pytest.raises(CameraError) as exc_info:
        open_camera(config)

    assert str(exc_info.value) == (
        "Camera unavailable, it may be turned off, in use by another app, or "
        "blocked in Windows camera privacy settings. Turn it on there, then Retry."
    )


def test_open_camera_non_windows_failure_keeps_camera_index_guidance(monkeypatch):
    config = AppConfig.defaults().camera
    monkeypatch.setattr(vision_module.os, "name", "posix")
    monkeypatch.setattr(
        vision_module.cv2,
        "VideoCapture",
        lambda _index, _backend: None,
    )

    with pytest.raises(CameraError) as exc_info:
        open_camera(config)

    message = str(exc_info.value)
    assert "camera.index" in message
    assert "Windows camera privacy settings" not in message
