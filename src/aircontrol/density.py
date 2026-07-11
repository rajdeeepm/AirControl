"""Incidental-motion features and k-nearest-neighbor density distances.

Persistence rows all contain ``FEATURE_DIM`` floats. Row 0 is the fitted
per-dimension mean, row 1 is the fitted standard deviation after applying the
``1e-9`` floor, and rows 2 onward are the raw, unstandardized training feature
vectors. The neighbor count is not encoded; callers supply it to
``from_rows(rows, k=...)``, where it is capped against the restored row count.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from aircontrol.trajectory import Trajectory, normalize


FEATURE_DIM = 12

_HISTOGRAM_BINS = 8
_MAX_HISTOGRAM_SPEED = 2.0
_STD_FLOOR = 1e-9


def featurize(traj: Trajectory) -> tuple[float, ...]:
    normalized = normalize(traj)

    speeds = np.linalg.norm(normalized.velocity, axis=2).sum(axis=1)
    histogram, _ = np.histogram(
        np.clip(speeds, 0.0, _MAX_HISTOGRAM_SPEED),
        bins=np.linspace(0.0, _MAX_HISTOGRAM_SPEED, _HISTOGRAM_BINS + 1),
    )

    centroids = normalized.canonical.mean(axis=1)
    centroid_steps = np.diff(centroids, axis=0)
    path_length = np.linalg.norm(centroid_steps, axis=1).sum()

    duration = traj.frames[-1].timestamp - traj.frames[0].timestamp
    values = (
        *histogram,
        path_length,
        duration,
        speeds.mean(),
        speeds.max(),
    )
    return tuple(float(value) for value in values)


class IncidentalDensity:
    __slots__ = ("_features", "_mean", "_std", "_k")

    def __init__(
        self,
        features: np.ndarray,
        mean: np.ndarray,
        std: np.ndarray,
        k: int,
    ) -> None:
        self._features = features
        self._mean = mean
        self._std = std
        self._k = k

    @classmethod
    def fit(
        cls,
        trajectories: Sequence[Trajectory],
        k: int = 5,
    ) -> IncidentalDensity:
        if len(trajectories) < 2:
            raise ValueError("Incidental density requires at least two trajectories")

        features = np.asarray(
            [featurize(trajectory) for trajectory in trajectories],
            dtype=np.float64,
        )
        mean = features.mean(axis=0)
        std = np.maximum(features.std(axis=0), _STD_FLOOR)
        return cls(features, mean, std, cls._effective_k(k, len(features)))

    def distance(self, traj: Trajectory) -> float:
        probe = np.asarray(featurize(traj), dtype=np.float64)
        standardized_features = (self._features - self._mean) / self._std
        standardized_probe = (probe - self._mean) / self._std
        distances = np.linalg.norm(
            standardized_features - standardized_probe,
            axis=1,
        )
        nearest = np.sort(distances)[: self._k]
        return float(nearest.mean())

    def to_rows(self) -> tuple[tuple[float, ...], ...]:
        arrays = (self._mean, self._std, *self._features)
        return tuple(
            tuple(float(value) for value in row)
            for row in arrays
        )

    @classmethod
    def from_rows(
        cls,
        rows: Sequence[Sequence[float]],
        k: int = 5,
    ) -> IncidentalDensity:
        stored_rows = tuple(tuple(float(value) for value in row) for row in rows)
        if len(stored_rows) < 4:
            raise ValueError("Density rows require mean, std, and two training rows")
        if any(len(row) != FEATURE_DIM for row in stored_rows):
            raise ValueError(f"Density rows must each contain {FEATURE_DIM} floats")

        values = np.asarray(stored_rows, dtype=np.float64)
        mean = values[0].copy()
        std = values[1].copy()
        if np.any(std <= 0.0):
            raise ValueError("Density standard deviations must be positive")
        std = np.maximum(std, _STD_FLOOR)
        features = values[2:].copy()
        return cls(features, mean, std, cls._effective_k(k, len(features)))

    @staticmethod
    def _effective_k(k: int, row_count: int) -> int:
        return max(1, min(int(k), row_count - 1))
