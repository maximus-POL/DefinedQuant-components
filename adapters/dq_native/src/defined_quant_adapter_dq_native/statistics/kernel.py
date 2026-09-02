"""Numerically defensive DQ-native statistical capability kernels."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .._boundary import CalculationError


def _finite_values(
    values: Sequence[float | int],
    *,
    minimum: int,
) -> tuple[float, ...]:
    if isinstance(values, str | bytes) or not isinstance(values, Sequence):
        raise CalculationError("invalid_values", "Values must be an ordered numeric sequence.")
    if len(values) < minimum:
        raise CalculationError(
            "insufficient_values",
            f"At least {minimum} finite values are required.",
        )
    normalized: list[float] = []
    for item in values:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise CalculationError(
                "invalid_values",
                "Every value must be a JSON number and booleans are not accepted.",
            )
        try:
            value = float(item)
        except OverflowError as exc:
            raise CalculationError("non_finite_values", "Every value must be finite.") from exc
        if not math.isfinite(value):
            raise CalculationError("non_finite_values", "Every value must be finite.")
        normalized.append(value)
    return tuple(normalized)


def _sample_standard_deviation(values: tuple[float, ...]) -> float:
    anchor = values[0]
    centered = tuple(value - anchor for value in values)
    working_values = centered if all(math.isfinite(value) for value in centered) else values
    scale = max(abs(value) for value in working_values)
    if scale == 0.0:
        return 0.0
    scaled = tuple(value / scale for value in working_values)
    mean = math.fsum(scaled) / len(scaled)
    root_sum_squared = 0.0
    for value in scaled:
        root_sum_squared = math.hypot(root_sum_squared, value - mean)
    normalized = root_sum_squared / math.sqrt(len(scaled) - 1)
    result = scale * normalized
    if not math.isfinite(result) or (normalized > 0.0 and result == 0.0):
        raise CalculationError(
            "non_finite_result",
            "Finite inputs produced an unrepresentable sample standard deviation.",
        )
    return result


def calculate_sample_standard_deviation(values: Sequence[float | int]) -> float:
    """Calculate sample standard deviation with an n-minus-one denominator."""

    return _sample_standard_deviation(_finite_values(values, minimum=2))


def calculate_rolling_sample_standard_deviations(
    values: Sequence[float | int],
    *,
    window_length: int,
) -> tuple[float, ...]:
    """Calculate sample standard deviation for every complete step-one window."""

    normalized = _finite_values(values, minimum=2)
    if isinstance(window_length, bool) or not isinstance(window_length, int):
        raise CalculationError("invalid_window_length", "Window length must be an integer.")
    if window_length < 2:
        raise CalculationError("window_too_short", "Window length must be at least two.")
    if window_length > len(normalized):
        raise CalculationError(
            "window_exceeds_values",
            "At least one complete rolling window is required.",
        )
    return tuple(
        _sample_standard_deviation(normalized[start : start + window_length])
        for start in range(len(normalized) - window_length + 1)
    )


def _non_negative_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CalculationError(f"invalid_{field}", f"{field} must be a JSON number.")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise CalculationError(f"non_finite_{field}", f"{field} must be finite.") from exc
    if not math.isfinite(normalized):
        raise CalculationError(f"non_finite_{field}", f"{field} must be finite.")
    if normalized < 0.0:
        raise CalculationError(f"negative_{field}", f"{field} must be non-negative.")
    return normalized


def _positive_factor(value: object) -> float:
    factor = _non_negative_number(value, field="annualization_factor")
    if factor <= 0.0:
        raise CalculationError(
            "non_positive_annualization_factor",
            "The annualization factor must be greater than zero.",
        )
    return factor


def annualize_square_root(
    periodic_value: float | int,
    annualization_factor: float | int,
) -> float:
    """Apply explicit square-root-of-time scaling to one non-negative value."""

    periodic = _non_negative_number(periodic_value, field="periodic_value")
    factor = _positive_factor(annualization_factor)
    annualized = periodic * math.sqrt(factor)
    if not math.isfinite(annualized) or (periodic > 0.0 and annualized == 0.0):
        raise CalculationError(
            "non_finite_result",
            "Finite inputs produced an unrepresentable annualized value.",
        )
    return annualized


def annualize_square_root_series(
    periodic_values: Sequence[float | int],
    annualization_factor: float | int,
) -> tuple[float, ...]:
    """Apply explicit square-root-of-time scaling to a non-empty ordered series."""

    if isinstance(periodic_values, str | bytes) or not isinstance(periodic_values, Sequence):
        raise CalculationError(
            "invalid_periodic_values",
            "Periodic values must be an ordered numeric sequence.",
        )
    if not periodic_values:
        raise CalculationError(
            "insufficient_periodic_values",
            "At least one periodic value is required.",
        )
    factor = _positive_factor(annualization_factor)
    return tuple(annualize_square_root(item, factor) for item in periodic_values)


__all__ = [
    "annualize_square_root",
    "annualize_square_root_series",
    "calculate_rolling_sample_standard_deviations",
    "calculate_sample_standard_deviation",
]
