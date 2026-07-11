from __future__ import annotations

import json
from dataclasses import fields

import pytest

from aircontrol.metrics import Metrics, MetricsSnapshot


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def test_candidate_rates_use_count_and_uptime() -> None:
    metrics = Metrics(clock=FakeClock([0.0, 1_800.0]))

    metrics.note_candidate(armed=True)
    metrics.note_candidate(armed=False)

    snapshot = metrics.snapshot()
    assert snapshot.uptime_seconds == pytest.approx(1_800.0)
    assert snapshot.candidates_per_hour == pytest.approx(4.0)
    assert snapshot.armed_candidates_per_hour == pytest.approx(2.0)


def test_latency_percentiles_from_known_values() -> None:
    metrics = Metrics(
        clock=FakeClock(
            [
                0.0,
                1.0,
                1.1,
                2.0,
                2.2,
                3.0,
                3.3,
                4.0,
                4.4,
                5.0,
                5.5,
                6.0,
            ]
        )
    )

    for _ in range(5):
        metrics.begin_gesture()
        metrics.note_action()

    snapshot = metrics.snapshot()
    assert snapshot.latency_ms_p50 == pytest.approx(300.0)
    assert snapshot.latency_ms_p95 == pytest.approx(480.0)


def test_false_positive_rate_uses_count_and_uptime() -> None:
    metrics = Metrics(clock=FakeClock([0.0, 900.0]))

    metrics.note_false_positive()
    metrics.note_false_positive()
    metrics.note_false_positive()

    assert metrics.snapshot().fp_per_hour == pytest.approx(12.0)


def test_sample_cpu_is_no_op_without_psutil() -> None:
    metrics = Metrics(clock=FakeClock([0.0, 10.0]))

    metrics.sample_cpu()

    assert metrics.snapshot().cpu_pct == 0.0


def test_export_json_matches_every_snapshot_field() -> None:
    metrics = Metrics(clock=FakeClock([0.0, 3_600.0, 3_600.0]))
    metrics.note_candidate(armed=True)
    metrics.note_false_positive()

    snapshot = metrics.snapshot()
    exported = json.loads(metrics.export_json())

    assert set(exported) == {field.name for field in fields(MetricsSnapshot)}
    for field in fields(MetricsSnapshot):
        assert exported[field.name] == getattr(snapshot, field.name)
