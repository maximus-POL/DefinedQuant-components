"""DQ-native binary64 implementation of the ``returns.simple`` capability."""

from __future__ import annotations

import math
from collections.abc import Sequence


class SimpleReturnCalculationError(ValueError):
    """Closed calculation failure raised by the DQ-native simple-return kernel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _normalize_prices(prices: Sequence[float | int]) -> tuple[float, ...]:
    if isinstance(prices, str | bytes) or not isinstance(prices, Sequence):
        raise SimpleReturnCalculationError(
            "invalid_prices",
            "Prices must be an ordered numeric sequence.",
        )
    if len(prices) < 2:
        raise SimpleReturnCalculationError(
            "insufficient_prices",
            "At least two ordered prices are required.",
        )

    normalized: list[float] = []
    for value in prices:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SimpleReturnCalculationError(
                "invalid_prices",
                "Every price must be a JSON number and booleans are not accepted.",
            )
        try:
            normalized.append(float(value))
        except OverflowError as exc:
            raise SimpleReturnCalculationError(
                "non_finite_prices",
                "Every price must be finite.",
            ) from exc

    result = tuple(normalized)
    if any(not math.isfinite(value) for value in result):
        raise SimpleReturnCalculationError(
            "non_finite_prices",
            "Every price must be finite.",
        )
    if any(value <= 0.0 for value in result):
        raise SimpleReturnCalculationError(
            "non_positive_prices",
            "Prices must be strictly positive.",
        )
    return result


def calculate_simple_returns(prices: Sequence[float | int]) -> tuple[float, ...]:
    """Calculate adjacent simple returns using difference-before-division binary64 arithmetic."""

    normalized = _normalize_prices(prices)
    returns: list[float] = []
    for previous, current in zip(normalized, normalized[1:], strict=False):
        value = (current - previous) / previous
        if not math.isfinite(value) or value == -1.0:
            raise SimpleReturnCalculationError(
                "non_finite_result",
                "Finite positive prices produced an unrepresentable simple return.",
            )
        returns.append(value)
    return tuple(returns)


__all__ = ["SimpleReturnCalculationError", "calculate_simple_returns"]

