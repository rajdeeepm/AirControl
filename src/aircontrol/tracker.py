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
        observations = self.detect_hands(rgb_frame, timestamp_ms)
        return max(
            observations,
            key=lambda observation: observation.confidence,
            default=None,
        )

    def detect_hands(
        self,
        rgb_frame,
        timestamp_ms: int,
    ) -> tuple[HandObservation, ...]:
        timestamp_ms = max(timestamp_ms, self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb_frame)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.hand_landmarks:
            return ()

        height, width = rgb_frame.shape[:2]
        observations: list[HandObservation] = []
        for index, hand_landmarks in enumerate(result.hand_landmarks):
            categories = (
                result.handedness[index] if len(result.handedness) > index else ()
            )
            category = categories[0] if categories else None
            handedness = category.category_name if category else "Unknown"
            if not self._input_is_mirrored:
                handedness = {"Left": "Right", "Right": "Left"}.get(
                    handedness,
                    handedness,
                )
            landmarks = tuple(
                Point3D(float(point.x), float(point.y), float(point.z))
                for point in hand_landmarks
            )
            world_landmarks = ()
            if result.hand_world_landmarks and len(result.hand_world_landmarks) > index:
                world_landmarks = tuple(
                    Point3D(float(point.x), float(point.y), float(point.z))
                    for point in result.hand_world_landmarks[index]
                )
            observations.append(
                HandObservation(
                    landmarks=landmarks,
                    handedness=handedness,
                    confidence=max(0.0, float(category.score)) if category else 0.0,
                    world_landmarks=world_landmarks,
                    image_width=int(width),
                    image_height=int(height),
                    input_is_mirrored=self._input_is_mirrored,
                )
            )
        return tuple(observations)

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> "HandTracker":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
