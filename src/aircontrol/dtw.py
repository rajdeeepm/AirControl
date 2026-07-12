from __future__ import annotations

import numpy as np

from aircontrol.trajectory import NormalizedTrajectory


def _finite_difference_velocity(sequence: np.ndarray) -> np.ndarray:
    velocity = np.zeros_like(sequence)
    velocity[1:] = sequence[1:] - sequence[:-1]
    return velocity


def dtw_distance(
    a: np.ndarray,
    b: np.ndarray,
    *,
    band: int = 8,
    velocity_weight: float = 0.3,
) -> float:
    velocity_a = _finite_difference_velocity(a)
    velocity_b = _finite_difference_velocity(b)

    position_cost = np.linalg.norm(
        a[:, None, :, :] - b[None, :, :, :],
        axis=-1,
    ).mean(axis=-1)
    velocity_cost = np.linalg.norm(
        velocity_a[:, None, :, :] - velocity_b[None, :, :, :],
        axis=-1,
    ).mean(axis=-1)
    frame_cost = position_cost + velocity_weight * velocity_cost

    a_length, b_length = frame_cost.shape
    cumulative = np.full((a_length + 1, b_length + 1), np.inf, dtype=np.float64)
    path_length = np.zeros((a_length + 1, b_length + 1), dtype=np.int32)
    cumulative[0, 0] = 0.0

    for a_index in range(1, a_length + 1):
        start = max(1, a_index - band)
        stop = min(b_length, a_index + band) + 1
        for b_index in range(start, stop):
            predecessor_cost, predecessor_length = min(
                (
                    (cumulative[a_index - 1, b_index - 1], path_length[a_index - 1, b_index - 1]),
                    (cumulative[a_index - 1, b_index], path_length[a_index - 1, b_index]),
                    (cumulative[a_index, b_index - 1], path_length[a_index, b_index - 1]),
                )
            )
            if np.isinf(predecessor_cost):
                continue
            cumulative[a_index, b_index] = (
                predecessor_cost + frame_cost[a_index - 1, b_index - 1]
            )
            path_length[a_index, b_index] = predecessor_length + 1

    steps = path_length[a_length, b_length]
    if steps == 0:
        return float("inf")
    return float(cumulative[a_length, b_length] / steps)


def trajectory_dtw(
    a: NormalizedTrajectory,
    b: NormalizedTrajectory,
    *,
    band: int = 8,
    velocity_weight: float = 0.3,
) -> float:
    return dtw_distance(
        a.canonical,
        b.canonical,
        band=band,
        velocity_weight=velocity_weight,
    )
