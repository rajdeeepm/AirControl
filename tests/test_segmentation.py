from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from aircontrol.domain import Point3D
from aircontrol.profile import MotionSignature
from aircontrol.segmentation import CandidateSegment, SegmentationMachine
from aircontrol.trajectory import LandmarkFrame, Trajectory


_DT = 0.1
_FLOOR = 1.0
_MOTION = MotionSignature(velocity_floor=_FLOOR, velocity_ceiling=3.0)


def _translated_hand(x: float, timestamp: float) -> LandmarkFrame:
    landmarks = [
        Point3D(x=x + index * 0.01, y=index * 0.02, z=0.0)
        for index in range(21)
    ]
    landmarks[0] = Point3D(x=x, y=0.0, z=0.0)
    landmarks[9] = Point3D(x=x, y=1.0, z=0.0)
    return LandmarkFrame(
        landmarks=tuple(landmarks),
        handedness="Right",
        timestamp=timestamp,
    )


def _frames_for_velocities(
    velocities: list[float],
    *,
    start_time: float = 0.0,
    dt: float = _DT,
) -> tuple[LandmarkFrame, ...]:
    """Translate all landmarks together while keeping palm size exactly one."""
    x = 0.0
    frames = [_translated_hand(x, start_time)]
    for index, velocity in enumerate(velocities, start=1):
        x += velocity * dt
        frames.append(_translated_hand(x, start_time + index * dt))
    return tuple(frames)


def _feed(
    machine: SegmentationMachine,
    frames: tuple[LandmarkFrame, ...],
    *,
    armed: bool = True,
) -> list[CandidateSegment]:
    segments = []
    for frame in frames:
        segment = machine.update(frame, armed=armed, now=frame.timestamp)
        if segment is not None:
            segments.append(segment)
    return segments


def test_deliberate_motion_burst_emits_exactly_one_segment() -> None:
    machine = SegmentationMachine(_MOTION)
    velocities = [0.0] * 4 + [1.2] * 5 + [0.2] * 4 + [0.2] * 3
    frames = _frames_for_velocities(velocities)

    segments = _feed(machine, frames)

    assert len(segments) == 1
    segment = segments[0]
    onset_index = 5
    offset_index = 13
    assert segment.t_onset == pytest.approx(frames[onset_index].timestamp)
    assert segment.t_offset == pytest.approx(frames[offset_index].timestamp)
    assert segment.t_onset < segment.t_offset
    assert segment.trajectory.frames == frames[onset_index : offset_index + 1]
    assert segment.trajectory.frames
    assert all(
        segment.t_onset <= frame.timestamp <= segment.t_offset
        for frame in segment.trajectory.frames
    )


def test_sub_floor_jitter_never_emits() -> None:
    machine = SegmentationMachine(_MOTION)
    frames = _frames_for_velocities([0.4] * 30)

    assert _feed(machine, frames) == []


def test_hysteresis_band_does_not_end_active_gesture() -> None:
    machine = SegmentationMachine(_MOTION)
    velocities = [0.0] * 4 + [1.2] * 3 + [0.9] * 6 + [1.2] + [0.2] * 4
    frames = _frames_for_velocities(velocities)

    segments = _feed(machine, frames)

    assert len(segments) == 1
    segment = segments[0]
    assert segment.t_offset == pytest.approx(frames[-1].timestamp)
    band_timestamps = {frame.timestamp for frame in frames[8:14]}
    assert band_timestamps.issubset(
        {frame.timestamp for frame in segment.trajectory.frames}
    )


def test_overlong_motion_is_discarded_and_machine_recovers_in_present() -> None:
    machine = SegmentationMachine(_MOTION)
    long_motion = [0.0] * 4 + [1.2] * 27
    recovery = [0.2] + [1.2] * 3 + [0.2] * 4
    frames = _frames_for_velocities(long_motion + recovery)
    long_motion_frame_count = len(long_motion) + 1

    assert _feed(machine, frames[:long_motion_frame_count]) == []

    segments = _feed(machine, frames[long_motion_frame_count:])
    assert len(segments) == 1
    assert segments[0].t_onset > frames[long_motion_frame_count - 1].timestamp


def test_presence_debounce_frames_do_not_count_toward_onset() -> None:
    machine = SegmentationMachine(_MOTION)
    before_valid_burst = [1.2] * 6 + [0.2] * 4
    valid_burst = [1.2] * 3 + [0.2] * 4
    frames = _frames_for_velocities(before_valid_burst + valid_burst)
    prefix_frame_count = len(before_valid_burst) + 1

    assert _feed(machine, frames[:prefix_frame_count]) == []

    segments = _feed(machine, frames[prefix_frame_count:])
    assert len(segments) == 1
    first_valid_onset = frames[prefix_frame_count]
    assert segments[0].t_onset == pytest.approx(first_valid_onset.timestamp)
    assert segments[0].trajectory.frames[0] == first_valid_onset


def test_hand_disappearing_mid_gesture_discards_and_resets() -> None:
    machine = SegmentationMachine(_MOTION)
    partial_frames = _frames_for_velocities([0.0] * 4 + [1.2] * 3)

    assert _feed(machine, partial_frames) == []
    disappearance_time = partial_frames[-1].timestamp + _DT
    assert machine.update(None, armed=True, now=disappearance_time) is None

    before_valid_burst = [1.2] * 6 + [0.2] * 4
    valid_burst = [1.2] * 3 + [0.2] * 4
    reentry_frames = _frames_for_velocities(
        before_valid_burst + valid_burst,
        start_time=disappearance_time + _DT,
    )
    prefix_frame_count = len(before_valid_burst) + 1
    assert _feed(machine, reentry_frames[:prefix_frame_count]) == []
    assert len(_feed(machine, reentry_frames[prefix_frame_count:])) == 1


def test_disarming_mid_gesture_never_emits() -> None:
    machine = SegmentationMachine(_MOTION)
    frames = _frames_for_velocities([0.0] * 4 + [1.2] * 3 + [0.2] * 4)

    assert _feed(machine, frames[:-1]) == []
    assert machine.update(frames[-1], armed=False, now=frames[-1].timestamp) is None

    rearmed_frames = _frames_for_velocities(
        [1.2] * 6 + [0.2] * 4,
        start_time=frames[-1].timestamp + _DT,
    )
    assert _feed(machine, rearmed_frames) == []

    unarmed_frames = _frames_for_velocities(
        [1.2] * 12,
        start_time=rearmed_frames[-1].timestamp + _DT,
    )
    assert _feed(machine, unarmed_frames, armed=False) == []


def test_reset_discards_partial_gesture() -> None:
    machine = SegmentationMachine(_MOTION)
    partial_frames = _frames_for_velocities([0.0] * 4 + [1.2] * 3)

    assert _feed(machine, partial_frames) == []
    machine.reset()

    trailing_frames = _frames_for_velocities(
        [0.2] * 4,
        start_time=partial_frames[-1].timestamp + _DT,
    )
    assert _feed(machine, trailing_frames) == []


def test_candidate_segment_is_frozen_and_slotted() -> None:
    frame = _translated_hand(0.0, 0.0)
    segment = CandidateSegment(
        trajectory=Trajectory(frames=(frame,), handedness="Right"),
        t_onset=0.0,
        t_offset=0.1,
    )

    assert hasattr(CandidateSegment, "__slots__")
    assert not hasattr(segment, "__dict__")
    with pytest.raises(FrozenInstanceError):
        segment.t_offset = 0.2
