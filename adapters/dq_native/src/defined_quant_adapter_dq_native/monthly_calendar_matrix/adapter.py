"""Thin canonical adapter for ``returns.monthly_calendar_matrix``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .._boundary import (
    AdapterFailure,
    CalculationError,
    adapter_failure,
    map_payload,
    translate_calculation,
)
from .kernel import align_monthly_returns

_FIELDS = frozenset({"returns", "observation_months"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map canonical inputs, align calendar labels, and map canonical output."""

    mapped = map_payload(payload, fields=_FIELDS, required=_FIELDS)
    returns = mapped["returns"]
    months = mapped["observation_months"]
    if isinstance(returns, str | bytes) or not isinstance(returns, Sequence):
        raise adapter_failure("invalid_returns", "Returns must be an ordered numeric sequence.")
    if isinstance(months, str | bytes) or not isinstance(months, Sequence):
        raise adapter_failure(
            "invalid_month_labels",
            "Observation months must be an ordered sequence.",
        )
    try:
        monthly_returns, return_months = align_monthly_returns(
            returns,
            months,
        )
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    return {
        "monthly_returns": list(monthly_returns),
        "return_months": list(return_months),
        "gap_check": "verified_consecutive_months",
    }


__all__ = ["AdapterFailure", "execute"]
