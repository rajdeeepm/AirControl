from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GateDecision:
    fire: bool
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class GateThresholds:
    t1_top1: float = 0.0
    t2_margin: float = 0.0
    t3_incidental: float = 0.0


class ConfidenceGate:
    def __init__(self, thresholds: GateThresholds) -> None:
        self._thresholds = thresholds

    @property
    def thresholds(self) -> GateThresholds:
        return self._thresholds

    def evaluate(
        self,
        top1: float,
        top2: float,
        incidental_distance: float,
    ) -> GateDecision:
        passes_t1 = top1 >= self._thresholds.t1_top1
        passes_t2 = top1 - top2 >= self._thresholds.t2_margin
        passes_t3 = incidental_distance >= self._thresholds.t3_incidental
        fire = passes_t1 and passes_t2 and passes_t3

        if not passes_t1:
            reason = "t1"
        elif not passes_t2:
            reason = "t2"
        elif not passes_t3:
            reason = "t3"
        else:
            reason = "ok"

        return GateDecision(fire=fire, confidence=top1, reason=reason)


def heuristic_decision() -> GateDecision:
    return GateDecision(True, 1.0, "heuristic")
