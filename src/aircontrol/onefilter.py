from __future__ import annotations

import math


class OneEuroFilter:
    """Dependency-free One-Euro filter for a two-dimensional signal."""

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.02,
        d_cutoff: float = 1.0,
    ) -> None:
        self._min_cutoff = 1.0
        self._beta = 0.0
        self._d_cutoff = 1.0
        self.configure(
            min_cutoff=min_cutoff,
            beta=beta,
            d_cutoff=d_cutoff,
        )
        self.reset()

    @property
    def parameters(self) -> tuple[float, float, float]:
        return self._min_cutoff, self._beta, self._d_cutoff

    def configure(
        self,
        *,
        min_cutoff: float,
        beta: float,
        d_cutoff: float,
    ) -> None:
        """Update tuning without discarding the current filtered position."""
        min_cutoff_value = self._finite_number(min_cutoff, "min_cutoff")
        beta_value = self._finite_number(beta, "beta")
        d_cutoff_value = self._finite_number(d_cutoff, "d_cutoff")
        if min_cutoff_value <= 0.0:
            raise ValueError("min_cutoff must be positive")
        if beta_value < 0.0:
            raise ValueError("beta cannot be negative")
        if d_cutoff_value <= 0.0:
            raise ValueError("d_cutoff must be positive")
        self._min_cutoff = min_cutoff_value
        self._beta = beta_value
        self._d_cutoff = d_cutoff_value

    def reset(self) -> None:
        self._timestamp: float | None = None
        self._raw_x = 0.0
        self._raw_y = 0.0
        self._filtered_x = 0.0
        self._filtered_y = 0.0
        self._derivative_x = 0.0
        self._derivative_y = 0.0

    def update(
        self,
        x: float,
        y: float,
        timestamp: float,
    ) -> tuple[float, float]:
        x_value = self._finite_number(x, "x")
        y_value = self._finite_number(y, "y")
        timestamp_value = self._finite_number(timestamp, "timestamp")

        if self._timestamp is None:
            self._timestamp = timestamp_value
            self._raw_x = x_value
            self._raw_y = y_value
            self._filtered_x = x_value
            self._filtered_y = y_value
            return x_value, y_value

        if timestamp_value <= self._timestamp:
            return self._filtered_x, self._filtered_y

        elapsed = timestamp_value - self._timestamp
        raw_derivative_x = (x_value - self._raw_x) / elapsed
        raw_derivative_y = (y_value - self._raw_y) / elapsed
        derivative_alpha = self._alpha(self._d_cutoff, elapsed)
        derivative_x = self._low_pass(
            raw_derivative_x,
            self._derivative_x,
            derivative_alpha,
        )
        derivative_y = self._low_pass(
            raw_derivative_y,
            self._derivative_y,
            derivative_alpha,
        )
        cutoff_x = self._min_cutoff + self._beta * abs(derivative_x)
        cutoff_y = self._min_cutoff + self._beta * abs(derivative_y)
        filtered_x = self._low_pass(
            x_value,
            self._filtered_x,
            self._alpha(cutoff_x, elapsed),
        )
        filtered_y = self._low_pass(
            y_value,
            self._filtered_y,
            self._alpha(cutoff_y, elapsed),
        )

        self._timestamp = timestamp_value
        self._raw_x = x_value
        self._raw_y = y_value
        self._filtered_x = filtered_x
        self._filtered_y = filtered_y
        self._derivative_x = derivative_x
        self._derivative_y = derivative_y
        return filtered_x, filtered_y

    @staticmethod
    def _finite_number(value: float, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{name} must be a finite number")
        return float(value)

    @staticmethod
    def _alpha(cutoff: float, elapsed: float) -> float:
        rate = 2.0 * math.pi * cutoff * elapsed
        return rate / (rate + 1.0)

    @staticmethod
    def _low_pass(value: float, previous: float, alpha: float) -> float:
        return previous + alpha * (value - previous)


__all__ = ["OneEuroFilter"]
