from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import math

from aircontrol.profile import MotionSignature
from aircontrol.trajectory import LandmarkFrame, Trajectory


_CENTER_LANDMARKS = (0, 5, 9, 13, 17)
_MINIMUM_PALM_SIZE = 1e-9

# Calibration derives the floor from the PEAK speed of a few practice reps, so a
# brisk calibration can leave it far above the speed of a deliberate gesture. A
# floor that high forces the user to swing their arm to register anything, and
# clips the slow start and end off whatever it does capture — which then varies
# per attempt and stops matching. Palm-normalised speeds above this ceiling
# never reflect a gesture people can repeat, so cap what segmentation asks for.
# This also repairs profiles that were already saved with an unusable floor.
_MAX_VELOCITY_FLOOR = 1.0


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
        missing_grace_frames: int = 6,
        max_frame_gap: float = 0.5,
        max_velocity_floor: float = _MAX_VELOCITY_FLOOR,
    ) -> None:
        self._motion = motion
        self._velocity_floor = min(motion.velocity_floor, max_velocity_floor)
        self._onset_frames = onset_frames
        self._offset_frames = offset_frames
        self._max_duration = max_duration
        self._presence_frames = presence_frames
        self._hysteresis = hysteresis
        self._missing_grace_frames = missing_grace_frames
        self._max_frame_gap = max_frame_gap
        self.reset()

    def update(
        self,
        frame: LandmarkFrame | None,
        armed: bool,
        now: float,
    ) -> CandidateSegment | None:
        if not armed:
            self.reset()
            return None

        if frame is None:
            return self._handle_missing_frame()

        self._missing_count = 0

        if self._state is _State.WAITING:
            self._presence_count += 1
            self._previous_frame = frame
            if self._presence_count >= self._presence_frames:
                self._state = _State.PRESENT
            return None

        previous = self._previous_frame
        if previous is not None and now - previous.timestamp > self._max_frame_gap:
            # The gap since the last real sample is too large to trust as a
            # velocity measurement (e.g. a long detection dropout absorbed by
            # the missing-frame grace below). Re-baseline on this frame
            # instead of computing a spurious high-velocity sample.
            self._previous_frame = frame
            return None

        velocity = self._velocity(frame)
        self._previous_frame = frame

        if self._state is _State.PRESENT:
            return self._update_present(frame, velocity, now)
        return self._update_in_gesture(frame, velocity, now)

    def _handle_missing_frame(self) -> CandidateSegment | None:
        """Handle a frame with no detected hand while armed.

        In WAITING there is no in-progress onset/gesture to preserve, so a
        missing frame resets immediately as before. In PRESENT or IN_GESTURE
        we tolerate a short run of missing frames (common during fast motion,
        where detection briefly drops due to motion blur) without discarding
        onset/offset progress: hold the current state and do not touch
        velocity, onset, offset, or ``previous_frame``. Only once the missing
        run exceeds ``missing_grace_frames`` do we give up and reset.
        """
        if self._state is _State.WAITING:
            self.reset()
            return None

        self._missing_count += 1
        if self._missing_count > self._missing_grace_frames:
            self.reset()
        return None

    def reset(self) -> None:
        self._state = _State.WAITING
        self._presence_count = 0
        self._onset_count = 0
        self._offset_count = 0
        self._missing_count = 0
        self._previous_frame: LandmarkFrame | None = None
        self._pending_frames: list[LandmarkFrame] = []
        self._gesture_frames: list[LandmarkFrame] = []
        self._t_onset: float | None = None

    @property
    def state_name(self) -> str:
        """Cheap, string-typed view of the current state for UI feedback."""
        return self._state.name.lower()

    def _update_present(
        self,
        frame: LandmarkFrame,
        velocity: float,
        now: float,
    ) -> None:
        if velocity < self._velocity_floor:
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

        if velocity < self._velocity_floor * self._hysteresis:
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
