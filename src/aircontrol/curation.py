from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from aircontrol.dtw import trajectory_dtw
from aircontrol.trajectory import Trajectory, normalize


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    mean_distance: float
    outlier_indices: tuple[int, ...]


def _distance_matrix(
    trajectories: Sequence[Trajectory],
    *,
    resample_length: int = 45,
) -> list[list[float]]:
    normalized = tuple(
        normalize(trajectory, resample_length=resample_length)
        for trajectory in trajectories
    )
    matrix = [[0.0] * len(normalized) for _ in normalized]

    for first_index in range(len(normalized)):
        for second_index in range(first_index + 1, len(normalized)):
            distance = trajectory_dtw(
                normalized[first_index],
                normalized[second_index],
            )
            matrix[first_index][second_index] = distance
            matrix[second_index][first_index] = distance

    return matrix


def consistency_report(
    takes: Sequence[Trajectory],
    *,
    resample_length: int = 45,
) -> ConsistencyReport:
    matrix = _distance_matrix(takes, resample_length=resample_length)
    take_count = len(matrix)
    pair_count = take_count * (take_count - 1) // 2
    mean_distance = (
        sum(
            matrix[first_index][second_index]
            for first_index in range(take_count)
            for second_index in range(first_index + 1, take_count)
        )
        / pair_count
        if pair_count
        else 0.0
    )

    if take_count < 3:
        return ConsistencyReport(mean_distance, ())

    outlier_threshold = 1.5 * mean_distance
    outlier_indices = tuple(
        index
        for index, distances in enumerate(matrix)
        if sum(distances) / (take_count - 1) > outlier_threshold
    )
    return ConsistencyReport(mean_distance, outlier_indices)


def prune_exemplars(
    trajs: Sequence[Trajectory],
    max_n: int,
) -> tuple[int, ...]:
    trajectory_count = len(trajs)
    keep_count = min(max_n, trajectory_count)
    if keep_count <= 0:
        return ()
    if keep_count == trajectory_count:
        return tuple(range(trajectory_count))

    matrix = _distance_matrix(trajs)
    selected = {trajectory_count - 1}

    while len(selected) < keep_count:
        next_index = max(
            (index for index in range(trajectory_count) if index not in selected),
            key=lambda index: (
                min(matrix[index][selected_index] for selected_index in selected),
                -index,
            ),
        )
        selected.add(next_index)

    return tuple(sorted(selected))
