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
from aircontrol.recording import RecordingConfig, RecordingSession, TakeEvent
from aircontrol.segmentation import CandidateSegment
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


class _ScriptedSegmentation:
    def __init__(self, takes: Sequence[Trajectory]) -> None:
        self._takes = list(takes)
        self.calls = 0

    def update(
        self,
        frame: LandmarkFrame | None,
        armed: bool,
        now: float,
    ) -> CandidateSegment | None:
        self.calls += 1
        assert armed
        if not self._takes:
            return None
        trajectory = self._takes.pop(0)
        return CandidateSegment(
            trajectory=trajectory,
            t_onset=now,
            t_offset=now + 1.0,
        )


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


def _capture_all(
    session: RecordingSession,
    takes: Sequence[Trajectory],
) -> _ScriptedSegmentation:
    segmentation = _ScriptedSegmentation(takes)
    session._segmentation = segmentation

    for index, take in enumerate(takes):
        event = session.feed(take.frames[0], now=float(index))
        assert event == TakeEvent(take_index=index, frame_count=len(take.frames))
        session.confirm_take()

    return segmentation


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
        _capture_all(session, _horizontal_cluster(7))

        outcome = session.finish()

        assert not outcome.saved
        assert outcome.reason == "need more takes"
        assert outcome.gesture_id is None
        assert outcome.consistency is None
        assert outcome.conflict_gesture_id is None
        assert session.phase == "refused"
        assert store.gestures.list() == []


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
        segmentation = _ScriptedSegmentation((discarded, confirmed))
        session._segmentation = segmentation

        event = session.feed(discarded.frames[0], now=0.0)
        assert event == TakeEvent(take_index=0, frame_count=18)
        assert session.takes_confirmed == 0

        assert session.feed(confirmed.frames[0], now=1.0) is None
        assert segmentation.calls == 1

        session.discard_take()
        assert session.takes_confirmed == 0
        assert session.phase == "capture"

        event = session.feed(confirmed.frames[0], now=2.0)
        assert event == TakeEvent(take_index=0, frame_count=12)
        session.confirm_take()
        assert session.takes_confirmed == 1

        outcome = session.finish()
        assert outcome.saved
        assert outcome.gesture_id is not None
        saved = store.exemplars.list(outcome.gesture_id)
        assert len(saved) == 1
        assert len(saved[0].frames) == 12
