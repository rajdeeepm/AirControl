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
        normalized_left = [
            SimpleNamespace(x=0.1, y=0.2, z=0.3) for _ in range(21)
        ]
        normalized_right = [
            SimpleNamespace(x=0.6, y=0.7, z=0.8) for _ in range(21)
        ]
        world_left = [
            SimpleNamespace(x=0.01, y=0.02, z=0.03) for _ in range(21)
        ]
        world_right = [
            SimpleNamespace(x=0.06, y=0.07, z=0.08) for _ in range(21)
        ]
        left = SimpleNamespace(category_name="Left", score=0.75)
        right = SimpleNamespace(category_name="Right", score=0.91)
        return SimpleNamespace(
            hand_landmarks=[normalized_left, normalized_right],
            hand_world_landmarks=[world_left, world_right],
            handedness=[[left], [right]],
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


def test_detect_hands_returns_each_detected_hand():
    observations = make_tracker().detect_hands(
        np.zeros((480, 640, 3), dtype=np.uint8),
        0,
    )

    assert len(observations) == 2
    assert tuple(observation.handedness for observation in observations) == (
        "Left",
        "Right",
    )
    assert tuple(observation.confidence for observation in observations) == (
        0.75,
        0.91,
    )
    assert observations[0].landmarks[0].x == 0.1
    assert observations[1].landmarks[0].x == 0.6
    assert observations[0].world_landmarks[0].x == 0.01
    assert observations[1].world_landmarks[0].x == 0.06
    assert all(observation.image_width == 640 for observation in observations)
    assert all(observation.image_height == 480 for observation in observations)
    assert all(observation.input_is_mirrored for observation in observations)


def test_detect_still_returns_highest_confidence_hand():
    observation = make_tracker().detect(
        np.zeros((480, 640, 3), dtype=np.uint8),
        0,
    )

    assert observation.handedness == "Right"
    assert observation.confidence == 0.91
    assert observation.landmarks[0].x == 0.6
    assert observation.world_landmarks[0].x == 0.06


def test_unmirrored_input_corrects_mediapipe_handedness():
    observations = make_tracker(input_is_mirrored=False).detect_hands(
        np.zeros((480, 640, 3), dtype=np.uint8),
        0,
    )

    assert tuple(observation.handedness for observation in observations) == (
        "Right",
        "Left",
    )
