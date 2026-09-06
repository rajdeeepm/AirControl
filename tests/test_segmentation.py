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
# Must track SegmentationMachine's constructor defaults.
_MISSING_GRACE_FRAMES = 6
_MAX_FRAME_GAP = 0.5


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
    start_x: float = 0.0,
) -> tuple[LandmarkFrame, ...]:
    """Translate all landmarks together while keeping palm size exactly one."""
    x = start_x
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


def test_brief_dropout_mid_gesture_is_tolerated_and_still_segments() -> None:
    """FIX 1: a couple of dropped detections during the fast part of a
    gesture (motion blur) must not wipe out onset/offset progress. The exact
    same velocity profile as
    ``test_deliberate_motion_burst_emits_exactly_one_segment`` still yields
    one segment even though two frames mid-burst report no hand at all.
    """
    machine = SegmentationMachine(_MOTION)
    velocities = [0.0] * 4 + [1.2] * 5 + [0.2] * 4 + [0.2] * 3
    frames = _frames_for_velocities(velocities)
    dropped_indices = {6, 7}

    segments: list[CandidateSegment] = []
    for index, frame in enumerate(frames):
        fed_frame = None if index in dropped_indices else frame
        segment = machine.update(fed_frame, armed=True, now=frame.timestamp)
        if segment is not None:
            segments.append(segment)

    assert len(segments) == 1
    segment = segments[0]
    assert segment.t_onset < segment.t_offset
    # None of the dropped timestamps leak into the captured trajectory.
    dropped_timestamps = {frames[index].timestamp for index in dropped_indices}
    assert not dropped_timestamps.intersection(
        frame.timestamp for frame in segment.trajectory.frames
    )


def test_missing_frames_beyond_grace_still_reset_the_machine() -> None:
    """A dropout longer than ``missing_grace_frames`` gives up and resets,
    same as full re-detection was always required for genuinely long gaps.
    """
    machine = SegmentationMachine(_MOTION)
    partial_frames = _frames_for_velocities([0.0] * 4 + [1.2] * 3)
    assert _feed(machine, partial_frames) == []

    gap_start = partial_frames[-1].timestamp + _DT
    for step in range(_MISSING_GRACE_FRAMES + 1):
        assert machine.update(None, armed=True, now=gap_start + step * _DT) is None

    before_valid_burst = [1.2] * 6 + [0.2] * 4
    valid_burst = [1.2] * 3 + [0.2] * 4
    reentry_frames = _frames_for_velocities(
        before_valid_burst + valid_burst,
        start_time=gap_start + (_MISSING_GRACE_FRAMES + 1) * _DT,
    )
    prefix_frame_count = len(before_valid_burst) + 1
    # The machine is back in WAITING: a full presence-debounce is required
    # again before onset frames can even be counted.
    assert _feed(machine, reentry_frames[:prefix_frame_count]) == []
    assert len(_feed(machine, reentry_frames[prefix_frame_count:])) == 1


def test_dt_gap_guard_prevents_spurious_velocity_after_resumption() -> None:
    """A grace-held dropout of exactly ``missing_grace_frames`` does not
    itself force a reset, but leaves a large real-time gap since the last
    real sample. The resumed frame must not be treated as a velocity sample
    (which would read as a huge spurious spike from the stale baseline) --
    it should merely re-baseline. Normal motion from there still segments.
    """
    machine = SegmentationMachine(_MOTION)
    warm_up = _frames_for_velocities([0.0] * 4)
    assert _feed(machine, warm_up) == []

    gap_start = warm_up[-1].timestamp + _DT
    for step in range(_MISSING_GRACE_FRAMES):
        assert machine.update(None, armed=True, now=gap_start + step * _DT) is None

    resumed_time = gap_start + _MISSING_GRACE_FRAMES * _DT + (_MAX_FRAME_GAP + 0.1)
    resumed_frame = _translated_hand(50.0, resumed_time)
    assert machine.update(resumed_frame, armed=True, now=resumed_time) is None

    burst = _frames_for_velocities(
        [1.2] * 3 + [0.2] * 4,
        start_time=resumed_time + _DT,
        start_x=50.0,
    )
    segments = _feed(machine, burst)
    assert len(segments) == 1


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


def test_unusable_calibrated_floor_is_capped_so_gestures_still_register() -> None:
    """A brisk calibration must not make ordinary gestures unregisterable.

    Calibration derives the floor from the PEAK speed of practice reps, so a
    quick rep can leave it far above the speed of a deliberate gesture. The
    machine caps what it asks for, which also repairs already-saved profiles.
    """
    unusable = MotionSignature(velocity_floor=8.0, velocity_ceiling=20.0)
    # A steady gesture well under the calibrated floor but over the cap.
    velocities = [0.0] * 4 + [1.6] * 6 + [0.0] * 5
    frames = _frames_for_velocities(velocities)

    capped = SegmentationMachine(unusable)
    uncapped = SegmentationMachine(unusable, max_velocity_floor=float("inf"))

    assert len(_feed(capped, frames)) == 1
    assert _feed(uncapped, frames) == []


def test_floor_below_the_cap_is_left_alone() -> None:
    """The cap is a ceiling, never a floor: calibrated values below it stand."""
    gentle = MotionSignature(velocity_floor=0.3, velocity_ceiling=3.0)
    machine = SegmentationMachine(gentle)
    # Too slow for the gentle floor: it must still abstain rather than fire.
    frames = _frames_for_velocities([0.0] * 4 + [0.2] * 8 + [0.0] * 5)

    assert _feed(machine, frames) == []


def test_capped_floor_keeps_more_of_a_gesture_than_an_unusable_one() -> None:
    """The cap also stops the slow start/end being clipped off a take.

    Clipping is speed dependent, so takes of the same gesture stop resembling
    each other and matching degrades, the cap keeps the whole motion.
    """
    high = MotionSignature(velocity_floor=2.0, velocity_ceiling=8.0)
    ramp = [0.0] * 4 + [1.2, 1.8, 2.4, 2.8, 2.4, 1.8, 1.2] + [0.0] * 5
    frames = _frames_for_velocities(ramp)

    capped = _feed(SegmentationMachine(high), frames)
    clipped = _feed(
        SegmentationMachine(high, max_velocity_floor=float("inf")), frames
    )

    assert len(capped) == 1
    assert len(clipped) == 1
    assert len(capped[0].trajectory.frames) > len(clipped[0].trajectory.frames)
