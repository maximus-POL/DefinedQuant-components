"""Thin canonical adapter for the DQ-native ``prices.rebase`` implementation."""

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
from .kernel import rebase_prices

_FIELDS = frozenset({"base_index", "base_value", "prices", "timestamps"})
_REQUIRED = frozenset({"base_index", "base_value", "prices"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map canonical input to rebasing and return canonical capability output."""

    mapped = map_payload(payload, fields=_FIELDS, required=_REQUIRED)
    prices = mapped["prices"]
    if isinstance(prices, str | bytes) or not isinstance(prices, Sequence):
        raise adapter_failure("invalid_prices", "Prices must be an ordered numeric sequence.")
    try:
        values = rebase_prices(
            prices,
            base_index=mapped["base_index"],  # type: ignore[arg-type]
            base_value=mapped["base_value"],  # type: ignore[arg-type]
        )
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    timestamps, ordering_status = map_timestamps(
        mapped.get("timestamps"),
        observation_count=len(prices),
    )
    return {
        "index_values": list(values),
        "base_index": mapped["base_index"],
        "base_value": float(mapped["base_value"]),  # type: ignore[arg-type]
        "index_timestamps": timestamps,
        "ordering_status": ordering_status,
    }


__all__ = ["AdapterFailure", "execute"]
