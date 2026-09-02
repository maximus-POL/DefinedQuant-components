"""Thin canonical adapter for the DQ-native ``performance.drawdown`` implementation."""

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
from .kernel import calculate_drawdown

_FIELDS = frozenset({"prices", "timestamps"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map canonical input to drawdown calculation and align episode timestamps."""

    mapped = map_payload(payload, fields=_FIELDS, required=frozenset({"prices"}))
    prices = mapped["prices"]
    if isinstance(prices, str | bytes) or not isinstance(prices, Sequence):
        raise adapter_failure("invalid_prices", "Prices must be an ordered numeric sequence.")
    try:
        result = calculate_drawdown(prices)
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    timestamps, ordering_status = map_timestamps(
        mapped.get("timestamps"),
        observation_count=len(prices),
    )

    def timestamp(index: int | None) -> str | None:
        return None if timestamps is None or index is None else timestamps[index]

    return {
        "drawdowns": list(result.drawdowns),
        "maximum_drawdown": result.maximum_drawdown,
        "running_peak_indices": list(result.running_peak_indices),
        "peak_index": result.peak_index,
        "trough_index": result.trough_index,
        "recovery_index": result.recovery_index,
        "recovered": result.recovery_index is not None,
        "drawdown_timestamps": timestamps,
        "peak_timestamp": timestamp(result.peak_index),
        "trough_timestamp": timestamp(result.trough_index),
        "recovery_timestamp": timestamp(result.recovery_index),
        "ordering_status": ordering_status,
    }


__all__ = ["AdapterFailure", "execute"]
