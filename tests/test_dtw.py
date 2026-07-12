from __future__ import annotations

from time import perf_counter

import numpy as np
import pytest

from aircontrol.domain import Point3D
from aircontrol.dtw import dtw_distance, trajectory_dtw
from aircontrol.trajectory import LandmarkFrame, NormalizedTrajectory, Trajectory, normalize


_ANCHOR_INDICES = frozenset((0, 5, 9, 17))
_SAMPLE_COUNT = 15
_RESAMPLE_LENGTH = 45


def _swipe_frame(
    progress: float,
    timestamp: float,
    direction: tuple[float, float, float],
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

    dx, dy, dz = direction
    for index, point in enumerate(landmarks):
        if index in _ANCHOR_INDICES:
            continue
        landmarks[index] = Point3D(
            x=point.x + progress * dx,
            y=point.y + progress * dy,
            z=point.z + progress * dz,
        )

    return LandmarkFrame(
        landmarks=tuple(landmarks),
        handedness="Right",
        timestamp=timestamp,
    )


def _normalized_swipe(
    direction: tuple[float, float, float],
    *,
    timestamp_power: float = 1.0,
) -> NormalizedTrajectory:
    progress_values = np.linspace(0.0, 1.0, _SAMPLE_COUNT)
    frames = tuple(
        _swipe_frame(
            float(progress),
            float(progress**timestamp_power),
            direction,
        )
        for progress in progress_values
    )
    return normalize(
        Trajectory(frames=frames, handedness="Right"),
        resample_length=_RESAMPLE_LENGTH,
    )


def test_identity_distance_is_zero() -> None:
    swipe = _normalized_swipe((0.8, 0.0, 0.0))

    assert dtw_distance(swipe.canonical, swipe.canonical) == pytest.approx(
        0.0,
        abs=1e-6,
    )


def test_distance_is_symmetric() -> None:
    horizontal = _normalized_swipe((0.8, 0.0, 0.0))
    vertical = _normalized_swipe((0.0, 0.8, 0.0))

    forward = dtw_distance(horizontal.canonical, vertical.canonical)
    reverse = dtw_distance(vertical.canonical, horizontal.canonical)

    assert forward == pytest.approx(reverse, abs=1e-6)


def test_time_warped_copy_is_closer_than_different_motion() -> None:
    horizontal = _normalized_swipe((0.8, 0.0, 0.0))
    warped_horizontal = _normalized_swipe(
        (0.8, 0.0, 0.0),
        timestamp_power=3.0,
    )
    vertical = _normalized_swipe((0.0, 0.8, 0.0))

    warped_distance = trajectory_dtw(horizontal, warped_horizontal, band=44)
    different_distance = trajectory_dtw(horizontal, vertical, band=44)

    assert warped_distance * 2.0 <= different_distance


def test_sakoe_chiba_band_changes_only_warped_pair() -> None:
    horizontal = _normalized_swipe((0.8, 0.0, 0.0))
    warped_horizontal = _normalized_swipe(
        (0.8, 0.0, 0.0),
        timestamp_power=3.0,
    )

    narrow_warped = dtw_distance(
        horizontal.canonical,
        warped_horizontal.canonical,
        band=1,
    )
    wide_warped = dtw_distance(
        horizontal.canonical,
        warped_horizontal.canonical,
        band=44,
    )
    narrow_identical = dtw_distance(
        horizontal.canonical,
        horizontal.canonical,
        band=1,
    )
    wide_identical = dtw_distance(
        horizontal.canonical,
        horizontal.canonical,
        band=44,
    )

    assert not np.isclose(narrow_warped, wide_warped, atol=1e-6, rtol=1e-5)
    assert narrow_identical == pytest.approx(wide_identical, abs=1e-6)


def test_t45_distance_completes_under_50_ms() -> None:
    horizontal = _normalized_swipe((0.8, 0.0, 0.0))
    vertical = _normalized_swipe((0.0, 0.8, 0.0))

    dtw_distance(horizontal.canonical, vertical.canonical)
    started = perf_counter()
    dtw_distance(horizontal.canonical, vertical.canonical)
    elapsed = perf_counter() - started

    print(f"DTW single-call benchmark: {elapsed * 1_000:.3f} ms")
    assert elapsed < 0.050
