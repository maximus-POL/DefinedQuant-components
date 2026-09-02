"""DQ-native binary64 implementation of the ``prices.rebase`` capability."""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from fractions import Fraction

from .._boundary import CalculationError


def _positive_number(value: object, *, code: str, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CalculationError(code, message)
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise CalculationError(code, message) from exc
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise CalculationError(code, message)
    return normalized


def _rebased_value(base_value: float, price: float, base_price: float) -> float:
    ratio = price / base_price
    value = base_value * ratio
    if math.isfinite(value) and ratio >= sys.float_info.min and value >= sys.float_info.min:
        return value
    try:
        return float(
            Fraction.from_float(base_value)
            * Fraction.from_float(price)
            / Fraction.from_float(base_price)
        )
    except OverflowError:
        return math.inf


def rebase_prices(
    prices: Sequence[float | int],
    *,
    base_index: int,
    base_value: float | int,
) -> tuple[float, ...]:
    """Rebase one positive path while preserving the explicit base exactly."""

    if isinstance(prices, str | bytes) or not isinstance(prices, Sequence):
        raise CalculationError("invalid_prices", "Prices must be an ordered numeric sequence.")
    if not prices:
        raise CalculationError("empty_prices", "At least one price is required.")
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
    if isinstance(base_index, bool) or not isinstance(base_index, int) or base_index < 0:
        raise CalculationError(
            "invalid_base_index",
            "The base index must be a non-negative integer.",
        )
    if base_index >= len(normalized):
        raise CalculationError(
            "base_index_out_of_range",
            "The base index must identify an observation in prices.",
        )
    level = _positive_number(
        base_value,
        code="invalid_base_value",
        message="The base value must be finite and strictly positive.",
    )
    base_price = normalized[base_index]
    result: list[float] = []
    for index, price in enumerate(normalized):
        value = level if index == base_index else _rebased_value(level, price, base_price)
        if not math.isfinite(value) or value <= 0.0:
            raise CalculationError(
                "non_finite_result",
                "Finite positive inputs produced an unrepresentable rebased value.",
            )
        result.append(value)
    return tuple(result)


__all__ = ["rebase_prices"]
