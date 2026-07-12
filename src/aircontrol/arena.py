from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from aircontrol.curation import prune_exemplars
from aircontrol.gate import GateThresholds
from aircontrol.matcher import MatchResult
from aircontrol.segmentation import CandidateSegment
from aircontrol.store import Store


@dataclass(frozen=True, slots=True)
class ArenaPrompt:
    gesture_id: int | None
    top1: float
    runner_up_id: int | None
    top2: float


class ArenaSession:
    def __init__(
        self,
        store: Store,
        *,
        max_exemplars: int = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._max_exemplars = max_exemplars
        self._clock = clock
        self._pending: tuple[CandidateSegment, MatchResult] | None = None
        self._stress_deadline: float | None = None
        self.stress_fires = 0

    def observe(
        self,
        segment: CandidateSegment,
        result: MatchResult,
        fired: bool,
    ) -> ArenaPrompt:
        self._pending = (segment, result)
        if fired and self.stress_active:
            self.stress_fires += 1

        ranked = sorted(
            result.scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
        runner_up_id = ranked[1][0] if len(ranked) >= 2 else None
        return ArenaPrompt(
            gesture_id=result.gesture_id,
            top1=result.top1,
            runner_up_id=runner_up_id,
            top2=result.top2,
        )

    def confirm(self) -> None:
        pending = self._take_pending()
        if pending is None:
            return

        segment, result = pending
        gesture_id = result.gesture_id
        if gesture_id is None:
            return

        self._store.exemplars.add(gesture_id, segment.trajectory)
        rows = self._store.exemplars.list_with_ids(gesture_id)
        if len(rows) > self._max_exemplars:
            keep_indices = set(
                prune_exemplars(
                    [trajectory for _, trajectory in rows],
                    self._max_exemplars,
                )
            )
            for index, (exemplar_id, _) in enumerate(rows):
                if index not in keep_indices:
                    self._store.exemplars.delete(exemplar_id)

        self._store.gesture_stats.record_confirm(gesture_id)

    def reject(self) -> None:
        pending = self._take_pending()
        if pending is None:
            return

        _, result = pending
        if result.gesture_id is not None:
            self._store.gesture_stats.record_reject(result.gesture_id)

    def start_stress(self, duration_seconds: float = 60.0) -> None:
        self._stress_deadline = self._clock() + duration_seconds
        self.stress_fires = 0

    @property
    def stress_active(self) -> bool:
        return (
            self._stress_deadline is not None
            and self._clock() < self._stress_deadline
        )

    def summary(
        self,
    ) -> dict[int | str, dict[str, int | float] | int]:
        summary: dict[int | str, dict[str, int | float] | int] = {}
        for gesture in self._store.gestures.list():
            stats = self._store.gesture_stats.get(gesture.id)
            summary[gesture.id] = {
                "confirms": stats.confirms,
                "rejects": stats.rejects,
                "threshold_offset": stats.threshold_offset,
            }
        summary["stress_fires"] = self.stress_fires
        return summary

    def _take_pending(self) -> tuple[CandidateSegment, MatchResult] | None:
        pending = self._pending
        self._pending = None
        return pending


def effective_t1(base: GateThresholds, offset: float) -> float:
    return base.t1_top1 + offset
