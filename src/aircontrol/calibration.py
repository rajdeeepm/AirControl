"""Pure calibration step runner used by the camera-facing application layer."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from aircontrol.config import AppConfig
from aircontrol.density import IncidentalDensity, featurize
from aircontrol.domain import HandObservation, Point2D, Pose
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
)
from aircontrol.recognizer import StaticPoseRecognizer
from aircontrol.trajectory import (
    LandmarkFrame,
    Trajectory,
    frame_from_observation,
    hand_size,
)


_CENTER_LANDMARKS = (0, 5, 9, 13, 17)
_NEGATIVE_CHUNK_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class StepInfo:
    name: str
    instruction: str
    progress: float
    recording: bool


class CalibrationRunner:
    """Collect a calibration profile from a deterministic stream of observations."""

    _STEP_NAMES = (
        "framing",
        "hand_snapshot",
        "motion_signature",
        "negative_capture",
        "lighting",
    )

    def __init__(
        self,
        config: AppConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._clock = clock
        self._recognizer = StaticPoseRecognizer(config.gestures)
        self._step_index = 0
        self._last_feed_at: float | None = None

        self._framing_centers: list[Point2D] = []
        self._volume: InteractionVolume | None = None

        self._snapshot_started_at: float | None = None
        self._hand_sizes: list[float] = []
        self._hand_size: float | None = None

        self._motion_previous: LandmarkFrame | None = None
        self._motion_speeds: list[float] = []
        self._motion_peaks: list[float] = []
        self._motion: MotionSignature | None = None

        self._negative_started_at: float | None = None
        self._negative_chunk_started_at: float | None = None
        self._negative_chunk: list[LandmarkFrame] = []
        self._negative_trajectories: list[Trajectory] = []
        self._incidental_features: tuple[tuple[float, ...], ...] = ()

        self._brightness_values: list[float] = []
        self._lighting_centers: list[Point2D] = []
        self._lighting: LightingProfile | None = None
        self._profile: CalibrationProfile | None = None

    def current_step(self) -> StepInfo:
        if self.is_complete():
            return StepInfo(
                name="complete",
                instruction="Calibration complete.",
                progress=1.0,
                recording=False,
            )

        name = self._STEP_NAMES[self._step_index]
        if name == "framing":
            return StepInfo(
                name=name,
                instruction=(
                    "Move your hand around the interaction area, then press Space."
                ),
                progress=1.0 if len(self._framing_centers) >= 2 else 0.0,
                recording=True,
            )
        if name == "hand_snapshot":
            return StepInfo(
                name=name,
                instruction="Hold an open palm still while the snapshot is recorded.",
                progress=self._timed_progress(
                    self._snapshot_started_at,
                    self.config.calibration.snapshot_seconds,
                ),
                recording=True,
            )
        if name == "motion_signature":
            rep = min(
                len(self._motion_peaks) + 1,
                self.config.calibration.motion_reps,
            )
            return StepInfo(
                name=name,
                instruction=(
                    f"Perform deliberate swipe {rep} of "
                    f"{self.config.calibration.motion_reps}, then press Space."
                ),
                progress=min(
                    1.0,
                    len(self._motion_peaks) / self.config.calibration.motion_reps,
                ),
                recording=True,
            )
        if name == "negative_capture":
            return StepInfo(
                name=name,
                instruction="Work normally while incidental motion is recorded.",
                progress=self._timed_progress(
                    self._negative_started_at,
                    self.config.calibration.negative_seconds,
                ),
                recording=True,
            )
        return StepInfo(
            name=name,
            instruction="Hold your hand nominally still, then press Space.",
            progress=(
                1.0
                if len(self._brightness_values) >= 2
                and len(self._lighting_centers) >= 2
                else 0.0
            ),
            recording=True,
        )

    def advance(self) -> None:
        if self.is_complete():
            return

        name = self._STEP_NAMES[self._step_index]
        if name == "framing":
            self._finish_framing()
        elif name == "motion_signature":
            self._finish_motion_rep()
        elif name == "lighting":
            self._finish_lighting()

    def feed(
        self,
        observation: HandObservation | None,
        frame_brightness: float | None,
        now: float,
    ) -> None:
        if self.is_complete():
            return
        self._last_feed_at = now

        name = self._STEP_NAMES[self._step_index]
        if name == "framing":
            if observation is not None:
                sample = self._recognizer.recognize(observation)
                if sample is not None:
                    self._framing_centers.append(sample.center)
        elif name == "hand_snapshot":
            self._feed_hand_snapshot(observation, now)
        elif name == "motion_signature":
            self._feed_motion_signature(observation, now)
        elif name == "negative_capture":
            self._feed_negative_capture(observation, now)
        elif name == "lighting":
            if frame_brightness is not None:
                self._brightness_values.append(float(frame_brightness))
            if observation is not None:
                sample = self._recognizer.recognize(observation)
                if sample is not None:
                    self._lighting_centers.append(sample.center)

    def is_complete(self) -> bool:
        return self._step_index >= len(self._STEP_NAMES)

    def result(self) -> CalibrationProfile:
        if not self.is_complete() or self._profile is None:
            raise RuntimeError("Calibration is not complete")
        return self._profile

    def _finish_framing(self) -> None:
        if len(self._framing_centers) < 2:
            return

        x_values = [center.x for center in self._framing_centers]
        y_values = [center.y for center in self._framing_centers]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        x_padding = (x_max - x_min) * 0.1
        y_padding = (y_max - y_min) * 0.1
        self._volume = InteractionVolume(
            x_min=x_min - x_padding,
            x_max=x_max + x_padding,
            y_min=y_min - y_padding,
            y_max=y_max + y_padding,
        )
        self._step_index += 1

    def _feed_hand_snapshot(
        self,
        observation: HandObservation | None,
        now: float,
    ) -> None:
        sample = self._recognizer.recognize(observation)
        if observation is not None and sample is not None and sample.pose == Pose.OPEN_PALM:
            if self._snapshot_started_at is None:
                self._snapshot_started_at = now
            frame = frame_from_observation(observation, now)
            self._hand_sizes.append(hand_size(frame))

        if (
            self._snapshot_started_at is not None
            and now - self._snapshot_started_at
            >= self.config.calibration.snapshot_seconds
            and len(self._hand_sizes) >= 2
        ):
            ordered = sorted(self._hand_sizes)
            midpoint = len(ordered) // 2
            if len(ordered) % 2:
                self._hand_size = ordered[midpoint]
            else:
                self._hand_size = (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
            self._step_index += 1

    def _feed_motion_signature(
        self,
        observation: HandObservation | None,
        now: float,
    ) -> None:
        if observation is None:
            self._motion_previous = None
            return

        frame = frame_from_observation(observation, now)
        previous = self._motion_previous
        if previous is not None:
            dt = frame.timestamp - previous.timestamp
            if dt > 0.0:
                displacement = math.dist(
                    _frame_center(frame),
                    _frame_center(previous),
                )
                palm_size = max(hand_size(frame), 1e-9)
                self._motion_speeds.append(displacement / palm_size / dt)
        self._motion_previous = frame

    def _finish_motion_rep(self) -> None:
        if not self._motion_speeds:
            return
        peak = max(self._motion_speeds)
        if peak <= 0.0:
            return

        self._motion_peaks.append(peak)
        self._motion_speeds.clear()
        self._motion_previous = None
        if len(self._motion_peaks) < self.config.calibration.motion_reps:
            return

        # A fraction of the PEAK speed of a practice rep, so keep it well under
        # that peak: people calibrate briskly but gesture deliberately, and a
        # floor near the practice peak makes ordinary gestures unregisterable.
        velocity_floor = 0.15 * min(self._motion_peaks)
        velocity_ceiling = 1.2 * max(self._motion_peaks)
        self._motion = MotionSignature(
            velocity_floor=velocity_floor,
            velocity_ceiling=velocity_ceiling,
        )
        self._step_index += 1

    def _feed_negative_capture(
        self,
        observation: HandObservation | None,
        now: float,
    ) -> None:
        if self._negative_started_at is None:
            self._negative_started_at = now
            self._negative_chunk_started_at = now

        chunk_started_at = self._negative_chunk_started_at
        if chunk_started_at is not None and now - chunk_started_at >= _NEGATIVE_CHUNK_SECONDS:
            self._flush_negative_chunk()
            intervals = max(
                1,
                int((now - chunk_started_at) // _NEGATIVE_CHUNK_SECONDS),
            )
            self._negative_chunk_started_at = (
                chunk_started_at + intervals * _NEGATIVE_CHUNK_SECONDS
            )

        if observation is not None:
            self._negative_chunk.append(frame_from_observation(observation, now))

        if (
            self._negative_started_at is not None
            and now - self._negative_started_at
            >= self.config.calibration.negative_seconds
        ):
            self._flush_negative_chunk()
            self._finish_negative_capture()

    def _flush_negative_chunk(self) -> None:
        if not self._negative_chunk:
            return
        frames = tuple(self._negative_chunk)
        self._negative_trajectories.append(
            Trajectory(frames=frames, handedness=frames[0].handedness)
        )
        self._negative_chunk.clear()

    def _finish_negative_capture(self) -> None:
        if not self._negative_trajectories:
            return

        feature_rows = tuple(
            featurize(trajectory) for trajectory in self._negative_trajectories
        )
        if len(self._negative_trajectories) >= 2:
            self._incidental_features = IncidentalDensity.fit(
                self._negative_trajectories
            ).to_rows()
        else:
            self._incidental_features = feature_rows
        self._step_index += 1

    def _finish_lighting(self) -> None:
        if len(self._brightness_values) < 2 or len(self._lighting_centers) < 2:
            return

        mean_brightness = sum(self._brightness_values) / len(self._brightness_values)
        mean_x = sum(center.x for center in self._lighting_centers) / len(
            self._lighting_centers
        )
        mean_y = sum(center.y for center in self._lighting_centers) / len(
            self._lighting_centers
        )
        mean_squared_distance = sum(
            (center.x - mean_x) ** 2 + (center.y - mean_y) ** 2
            for center in self._lighting_centers
        ) / len(self._lighting_centers)
        landmark_jitter = math.sqrt(mean_squared_distance)
        self._lighting = LightingProfile(
            mean_brightness=mean_brightness,
            landmark_jitter=landmark_jitter,
            acceptable=(
                40.0 <= mean_brightness <= 220.0 and landmark_jitter < 0.05
            ),
        )

        if (
            self._volume is None
            or self._hand_size is None
            or self._motion is None
        ):
            return
        self._profile = CalibrationProfile(
            hand_size=self._hand_size,
            volume=self._volume,
            motion=self._motion,
            lighting=self._lighting,
            incidental_features=self._incidental_features,
            created_at=float(self._clock()),
        )
        self._step_index += 1

    def _timed_progress(self, started_at: float | None, duration: float) -> float:
        if started_at is None or self._last_feed_at is None:
            return 0.0
        return min(1.0, max(0.0, (self._last_feed_at - started_at) / duration))


def _frame_center(frame: LandmarkFrame) -> tuple[float, float, float]:
    count = len(_CENTER_LANDMARKS)
    return (
        sum(frame.landmarks[index].x for index in _CENTER_LANDMARKS) / count,
        sum(frame.landmarks[index].y for index in _CENTER_LANDMARKS) / count,
        sum(frame.landmarks[index].z for index in _CENTER_LANDMARKS) / count,
    )


__all__ = ["CalibrationRunner", "StepInfo"]
