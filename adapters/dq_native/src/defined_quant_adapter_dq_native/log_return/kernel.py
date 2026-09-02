"""DQ-native binary64 implementation of the ``returns.log`` capability."""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence

from .._boundary import CalculationError


def _normalize_prices(prices: Sequence[float | int]) -> tuple[float, ...]:
    if isinstance(prices, str | bytes) or not isinstance(prices, Sequence):
        raise CalculationError("invalid_prices", "Prices must be an ordered numeric sequence.")
    if len(prices) < 2:
        raise CalculationError("insufficient_prices", "At least two ordered prices are required.")
    normalized: list[float] = []
    for item in prices:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise CalculationError(
                "invalid_prices",
                "Every price must be a JSON number and booleans are not accepted.",
            )
        try:
            value = float(item)
        except OverflowError as exc:
            raise CalculationError("non_finite_prices", "Every price must be finite.") from exc
        if not math.isfinite(value):
            raise CalculationError("non_finite_prices", "Every price must be finite.")
        if value <= 0.0:
            raise CalculationError("non_positive_prices", "Prices must be strictly positive.")
        normalized.append(value)
    return tuple(normalized)


def _log_return(previous: float, current: float) -> float:
    if current >= previous / 2.0 and previous >= current / 2.0:
        value = math.log1p((current - previous) / previous)
    else:
        ratio = current / previous
        value = (
            math.log(ratio)
            if math.isfinite(ratio) and ratio >= sys.float_info.min
            else math.log(current) - math.log(previous)
        )
    if not math.isfinite(value):
        raise CalculationError(
            "non_finite_result",
            "A finite positive price pair produced an unrepresentable log return.",
        )
    return value


def calculate_log_returns(prices: Sequence[float | int]) -> tuple[float, ...]:
    """Calculate precision-preserving adjacent binary64 log returns."""

    normalized = _normalize_prices(prices)
    return tuple(
        _log_return(previous, current)
        for previous, current in zip(normalized, normalized[1:], strict=False)
    )


__all__ = ["calculate_log_returns"]
