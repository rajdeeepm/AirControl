import math

import pytest

from aircontrol.onefilter import OneEuroFilter


def test_first_sample_is_returned_exactly() -> None:
    filter_ = OneEuroFilter()

    assert filter_.update(0.25, 0.75, 10.0) == (0.25, 0.75)


def test_slow_steady_motion_tracks_without_stalling() -> None:
    filter_ = OneEuroFilter()
    previous_x, previous_y = filter_.update(0.0, 0.0, 0.0)

    for frame in range(1, 61):
        timestamp = frame / 30
        current_x, current_y = filter_.update(0.1 * timestamp, 0.0, timestamp)
        assert current_x > previous_x
        assert current_y == 0.0
        previous_x, previous_y = current_x, current_y

    assert 0.18 < previous_x < 0.2
    assert previous_y == 0.0


def test_static_jitter_is_strongly_suppressed() -> None:
    filter_ = OneEuroFilter()
    filter_.update(0.5, 0.5, 0.0)
    outputs: list[tuple[float, float]] = []

    for frame in range(1, 121):
        sign = 1.0 if frame % 2 else -1.0
        outputs.append(
            filter_.update(
                0.5 + sign * 0.002,
                0.5 - sign * 0.002,
                frame / 30,
            )
        )

    settled = outputs[-60:]
    assert max(math.hypot(x - 0.5, y - 0.5) for x, y in settled) < 3e-4


def test_fast_motion_raises_cutoff_and_stays_responsive() -> None:
    fixed = OneEuroFilter(beta=0.0)
    adaptive = OneEuroFilter()
    fixed.update(0.0, 0.0, 0.0)
    adaptive.update(0.0, 0.0, 0.0)

    fixed_x, _ = fixed.update(1.0, 0.0, 1 / 30)
    adaptive_x, _ = adaptive.update(1.0, 0.0, 1 / 30)

    assert fixed_x < 0.2
    assert adaptive_x > 0.18
    assert adaptive_x > fixed_x


def test_fast_motion_on_one_axis_does_not_pass_more_cross_axis_jitter() -> None:
    moving = OneEuroFilter(beta=1.0)
    stationary = OneEuroFilter(beta=1.0)
    moving.update(0.0, 0.0, 0.0)
    stationary.update(0.0, 0.0, 0.0)

    _moving_x, moving_y = moving.update(1.0, 0.002, 1 / 30)
    _stationary_x, stationary_y = stationary.update(0.0, 0.002, 1 / 30)

    assert moving_y == pytest.approx(stationary_y)


def test_irregular_intervals_remain_finite_monotonic_and_bounded() -> None:
    filter_ = OneEuroFilter()
    outputs = [filter_.update(0.0, 0.0, 10.0)]

    for timestamp, x in ((10.01, 0.01), (10.04, 0.04), (10.041, 0.041), (10.2, 0.2)):
        output = filter_.update(x, 0.0, timestamp)
        outputs.append(output)
        assert math.isfinite(output[0])
        assert math.isfinite(output[1])
        assert outputs[-2][0] < output[0] <= x


def test_reconfigure_preserves_state_and_applies_new_cutoff() -> None:
    configured = OneEuroFilter()
    baseline = OneEuroFilter()
    for filter_ in (configured, baseline):
        filter_.update(0.0, 0.0, 0.0)
        filter_.update(0.2, 0.0, 0.1)

    before, _ = configured.update(0.25, 0.0, 0.15)
    configured.configure(min_cutoff=4.0, beta=0.02, d_cutoff=1.0)
    configured_x, _ = configured.update(0.3, 0.0, 0.2)
    baseline_x, _ = baseline.update(0.3, 0.0, 0.2)

    assert before < configured_x < 0.3
    assert configured_x > baseline_x


def test_non_increasing_timestamps_are_ignored_without_poisoning_state() -> None:
    filter_ = OneEuroFilter()
    filter_.update(0.0, 0.0, 10.0)
    accepted = filter_.update(0.2, 0.0, 10.2)

    assert filter_.update(99.0, -99.0, 10.2) == accepted
    assert filter_.update(99.0, -99.0, 10.1) == accepted

    resumed_x, resumed_y = filter_.update(0.3, 0.0, 10.3)
    assert accepted[0] < resumed_x < 0.3
    assert resumed_y == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_cutoff": 0.0},
        {"min_cutoff": float("nan")},
        {"min_cutoff": float("inf")},
        {"beta": -0.01},
        {"beta": float("nan")},
        {"beta": float("inf")},
        {"d_cutoff": 0.0},
        {"d_cutoff": float("nan")},
        {"d_cutoff": float("inf")},
    ],
)
def test_invalid_parameters_are_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        OneEuroFilter(**kwargs)
