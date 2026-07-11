from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import math

from aircontrol.profile import MotionSignature
from aircontrol.trajectory import LandmarkFrame, Trajectory


_CENTER_LANDMARKS = (0, 5, 9, 13, 17)
_MINIMUM_PALM_SIZE = 1e-9


@dataclass(frozen=True, slots=True)
class CandidateSegment:
    trajectory: Trajectory
    t_onset: float
    t_offset: float


class _State(Enum):
    WAITING = auto()
    PRESENT = auto()
    IN_GESTURE = auto()


class SegmentationMachine:
    def __init__(
        self,
        motion: MotionSignature,
        *,
        onset_frames: int = 3,
        offset_frames: int = 4,
        max_duration: float = 2.5,
        presence_frames: int = 5,
        hysteresis: float = 0.8,
    ) -> None:
        self._motion = motion
        self._onset_frames = onset_frames
        self._offset_frames = offset_frames
        self._max_duration = max_duration
        self._presence_frames = presence_frames
        self._hysteresis = hysteresis
        self.reset()

    def update(
        self,
        frame: LandmarkFrame | None,
        armed: bool,
        now: float,
    ) -> CandidateSegment | None:
        if frame is None or not armed:
            self.reset()
            return None

        if self._state is _State.WAITING:
            self._presence_count += 1
            self._previous_frame = frame
            if self._presence_count >= self._presence_frames:
                self._state = _State.PRESENT
            return None

        velocity = self._velocity(frame)
        self._previous_frame = frame

        if self._state is _State.PRESENT:
            return self._update_present(frame, velocity, now)
        return self._update_in_gesture(frame, velocity, now)

    def reset(self) -> None:
        self._state = _State.WAITING
        self._presence_count = 0
        self._onset_count = 0
        self._offset_count = 0
        self._previous_frame: LandmarkFrame | None = None
        self._pending_frames: list[LandmarkFrame] = []
        self._gesture_frames: list[LandmarkFrame] = []
        self._t_onset: float | None = None

    def _update_present(
        self,
        frame: LandmarkFrame,
        velocity: float,
        now: float,
    ) -> None:
        if velocity < self._motion.velocity_floor:
            self._clear_onset()
            return None

        if self._onset_count == 0:
            self._t_onset = now
        self._onset_count += 1
        self._pending_frames.append(frame)

        if self._onset_count < self._onset_frames:
            return None

        self._state = _State.IN_GESTURE
        self._gesture_frames = self._pending_frames
        self._pending_frames = []
        self._offset_count = 0

        if self._duration_exceeded(now):
            self._return_to_present()
        return None

    def _update_in_gesture(
        self,
        frame: LandmarkFrame,
        velocity: float,
        now: float,
    ) -> CandidateSegment | None:
        self._gesture_frames.append(frame)

        if self._duration_exceeded(now):
            self._return_to_present()
            return None

        if velocity < self._motion.velocity_floor * self._hysteresis:
            self._offset_count += 1
        else:
            self._offset_count = 0

        if self._offset_count < self._offset_frames:
            return None

        t_onset = self._t_onset
        if t_onset is None:
            raise RuntimeError("gesture onset time is missing")
        segment = CandidateSegment(
            trajectory=Trajectory(
                frames=tuple(self._gesture_frames),
                handedness=frame.handedness,
            ),
            t_onset=t_onset,
            t_offset=now,
        )
        self._return_to_present()
        return segment

    def _velocity(self, frame: LandmarkFrame) -> float:
        previous = self._previous_frame
        if previous is None:
            return 0.0

        dt = frame.timestamp - previous.timestamp
        if dt <= 0.0:
            return 0.0

        current_center = self._center(frame)
        previous_center = self._center(previous)
        displacement = math.dist(current_center, previous_center)

        wrist = frame.landmarks[0]
        middle_mcp = frame.landmarks[9]
        palm_size = max(
            math.sqrt(
                (middle_mcp.x - wrist.x) ** 2
                + (middle_mcp.y - wrist.y) ** 2
                + (middle_mcp.z - wrist.z) ** 2
            ),
            _MINIMUM_PALM_SIZE,
        )
        return displacement / palm_size / dt

    @staticmethod
    def _center(frame: LandmarkFrame) -> tuple[float, float, float]:
        count = len(_CENTER_LANDMARKS)
        return (
            sum(frame.landmarks[index].x for index in _CENTER_LANDMARKS) / count,
            sum(frame.landmarks[index].y for index in _CENTER_LANDMARKS) / count,
            sum(frame.landmarks[index].z for index in _CENTER_LANDMARKS) / count,
        )

    def _duration_exceeded(self, now: float) -> bool:
        return self._t_onset is not None and now - self._t_onset > self._max_duration

    def _clear_onset(self) -> None:
        self._onset_count = 0
        self._pending_frames = []
        self._t_onset = None

    def _return_to_present(self) -> None:
        self._state = _State.PRESENT
        self._presence_count = self._presence_frames
        self._clear_onset()
        self._offset_count = 0
        self._gesture_frames = []
