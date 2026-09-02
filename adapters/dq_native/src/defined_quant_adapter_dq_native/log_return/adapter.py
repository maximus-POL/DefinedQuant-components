"""Thin canonical adapter for the DQ-native ``returns.log`` implementation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .._boundary import (
    AdapterFailure,
    CalculationError,
    adapter_failure,
    map_payload,
    map_timestamps,
    translate_calculation,
)
from .kernel import calculate_log_returns

_FIELDS = frozenset({"prices", "timestamps"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map canonical input to the kernel and return canonical capability output."""

    mapped = map_payload(payload, fields=_FIELDS, required=frozenset({"prices"}))
    prices = mapped["prices"]
    if isinstance(prices, str | bytes) or not isinstance(prices, Sequence):
        raise adapter_failure("invalid_prices", "Prices must be an ordered numeric sequence.")
    try:
        returns = calculate_log_returns(prices)
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    return_timestamps, ordering_status = map_timestamps(
        mapped.get("timestamps"),
        observation_count=len(prices),
        output_start=1,
    )
    return {
        "returns": list(returns),
        "return_kind": "log",
        "return_timestamps": return_timestamps,
        "ordering_status": ordering_status,
    }


__all__ = ["AdapterFailure", "execute"]
