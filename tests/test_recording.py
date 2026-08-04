from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

import pytest

from aircontrol.config import AppConfig
from aircontrol.density import IncidentalDensity
from aircontrol.domain import Point3D
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
)
from aircontrol.recording import (
    MAX_TAKE_SECONDS,
    POSE_BUILTIN_REFUSAL,
    POSE_UNSTABLE_REFUSAL,
    RecordingConfig,
    RecordingSession,
    TakeEvent,
)
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory


_ANCHOR_INDICES = frozenset((0, 5, 9, 17))
_FRAME_COUNT = 18


def _motion_frame(
    x_offset: float,
    y_offset: float,
    *,
    frame_index: int,
    noise: float,
    phase: float,
    timestamp: float,
) -> LandmarkFrame:
    landmarks = [
        Point3D(
            x=(index % 5) * 0.08,
            y=(index // 5) * 0.12,
            z=(index % 3) * 0.02,
        )
        for index in range(21)
    ]
    landmarks[0] = Point3D(x=0.0, y=0.0, z=0.0)
    landmarks[5] = Point3D(x=1.0, y=0.0, z=0.0)
    landmarks[9] = Point3D(x=0.0, y=1.0, z=0.0)
    landmarks[17] = Point3D(x=0.0, y=0.8, z=0.0)

    for index, point in enumerate(landmarks):
        if index in _ANCHOR_INDICES:
            continue
        angle = phase + frame_index * 0.73 + index * 0.19
        landmarks[index] = Point3D(
            x=point.x + x_offset + noise * math.sin(angle),
            y=point.y + y_offset + noise * math.cos(angle),
            z=point.z,
        )

    return LandmarkFrame(
        landmarks=tuple(landmarks),
        handedness="Right",
        timestamp=timestamp,
    )


def _motion_trajectory(
    kind: str,
    *,
    amplitude: float = 1.0,
    timing_power: float = 1.0,
    noise: float = 0.0,
    phase: float = 0.0,
    frame_count: int = _FRAME_COUNT,
) -> Trajectory:
    frames = []
    for frame_index in range(frame_count):
        progress = frame_index / (frame_count - 1)
        if kind == "horizontal":
            x_offset, y_offset = amplitude * progress, 0.0
        elif kind == "vertical":
            x_offset, y_offset = 0.0, amplitude * progress
        elif kind == "circle":
            angle = 2.0 * math.pi * progress
            x_offset = amplitude * (math.cos(angle) - 1.0)
            y_offset = amplitude * math.sin(angle)
        else:
            raise ValueError(f"Unknown motion kind: {kind}")

        frames.append(
            _motion_frame(
                x_offset,
                y_offset,
                frame_index=frame_index,
                noise=noise,
                phase=phase,
                timestamp=progress**timing_power,
            )
        )
    return Trajectory(frames=tuple(frames), handedness="Right")


def _horizontal_cluster(count: int) -> tuple[Trajectory, ...]:
    midpoint = (count - 1) / 2
    return tuple(
        _motion_trajectory(
            "horizontal",
            amplitude=1.0 + (index - midpoint) * 0.012,
            timing_power=1.0 + (index - midpoint) * 0.015,
            noise=0.003 + index * 0.0003,
            phase=index * 0.41,
        )
        for index in range(count)
    )


def _translated_hand(x: float, timestamp: float) -> LandmarkFrame:
    """A single-hand frame translated by ``x``, with palm size held at 1.0.

    Mirrors tests/test_segmentation.py's helper so palm-normalised speed is
    exactly controllable: moving ``x`` by ``v * dt`` between two frames
    yields a measured speed of ``v``.
    """
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


def _frames_for_speeds(
    speeds: list[float],
    *,
    start_time: float = 0.0,
    dt: float = 0.1,
    start_x: float = 0.0,
) -> tuple[LandmarkFrame, ...]:
    """Build frames whose palm-normalised speed sequence is exactly ``speeds``.

    The first frame has no predecessor so its measured speed is always 0.0;
    ``speeds[i]`` is the speed of frame ``i + 1``.
    """
    x = start_x
    frames = [_translated_hand(x, start_time)]
    for index, speed in enumerate(speeds, start=1):
        x += speed * dt
        frames.append(_translated_hand(x, start_time + index * dt))
    return tuple(frames)


@pytest.fixture
def profile() -> CalibrationProfile:
    return CalibrationProfile(
        hand_size=0.22,
        volume=InteractionVolume(0.0, 1.0, 0.0, 1.0),
        motion=MotionSignature(velocity_floor=0.5, velocity_ceiling=5.0),
        lighting=LightingProfile(120.0, 0.01, True),
        incidental_features=(),
        created_at=1.0,
    )


@pytest.fixture
def config() -> AppConfig:
    return AppConfig.defaults()


def _capture_take(
    session: RecordingSession,
    trajectory: Trajectory,
    *,
    start_now: float,
) -> TakeEvent | None:
    """Drive one explicit begin_take -> feed(...) -> end_take cycle."""
    session.begin_take(start_now)
    for offset, frame in enumerate(trajectory.frames):
        result = session.feed(frame, start_now + offset * 1e-3)
        assert result is None
    return session.end_take(start_now + len(trajectory.frames) * 1e-3)


def _capture_all(
    session: RecordingSession,
    takes: Sequence[Trajectory],
) -> None:
    for index, take in enumerate(takes):
        event = _capture_take(session, take, start_now=float(index) * 100.0)
        assert event is not None
        assert event.take_index == index
        session.confirm_take()


def test_happy_path_saves_eight_exemplars_and_creates_stats(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    takes = _horizontal_cluster(8)

    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)
        _capture_all(session, takes)

        assert session.takes_confirmed == 8
        assert session.phase == "capture"

        outcome = session.finish()

        assert outcome.saved
        assert outcome.reason == "saved"
        assert outcome.gesture_id is not None
        assert outcome.consistency is not None
        assert outcome.consistency.outlier_indices == ()
        assert outcome.conflict_gesture_id is None
        assert session.phase == "saved"
        assert [gesture.name for gesture in store.gestures.list()] == ["Wave"]
        assert store.exemplars.count(outcome.gesture_id) == 8
        stats_row = store._connection.execute(
            "SELECT gesture_id FROM gesture_stats WHERE gesture_id = ?",
            (outcome.gesture_id,),
        ).fetchone()
        assert stats_row is not None


def test_inconsistent_takes_are_refused_with_outlier_indices(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    takes = list(_horizontal_cluster(7))
    takes.insert(4, _motion_trajectory("vertical", noise=0.004, phase=0.31))

    with Store(":memory:") as store:
        session = RecordingSession(
            "Mixed",
            store,
            profile,
            config,
            RecordingConfig(consistency_max_mean=0.05),
        )
        _capture_all(session, takes)

        outcome = session.finish()

        assert not outcome.saved
        assert outcome.reason == "inconsistent"
        assert outcome.gesture_id is None
        assert outcome.consistency is not None
        assert outcome.consistency.outlier_indices == (4,)
        assert outcome.conflict_gesture_id is None
        assert session.phase == "refused"
        assert store.gestures.list() == []


def test_takes_matching_seeded_gesture_are_refused_with_conflict_id(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    takes = _horizontal_cluster(8)

    with Store(":memory:") as store:
        existing = store.gestures.add("Horizontal")
        store.exemplars.add(existing.id, _motion_trajectory("horizontal"))
        session = RecordingSession("Duplicate", store, profile, config)
        _capture_all(session, takes)

        outcome = session.finish()

        assert not outcome.saved
        assert outcome.reason == "too similar"
        assert outcome.gesture_id is None
        assert outcome.consistency is not None
        assert outcome.conflict_gesture_id == existing.id
        assert session.phase == "refused"
        assert [gesture.id for gesture in store.gestures.list()] == [existing.id]
        assert store.exemplars.count(existing.id) == 1


def test_takes_near_incidental_rows_are_refused_as_desk_motion(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    first = _motion_trajectory("horizontal", amplitude=0.98, phase=0.1)
    second = _motion_trajectory("horizontal", amplitude=1.02, phase=0.2)
    density = IncidentalDensity.fit((first, second))
    fitted_profile = replace(profile, incidental_features=density.to_rows())
    takes = (first, second) * 4

    with Store(":memory:") as store:
        session = RecordingSession("Desk-like", store, fitted_profile, config)
        _capture_all(session, takes)

        outcome = session.finish()

        assert not outcome.saved
        assert outcome.reason == "resembles desk motion"
        assert outcome.gesture_id is None
        assert outcome.consistency is not None
        assert outcome.conflict_gesture_id is None
        assert session.phase == "refused"
        assert store.gestures.list() == []


def test_finish_before_minimum_takes_is_refused(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession("Too soon", store, profile, config)
        assert session.rec_config.min_takes == 3
        _capture_all(session, _horizontal_cluster(2))

        outcome = session.finish()

        assert not outcome.saved
        assert outcome.reason == "need more takes"
        assert outcome.gesture_id is None
        assert outcome.consistency is None
        assert outcome.conflict_gesture_id is None
        assert session.phase == "refused"
        assert store.gestures.list() == []


def test_finish_at_the_new_minimum_of_three_takes_succeeds(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    """The lowered minimum (8 -> 3) is plenty for a consistent, distinct
    gesture -- there is nothing else gating a save at exactly 3 takes."""
    with Store(":memory:") as store:
        session = RecordingSession("Just enough", store, profile, config)
        _capture_all(session, _horizontal_cluster(3))

        outcome = session.finish()

        assert outcome.saved
        assert outcome.reason == "saved"
        assert outcome.gesture_id is not None
        assert session.phase == "saved"
        assert store.exemplars.count(outcome.gesture_id) == 3


def test_discard_drops_pending_take_without_counting_or_consuming_next(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    discarded = _motion_trajectory("horizontal")
    confirmed = _motion_trajectory("vertical", frame_count=12)

    with Store(":memory:") as store:
        session = RecordingSession(
            "Kept",
            store,
            profile,
            config,
            RecordingConfig(min_takes=1),
        )

        event = _capture_take(session, discarded, start_now=0.0)
        assert event is not None
        assert event.take_index == 0
        assert session.takes_confirmed == 0

        session.discard_take()
        assert session.takes_confirmed == 0
        assert session.phase == "capture"
        assert session.capture_state == "idle"

        # Discarding drops any in-flight capture too: feeding without a new
        # begin_take does nothing.
        assert session.feed(confirmed.frames[0], now=1.0) is None

        confirmed_event = _capture_take(session, confirmed, start_now=2.0)
        assert confirmed_event is not None
        session.confirm_take()
        assert session.takes_confirmed == 1

        outcome = session.finish()
        assert outcome.saved
        assert outcome.gesture_id is not None
        saved = store.exemplars.list(outcome.gesture_id)
        assert len(saved) == 1
        # The stored exemplar matches whatever the trim produced for this
        # take, not necessarily the 12 raw input frames -- trimming is
        # covered precisely by the dedicated trim tests below.
        assert len(saved[0].frames) == confirmed_event.frame_count


def test_no_frames_are_captured_without_begin_take(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    """The regression this whole feature exists to fix: continuous
    incidental movement must never produce a take unless the user
    explicitly triggers a capture.
    """
    frames = _frames_for_speeds([1.2] * 30)

    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)
        for index, frame in enumerate(frames):
            event = session.feed(frame, now=float(index) * 0.1)
            assert event is None
        assert session.takes_confirmed == 0
        assert session.capture_state == "idle"


def test_begin_take_feed_end_take_produces_exactly_one_take(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    frames = _frames_for_speeds([0.0] * 3 + [1.5] * 10 + [0.0] * 3)

    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)

        session.begin_take(now=0.0)
        assert session.capture_state == "capturing"

        for index, frame in enumerate(frames):
            assert session.feed(frame, now=index * 0.01) is None

        take = session.end_take(now=1.0)
        assert take is not None
        assert take.take_index == 0
        assert session.capture_state == "pending_take"

        # Feeding after end_take does nothing until the next begin_take.
        assert session.feed(frames[0], now=2.0) is None
        assert session.feed(None, now=3.0) is None
        assert session.capture_state == "pending_take"


def test_end_take_without_begin_take_is_a_harmless_no_op(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)

        assert session.end_take(now=5.0) is None
        assert session.capture_state == "idle"
        assert session.takes_confirmed == 0

        # Still perfectly usable afterwards.
        session.begin_take(now=6.0)
        assert session.capture_state == "capturing"


def test_capture_with_no_motion_is_refused_and_does_not_consume_a_take(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    frames = _frames_for_speeds([0.0] * 10)

    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)

        session.begin_take(now=0.0)
        for index, frame in enumerate(frames):
            session.feed(frame, now=index * 0.01)

        take = session.end_take(now=5.0)

        assert take is None
        assert session.last_take_refused == "no motion"
        assert session.capture_state == "idle"
        assert session.takes_confirmed == 0
        assert session.phase == "capture"

        # The user can immediately try again.
        session.begin_take(now=6.0)
        assert session.capture_state == "capturing"
        assert session.last_take_refused is None


def test_capture_trims_to_the_motion_span(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    speeds = [0.0] * 5 + [2.0] * 8 + [0.0] * 5
    frames = _frames_for_speeds(speeds)

    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)

        session.begin_take(now=0.0)
        for index, frame in enumerate(frames):
            session.feed(frame, now=index * 0.01)
        take = session.end_take(now=5.0)

        assert take is not None
        assert 0 < take.frame_count < len(frames)

        session.confirm_take()
        trimmed = session._confirmed[0]
        assert len(trimmed.frames) == take.frame_count


def test_trim_is_speed_independent_for_slow_and_fast_gestures(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    """A fixed absolute velocity floor (like the profile's 0.5) would reject
    the slow gesture below while accepting the fast one. The trim uses each
    take's own peak speed instead, so both produce a non-empty take.
    """
    slow_speeds = [0.0] * 5 + [0.4] * 8 + [0.0] * 5
    fast_speeds = [0.0] * 5 + [4.0] * 8 + [0.0] * 5
    assert max(slow_speeds) < profile.motion.velocity_floor

    with Store(":memory:") as store:
        session = RecordingSession(
            "Wave",
            store,
            profile,
            config,
            RecordingConfig(max_takes=2),
        )

        for speeds in (slow_speeds, fast_speeds):
            frames = _frames_for_speeds(speeds)
            session.begin_take(now=0.0)
            for index, frame in enumerate(frames):
                session.feed(frame, now=index * 0.01)
            take = session.end_take(now=5.0)

            assert take is not None
            assert take.frame_count >= 6
            session.confirm_take()

        assert session.takes_confirmed == 2


def test_feed_auto_finalizes_once_elapsed_reaches_the_safety_cap(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    frames = _frames_for_speeds([0.0] * 3 + [1.5] * 10 + [0.0] * 3)

    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)

        session.begin_take(now=0.0)
        for frame in frames:
            # Frame timestamps drive trimming only; "now" here (well under
            # the safety cap) drives the capture-window clock.
            assert session.feed(frame, now=0.01) is None

        # The user never calls end_take. Once elapsed time reaches the
        # safety cap, the take auto-finalises via the same trim path.
        take = session.feed(None, now=MAX_TAKE_SECONDS + 0.5)

        assert take is not None
        assert session.capture_state == "pending_take"


# --- Static hand-pose recording ---------------------------------------------

_POSE_FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


def _fist_landmarks(*, jitter: float = 0.0, seed: float = 0.0) -> tuple[Point3D, ...]:
    """A canonical curled-fist hand -- collides with the built-in FIST pose."""
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for _name, (mcp, pip, dip, tip, x, y) in _POSE_FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.07)
        points[dip] = Point3D(x + 0.035, y - 0.01)
        points[tip] = Point3D(x + 0.018, y + 0.045)
    return _jittered(points, jitter=jitter, seed=seed)


def _custom_pose_landmarks(*, jitter: float = 0.0, seed: float = 0.0) -> tuple[Point3D, ...]:
    """A relaxed, uniformly half-curled hand: distinct from every built-in pose."""
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for _name, (mcp, pip, dip, tip, x, y) in _POSE_FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.08)
        points[dip] = Point3D(x + 0.05, y - 0.13)
        points[tip] = Point3D(x + 0.02, y - 0.20)
    return _jittered(points, jitter=jitter, seed=seed)


def _open_palm_landmarks(*, jitter: float = 0.0, seed: float = 0.0) -> tuple[Point3D, ...]:
    """A flat open palm, fingers together -- collides with built-in OPEN_PALM."""
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for _name, (mcp, pip, dip, tip, x, y) in _POSE_FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.13)
        points[dip] = Point3D(x, y - 0.24)
        points[tip] = Point3D(x, y - 0.34)
    return _jittered(points, jitter=jitter, seed=seed)


def _spock_landmarks(*, jitter: float = 0.0, seed: float = 0.0) -> tuple[Point3D, ...]:
    """All four fingers extended but parted between middle and ring (a
    Spock/Vulcan salute split) -- must stay distinct from built-in OPEN_PALM.
    """
    spread_x = {
        "index": 0.43,
        "middle": 0.49,
        "ring": 0.70,
        "pinky": 0.76,
    }
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for name, (mcp, pip, dip, tip, _x, y) in _POSE_FINGER_LAYOUT.items():
        x = spread_x[name]
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.13)
        points[dip] = Point3D(x, y - 0.24)
        points[tip] = Point3D(x, y - 0.34)
    return _jittered(points, jitter=jitter, seed=seed)


def _jittered(
    points: list[Point3D],
    *,
    jitter: float,
    seed: float,
) -> tuple[Point3D, ...]:
    if jitter == 0.0:
        return tuple(points)
    return tuple(
        Point3D(
            point.x + jitter * math.sin(seed + index * 1.7),
            point.y + jitter * math.cos(seed + index * 2.3),
            point.z,
        )
        for index, point in enumerate(points)
    )


def _pose_take_frames(
    landmarks_fn,
    *,
    count: int,
    dt: float,
    jitter: float = 0.0,
    start_time: float = 0.0,
    handedness: str = "Left",
) -> tuple[LandmarkFrame, ...]:
    return tuple(
        LandmarkFrame(
            landmarks=landmarks_fn(jitter=jitter, seed=index * 0.9),
            handedness=handedness,
            timestamp=start_time + index * dt,
        )
        for index in range(count)
    )


def _capture_pose_take(
    session: RecordingSession,
    frames: Sequence[LandmarkFrame],
    *,
    start_now: float,
) -> TakeEvent | None:
    session.begin_take(start_now)
    for offset, frame in enumerate(frames):
        session.feed(frame, start_now + offset * 1e-3)
    return session.end_take(start_now + len(frames) * 1e-3)


def _steady_pose_frames(start_time: float = 0.0) -> tuple[LandmarkFrame, ...]:
    return _pose_take_frames(
        _custom_pose_landmarks,
        count=12,
        dt=0.05,
        jitter=0.0,
        start_time=start_time,
    )


def test_steady_pose_hold_yields_a_pending_take(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession(
            "Peace sign", store, profile, config, kind="pose"
        )
        assert session.kind == "pose"

        take = _capture_pose_take(session, _steady_pose_frames(), start_now=0.0)

        assert take is not None
        assert session.capture_state == "pending_take"
        assert session.last_take_refused is None


def test_jittery_pose_hold_is_refused_without_consuming_a_take(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession(
            "Peace sign", store, profile, config, kind="pose"
        )
        jittery = _pose_take_frames(
            _custom_pose_landmarks,
            count=12,
            dt=0.05,
            jitter=0.08,
        )

        take = _capture_pose_take(session, jittery, start_now=0.0)

        assert take is None
        assert session.capture_state == "idle"
        assert session.last_take_refused == POSE_UNSTABLE_REFUSAL
        assert session.takes_confirmed == 0


def test_too_short_pose_hold_is_refused_as_unstable(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession(
            "Peace sign", store, profile, config, kind="pose"
        )
        # Total duration well under POSE_MIN_HOLD_SECONDS.
        brief = _pose_take_frames(_custom_pose_landmarks, count=4, dt=0.02)

        take = _capture_pose_take(session, brief, start_now=0.0)

        assert take is None
        assert session.last_take_refused == POSE_UNSTABLE_REFUSAL


def test_pose_matching_a_built_in_shape_is_refused_without_consuming_a_take(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession(
            "My fist", store, profile, config, kind="pose"
        )
        fist_frames = _pose_take_frames(
            _fist_landmarks,
            count=12,
            dt=0.05,
            jitter=0.0,
        )

        take = _capture_pose_take(session, fist_frames, start_now=0.0)

        assert take is None
        assert session.capture_state == "idle"
        assert session.last_take_refused == POSE_BUILTIN_REFUSAL
        assert session.takes_confirmed == 0


def test_flat_open_palm_hold_is_refused_as_built_in(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    """A genuine flat open palm still collides with OPEN_PALM -- that refusal
    is correct, since it IS the arming pose."""
    with Store(":memory:") as store:
        session = RecordingSession(
            "My open hand", store, profile, config, kind="pose"
        )
        open_palm_frames = _pose_take_frames(
            _open_palm_landmarks,
            count=12,
            dt=0.05,
            jitter=0.0,
        )

        take = _capture_pose_take(session, open_palm_frames, start_now=0.0)

        assert take is None
        assert session.capture_state == "idle"
        assert session.last_take_refused == POSE_BUILTIN_REFUSAL
        assert session.takes_confirmed == 0


def test_spock_pose_hold_is_not_refused_as_built_in(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    """A held Spock-like split-finger pose looks nothing like the built-in
    OPEN_PALM bucket now that OPEN_PALM requires fingers together, so it
    should be recordable as a custom pose."""
    with Store(":memory:") as store:
        session = RecordingSession(
            "Spock", store, profile, config, kind="pose"
        )
        spock_frames = _pose_take_frames(
            _spock_landmarks,
            count=12,
            dt=0.05,
            jitter=0.0,
        )

        take = _capture_pose_take(session, spock_frames, start_now=0.0)

        assert take is not None
        assert session.capture_state == "pending_take"
        assert session.last_take_refused is None


def test_capture_steady_reports_live_stability_while_capturing(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession(
            "Peace sign", store, profile, config, kind="pose"
        )
        session.begin_take(now=0.0)
        assert session.capture_steady is None

        steady_frames = _pose_take_frames(
            _custom_pose_landmarks, count=8, dt=0.05, jitter=0.0
        )
        for offset, frame in enumerate(steady_frames):
            session.feed(frame, now=offset * 1e-3)
        assert session.capture_steady is True

        jittery_frames = _pose_take_frames(
            _custom_pose_landmarks,
            count=8,
            dt=0.05,
            jitter=0.09,
            start_time=steady_frames[-1].timestamp + 0.05,
        )
        for offset, frame in enumerate(jittery_frames):
            session.feed(frame, now=offset * 1e-3)
        assert session.capture_steady is False


def test_motion_capture_steady_is_always_none(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession("Wave", store, profile, config)
        assert session.kind == "motion"

        _capture_take(
            session, _horizontal_cluster(1)[0], start_now=0.0
        )

        assert session.capture_steady is None


def test_pose_finish_saves_gesture_with_pose_kind_and_exemplars(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    with Store(":memory:") as store:
        session = RecordingSession(
            "Peace sign",
            store,
            profile,
            config,
            RecordingConfig(min_takes=3, max_takes=5),
            kind="pose",
        )
        for index in range(3):
            take = _capture_pose_take(
                session, _steady_pose_frames(start_time=index * 100.0), start_now=index * 100.0
            )
            assert take is not None
            session.confirm_take()

        outcome = session.finish()

        assert outcome.saved
        gesture = store.gestures.get(outcome.gesture_id)
        assert gesture is not None
        assert gesture.kind == "pose"
        assert store.exemplars.count(outcome.gesture_id) == 3


def test_pose_recording_confusability_check_uses_pose_matcher_only(
    profile: CalibrationProfile,
    config: AppConfig,
) -> None:
    """A pose recording's confusability check must not compare against motion.

    Regression guard: RecordingSession._most_confusable_match used to call
    the matcher's motion-only match() unconditionally, which would either
    ignore an existing, genuinely confusable pose (a false negative on
    confusability) or mis-score against unrelated motion gestures.
    """
    with Store(":memory:") as store:
        motion_session = RecordingSession("Wave", store, profile, config)
        _capture_all(motion_session, _horizontal_cluster(8))
        motion_outcome = motion_session.finish()
        assert motion_outcome.saved

        pose_session = RecordingSession(
            "Peace sign",
            store,
            profile,
            config,
            RecordingConfig(min_takes=3, max_takes=5),
            kind="pose",
        )
        for index in range(3):
            take = _capture_pose_take(
                pose_session,
                _steady_pose_frames(start_time=index * 100.0),
                start_now=index * 100.0,
            )
            assert take is not None
            pose_session.confirm_take()

        outcome = pose_session.finish()

        assert outcome.saved
        assert outcome.conflict_gesture_id is None
