"""DQ-native implementation of ``returns.monthly_calendar_matrix``."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from .._boundary import CalculationError

_YEAR_MONTH = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")


def _month_ordinal(label: object) -> int:
    if not isinstance(label, str):
        raise CalculationError(
            "invalid_month_labels",
            "Every month label must use canonical YYYY-MM format.",
        )
    match = _YEAR_MONTH.fullmatch(label)
    if match is None:
        raise CalculationError(
            "invalid_month_labels",
            "Every month label must use canonical YYYY-MM format.",
        )
    return int(match.group("year")) * 12 + int(match.group("month")) - 1


def align_monthly_returns(
    returns: Sequence[float | int],
    observation_months: Sequence[str],
) -> tuple[tuple[float, ...], tuple[str, ...]]:
    """Align supplied simple returns to consecutive interval-end months."""

    if isinstance(returns, str | bytes) or not isinstance(returns, Sequence):
        raise CalculationError("invalid_returns", "Returns must be an ordered numeric sequence.")
    if isinstance(observation_months, str | bytes) or not isinstance(
        observation_months, Sequence
    ):
        raise CalculationError(
            "invalid_month_labels",
            "Observation months must be an ordered sequence.",
        )
    normalized_returns: list[float] = []
    for item in returns:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise CalculationError(
                "invalid_returns",
                "Every return must be a JSON number and booleans are not accepted.",
            )
        try:
            value = float(item)
        except OverflowError as exc:
            raise CalculationError("non_finite_returns", "Every return must be finite.") from exc
        if not math.isfinite(value):
            raise CalculationError("non_finite_returns", "Every return must be finite.")
        if value <= -1.0:
            raise CalculationError(
                "invalid_simple_returns",
                "Every simple return must be greater than negative one.",
            )
        normalized_returns.append(value)
    if not normalized_returns:
        raise CalculationError("insufficient_returns", "At least one return is required.")

    months = tuple(observation_months)
    if len(months) != len(normalized_returns) + 1:
        raise CalculationError(
            "return_count_mismatch",
            "Observation months must contain one more item than returns.",
        )
    ordinals = tuple(_month_ordinal(label) for label in months)
    if len(set(ordinals)) != len(ordinals):
        raise CalculationError("duplicate_months", "Duplicate month labels are not accepted.")
    if any(right < left for left, right in zip(ordinals, ordinals[1:], strict=False)):
        raise CalculationError(
            "non_increasing_months",
            "Month labels must be strictly increasing and are never silently reordered.",
        )
    if any(right != left + 1 for left, right in zip(ordinals, ordinals[1:], strict=False)):
        raise CalculationError(
            "non_consecutive_months",
            "Completed month-end labels must be consecutive without missing months.",
        )
    return tuple(normalized_returns), months[1:]


__all__ = ["align_monthly_returns"]
