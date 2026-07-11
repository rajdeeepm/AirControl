from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass

try:
    import psutil
except ImportError:  # pragma: no cover - exercised through sample_cpu
    psutil = None  # type: ignore[assignment]


_SECONDS_PER_HOUR = 3_600.0
_MINIMUM_UPTIME_HOURS = 1e-12
_MAX_LATENCY_SAMPLES = 1_024


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    candidates_per_hour: float
    armed_candidates_per_hour: float
    fp_per_hour: float
    latency_ms_p50: float
    latency_ms_p95: float
    cpu_pct: float
    uptime_seconds: float


class Metrics:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._candidate_count = 0
        self._armed_candidate_count = 0
        self._false_positive_count = 0
        self._last_begin: float | None = None
        self._latencies_ms: deque[float] = deque(maxlen=_MAX_LATENCY_SAMPLES)
        self._cpu_pct = 0.0

    def note_candidate(self, armed: bool) -> None:
        self._candidate_count += 1
        if armed:
            self._armed_candidate_count += 1

    def note_false_positive(self) -> None:
        self._false_positive_count += 1

    def begin_gesture(self) -> None:
        self._last_begin = self._clock()

    def note_action(self) -> None:
        if self._last_begin is None:
            return
        latency_ms = max(0.0, self._clock() - self._last_begin) * 1_000.0
        self._latencies_ms.append(latency_ms)
        self._last_begin = None

    def sample_cpu(self) -> None:
        if psutil is not None:
            self._cpu_pct = float(psutil.cpu_percent(interval=None))

    def snapshot(self) -> MetricsSnapshot:
        uptime_seconds = max(0.0, self._clock() - self._started_at)
        uptime_hours = max(
            uptime_seconds / _SECONDS_PER_HOUR,
            _MINIMUM_UPTIME_HOURS,
        )
        latencies = sorted(self._latencies_ms)
        return MetricsSnapshot(
            candidates_per_hour=self._candidate_count / uptime_hours,
            armed_candidates_per_hour=self._armed_candidate_count / uptime_hours,
            fp_per_hour=self._false_positive_count / uptime_hours,
            latency_ms_p50=_percentile(latencies, 0.50),
            latency_ms_p95=_percentile(latencies, 0.95),
            cpu_pct=self._cpu_pct,
            uptime_seconds=uptime_seconds,
        )

    def export_json(self) -> str:
        return json.dumps(asdict(self.snapshot()))


def _percentile(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0

    position = (len(sorted_values) - 1) * quantile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    lower = sorted_values[lower_index]
    upper = sorted_values[upper_index]
    return lower + (upper - lower) * fraction
