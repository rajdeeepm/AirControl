import time

import numpy as np
from aircontrol.config import AppConfig
from aircontrol.vision import AsyncVisionWorker, open_camera
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

    def __init__(self, *_args, **_kwargs):
        self.closed = False
        self.instances.append(self)

    def detect(self, _frame, _timestamp):
        return None

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
    assert worker.stop(1.0)
    assert capture.released
    assert FakeTracker.instances[-1].closed


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
