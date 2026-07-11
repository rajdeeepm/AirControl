from __future__ import annotations

import pytest

from aircontrol.gate import (
    ConfidenceGate,
    GateDecision,
    GateThresholds,
    heuristic_decision,
)


@pytest.mark.parametrize(
    (
        "top1",
        "top2",
        "incidental_distance",
        "expected_fire",
        "expected_reason",
    ),
    [
        (0.7, 0.4, 0.6, False, "t1"),
        (0.9, 0.8, 0.6, False, "t2"),
        (0.9, 0.6, 0.4, False, "t3"),
        (0.9, 0.6, 0.6, True, "ok"),
        (0.7, 0.7, 0.4, False, "t1"),
        (0.9, 0.8, 0.4, False, "t2"),
    ],
)
def test_confidence_gate_truth_table(
    top1: float,
    top2: float,
    incidental_distance: float,
    expected_fire: bool,
    expected_reason: str,
) -> None:
    gate = ConfidenceGate(
        GateThresholds(t1_top1=0.8, t2_margin=0.2, t3_incidental=0.5)
    )

    decision = gate.evaluate(top1, top2, incidental_distance)

    assert decision == GateDecision(expected_fire, top1, expected_reason)


@pytest.mark.parametrize(
    ("top1", "top2", "incidental_distance"),
    [
        (0.0, 0.0, 0.0),
        (0.25, 0.1, 0.4),
        (1.0, 0.0, 100.0),
    ],
)
def test_default_thresholds_are_permissive(
    top1: float,
    top2: float,
    incidental_distance: float,
) -> None:
    decision = ConfidenceGate(GateThresholds()).evaluate(
        top1,
        top2,
        incidental_distance,
    )

    assert decision.fire is True
    assert decision.reason == "ok"


def test_heuristic_decision_fires() -> None:
    assert heuristic_decision().fire is True
