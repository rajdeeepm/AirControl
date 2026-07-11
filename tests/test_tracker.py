from types import SimpleNamespace

import numpy as np

from aircontrol.tracker import HandTracker


class FakeMediaPipe:
    class ImageFormat:
        SRGB = "srgb"

    @staticmethod
    def Image(**kwargs):
        return kwargs


class FakeLandmarker:
    def detect_for_video(self, _image, _timestamp):
        normalized = [SimpleNamespace(x=0.1, y=0.2, z=0.3) for _ in range(21)]
        world = [SimpleNamespace(x=0.01, y=0.02, z=0.03) for _ in range(21)]
        category = SimpleNamespace(category_name="Right", score=0.91)
        return SimpleNamespace(
            hand_landmarks=[normalized],
            hand_world_landmarks=[world],
            handedness=[[category]],
        )


def make_tracker(input_is_mirrored=True):
    tracker = HandTracker.__new__(HandTracker)
    tracker._mp = FakeMediaPipe
    tracker._landmarker = FakeLandmarker()
    tracker._last_timestamp_ms = -1
    tracker._input_is_mirrored = input_is_mirrored
    return tracker


def test_detect_retains_world_landmarks_and_frame_dimensions():
    observation = make_tracker().detect(np.zeros((480, 640, 3), dtype=np.uint8), 0)

    assert observation.handedness == "Right"
    assert observation.image_width == 640
    assert observation.image_height == 480
    assert len(observation.landmarks) == 21
    assert len(observation.world_landmarks) == 21


def test_unmirrored_input_corrects_mediapipe_handedness():
    observation = make_tracker(input_is_mirrored=False).detect(
        np.zeros((480, 640, 3), dtype=np.uint8), 0
    )

    assert observation.handedness == "Left"

