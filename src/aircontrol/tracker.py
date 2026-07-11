from __future__ import annotations

import os
import tempfile
from pathlib import Path

from aircontrol.config import TrackingConfig
from aircontrol.domain import HandObservation, Point3D


class HandTracker:
    """Thin wrapper around MediaPipe Tasks' video-mode Hand Landmarker."""

    def __init__(
        self,
        config: TrackingConfig,
        model_path: str | Path,
        input_is_mirrored: bool = True,
    ):
        matplotlib_cache = Path(tempfile.gettempdir()) / "aircontrol-matplotlib"
        matplotlib_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
        except ImportError as exc:
            raise RuntimeError("MediaPipe is not installed. Run setup.cmd first.") from exc

        self._mp = mp
        self._vision = vision
        self._input_is_mirrored = input_is_mirrored
        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=config.max_hands,
            min_hand_detection_confidence=config.min_detection_confidence,
            min_hand_presence_confidence=config.min_presence_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1

    def detect(self, rgb_frame, timestamp_ms: int) -> HandObservation | None:
        timestamp_ms = max(timestamp_ms, self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb_frame)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.hand_landmarks:
            return None

        best_index = 0
        best_score = -1.0
        for index, categories in enumerate(result.handedness):
            score = categories[0].score if categories else 0.0
            if score > best_score:
                best_index = index
                best_score = score

        category = result.handedness[best_index][0] if result.handedness[best_index] else None
        handedness = category.category_name if category else "Unknown"
        if not self._input_is_mirrored:
            handedness = {"Left": "Right", "Right": "Left"}.get(handedness, handedness)
        landmarks = tuple(
            Point3D(float(point.x), float(point.y), float(point.z))
            for point in result.hand_landmarks[best_index]
        )
        world_landmarks = ()
        if result.hand_world_landmarks and len(result.hand_world_landmarks) > best_index:
            world_landmarks = tuple(
                Point3D(float(point.x), float(point.y), float(point.z))
                for point in result.hand_world_landmarks[best_index]
            )
        height, width = rgb_frame.shape[:2]
        return HandObservation(
            landmarks=landmarks,
            handedness=handedness,
            confidence=max(0.0, best_score),
            world_landmarks=world_landmarks,
            image_width=int(width),
            image_height=int(height),
            input_is_mirrored=self._input_is_mirrored,
        )

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> "HandTracker":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
