"""DQ-native binary64 implementation of ``performance.drawdown``."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

from .._boundary import CalculationError


class DrawdownResult(NamedTuple):
    """Canonical numerical drawdown result before timestamp mapping."""

    drawdowns: tuple[float, ...]
    maximum_drawdown: float
    running_peak_indices: tuple[int, ...]
    peak_index: int
    trough_index: int
    recovery_index: int | None


def _normalize_prices(prices: Sequence[float | int]) -> tuple[float, ...]:
    if isinstance(prices, str | bytes) or not isinstance(prices, Sequence):
        raise CalculationError("invalid_prices", "Prices must be an ordered numeric sequence.")
    if not prices:
        raise CalculationError("empty_prices", "At least one price is required.")
    result: list[float] = []
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
        result.append(value)
    return tuple(result)


def calculate_drawdown(prices: Sequence[float | int]) -> DrawdownResult:
    """Calculate an underwater path and deterministic maximum-drawdown episode."""

    normalized = _normalize_prices(prices)
    running_peak = normalized[0]
    running_peak_index = 0
    drawdowns: list[float] = []
    peak_indices: list[int] = []
    for index, price in enumerate(normalized):
        if price >= running_peak:
            running_peak = price
            running_peak_index = index
            drawdown = 0.0
        else:
            ratio = price / running_peak
            if ratio == 0.0 or ratio - 1.0 == -1.0 or not math.isfinite(ratio):
                raise CalculationError(
                    "non_finite_result",
                    "A positive price-to-peak ratio was unrepresentable.",
                )
            drawdown = ratio - 1.0
        drawdowns.append(drawdown)
        peak_indices.append(running_peak_index)

    drawdown_path = tuple(drawdowns)
    running_indices = tuple(peak_indices)
    trough_index = drawdown_path.index(min(drawdown_path))
    peak_index = running_indices[trough_index]
    peak_value = normalized[peak_index]
    recovery_index = (
        peak_index
        if trough_index == peak_index
        else next(
            (
                index
                for index in range(trough_index + 1, len(normalized))
                if normalized[index] >= peak_value
            ),
            None,
        )
    )
    return DrawdownResult(
        drawdowns=drawdown_path,
        maximum_drawdown=drawdown_path[trough_index],
        running_peak_indices=running_indices,
        peak_index=peak_index,
        trough_index=trough_index,
        recovery_index=recovery_index,
    )


__all__ = ["DrawdownResult", "calculate_drawdown"]
