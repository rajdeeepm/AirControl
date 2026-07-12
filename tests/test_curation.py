from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from aircontrol.curation import (
    ConsistencyReport,
    consistency_report,
    prune_exemplars,
)
from aircontrol.domain import Point3D
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
) -> Trajectory:
    frames = []
    for frame_index in range(_FRAME_COUNT):
        progress = frame_index / (_FRAME_COUNT - 1)
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


def test_consistency_report_detects_planted_outlier() -> None:
    takes = list(_horizontal_cluster(7))
    takes.insert(4, _motion_trajectory("vertical", noise=0.004, phase=0.31))

    report = consistency_report(takes)

    assert report.mean_distance > 0.0
    assert report.outlier_indices == (4,)


def test_consistency_report_finds_no_outliers_in_tight_cluster() -> None:
    report = consistency_report(_horizontal_cluster(8))

    assert report.mean_distance > 0.0
    assert report.outlier_indices == ()


def test_consistency_report_is_frozen_and_slotted() -> None:
    report = ConsistencyReport(mean_distance=0.25, outlier_indices=(2,))

    assert hasattr(ConsistencyReport, "__slots__")
    assert not hasattr(report, "__dict__")
    with pytest.raises(FrozenInstanceError):
        report.mean_distance = 0.5


def test_pruning_keeps_distant_takes_and_newest_take() -> None:
    cluster = _horizontal_cluster(10)
    takes = (
        *cluster[:9],
        _motion_trajectory("vertical", noise=0.004, phase=0.19),
        _motion_trajectory("circle", noise=0.005, phase=0.73),
        cluster[-1],
    )

    kept = prune_exemplars(takes, max_n=5)

    assert len(kept) == 5
    assert {9, 10, 11} <= set(kept)


@pytest.mark.parametrize(
    ("take_count", "max_n"),
    ((0, 4), (1, 4), (4, 4), (4, 9), (8, 3)),
)
def test_pruning_keep_count_is_minimum_of_cap_and_input_size(
    take_count: int,
    max_n: int,
) -> None:
    kept = prune_exemplars(_horizontal_cluster(take_count), max_n=max_n)

    assert len(kept) == min(max_n, take_count)


def test_pruning_is_deterministic_and_breaks_ties_by_lowest_index() -> None:
    trajectory = _motion_trajectory("horizontal")
    takes = (trajectory,) * 6

    results = tuple(prune_exemplars(takes, max_n=3) for _ in range(4))

    assert results == ((0, 1, 5),) * 4
