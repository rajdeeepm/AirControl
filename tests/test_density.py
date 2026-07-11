from __future__ import annotations

import math

import pytest

from aircontrol.density import FEATURE_DIM, IncidentalDensity, featurize
from aircontrol.domain import Point3D
from aircontrol.trajectory import LandmarkFrame, Trajectory


_DT = 0.1
_FRAME_COUNT = 16
_ANCHOR_LANDMARKS = frozenset((0, 5, 9, 17))


def _hand_with_center_offset(offset: float, timestamp: float) -> LandmarkFrame:
    """Move the hand shape relative to a fixed wrist and palm basis."""
    landmarks = [
        Point3D(x=index * 0.01, y=index * 0.02, z=0.0)
        for index in range(21)
    ]
    landmarks[0] = Point3D(x=0.0, y=0.0, z=0.0)
    landmarks[9] = Point3D(x=0.0, y=1.0, z=0.0)
    landmarks[5] = Point3D(x=0.5, y=0.3, z=0.0)
    landmarks[17] = Point3D(x=-0.5, y=0.3, z=0.0)

    for index, point in enumerate(landmarks):
        if index not in _ANCHOR_LANDMARKS:
            landmarks[index] = Point3D(
                x=point.x + offset,
                y=point.y,
                z=point.z,
            )

    return LandmarkFrame(
        landmarks=tuple(landmarks),
        handedness="Right",
        timestamp=timestamp,
    )


def _trajectory(offsets: tuple[float, ...]) -> Trajectory:
    frames = tuple(
        _hand_with_center_offset(offset, index * _DT)
        for index, offset in enumerate(offsets)
    )
    return Trajectory(frames=frames, handedness="Right")


def _slow_jitter(amplitude: float, phase: float = 0.0) -> Trajectory:
    offsets = tuple(
        amplitude * math.sin(index * 1.3 + phase)
        for index in range(_FRAME_COUNT)
    )
    return _trajectory(offsets)


def _fast_swipe(amplitude: float = 1.5) -> Trajectory:
    offsets = tuple(
        amplitude * index / (_FRAME_COUNT - 1)
        for index in range(_FRAME_COUNT)
    )
    return _trajectory(offsets)


def _slow_cluster() -> tuple[Trajectory, ...]:
    return tuple(
        _slow_jitter(amplitude, phase=index * 0.04)
        for index, amplitude in enumerate((0.015, 0.018, 0.021, 0.024, 0.027, 0.030))
    )


def test_featurize_returns_12_deterministic_floats() -> None:
    trajectory = _slow_jitter(0.02, phase=0.1)

    first = featurize(trajectory)
    second = featurize(trajectory)

    assert FEATURE_DIM == 12
    assert len(first) == FEATURE_DIM
    assert all(isinstance(value, float) for value in first)
    assert first == second


def test_density_separates_similar_jitter_from_fast_large_swipe() -> None:
    density = IncidentalDensity.fit(_slow_cluster())

    low = density.distance(_slow_jitter(0.022, phase=0.09))
    high = density.distance(_fast_swipe())

    assert low > 0.0
    assert high / low > 3.0


def test_rows_round_trip_preserves_probe_distances_exactly() -> None:
    density = IncidentalDensity.fit(_slow_cluster(), k=3)
    restored = IncidentalDensity.from_rows(density.to_rows(), k=3)
    probes = (
        _slow_jitter(0.019, phase=0.07),
        _slow_jitter(0.028, phase=0.16),
        _fast_swipe(amplitude=1.2),
    )

    for probe in probes:
        assert restored.distance(probe) == density.distance(probe)


@pytest.mark.parametrize("trajectory_count", (0, 1))
def test_fit_requires_at_least_two_trajectories(trajectory_count: int) -> None:
    trajectories = tuple(
        _slow_jitter(0.02 + index * 0.001)
        for index in range(trajectory_count)
    )

    with pytest.raises(ValueError):
        IncidentalDensity.fit(trajectories)
