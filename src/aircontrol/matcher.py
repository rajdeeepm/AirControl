from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aircontrol.dtw import dtw_distance
from aircontrol.store import Store
from aircontrol.trajectory import NormalizedTrajectory, Trajectory, normalize


@dataclass(frozen=True, slots=True)
class MatchResult:
    gesture_id: int | None
    top1: float
    top2: float
    scores: dict[int, float]


class TrajectoryMatcher(Protocol):
    def match(self, traj: Trajectory) -> MatchResult: ...

    def refresh(self) -> None: ...


class DtwMatcher:
    def __init__(
        self,
        store: Store,
        *,
        resample_length: int = 45,
        band: int = 8,
        velocity_weight: float = 0.3,
        max_exemplars: int = 15,
    ) -> None:
        self._store = store
        self._resample_length = resample_length
        self._band = band
        self._velocity_weight = velocity_weight
        self._max_exemplars = max_exemplars
        self._exemplars: dict[int, tuple[NormalizedTrajectory, ...]] = {}

    def refresh(self) -> None:
        exemplars: dict[int, tuple[NormalizedTrajectory, ...]] = {}
        for gesture in self._store.gestures.list():
            stored = self._store.exemplars.list(gesture.id)
            recent = stored[-self._max_exemplars :] if self._max_exemplars > 0 else []
            exemplars[gesture.id] = tuple(
                normalize(trajectory, resample_length=self._resample_length)
                for trajectory in recent
            )
        self._exemplars = exemplars

    def match(self, traj: Trajectory) -> MatchResult:
        if not self._exemplars:
            return MatchResult(None, 0.0, 0.0, {})

        normalized = normalize(traj, resample_length=self._resample_length)
        scores = {
            gesture_id: max(
                (
                    1.0
                    / (
                        1.0
                        + dtw_distance(
                            normalized.canonical,
                            exemplar.canonical,
                            band=self._band,
                            velocity_weight=self._velocity_weight,
                        )
                    )
                    for exemplar in gesture_exemplars
                ),
                default=0.0,
            )
            for gesture_id, gesture_exemplars in self._exemplars.items()
        }
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        gesture_id, top1 = ranked[0]
        top2 = ranked[1][1] if len(ranked) > 1 else 0.0
        return MatchResult(
            gesture_id=gesture_id,
            top1=top1,
            top2=top2,
            scores=scores,
        )
