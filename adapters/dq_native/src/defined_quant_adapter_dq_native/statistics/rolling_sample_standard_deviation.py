"""Thin adapter for ``statistics.rolling_sample_standard_deviation``."""

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
from .kernel import calculate_rolling_sample_standard_deviations

_FIELDS = frozenset({"values", "window_length", "timestamps"})
_REQUIRED = frozenset({"values", "window_length"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map canonical inputs to step-one rolling sample deviations."""

    mapped = map_payload(payload, fields=_FIELDS, required=_REQUIRED)
    values = mapped["values"]
    window_length = mapped["window_length"]
    if isinstance(values, str | bytes) or not isinstance(values, Sequence):
        raise adapter_failure("invalid_values", "Values must be an ordered numeric sequence.")
    if isinstance(window_length, bool) or not isinstance(window_length, int):
        raise adapter_failure("invalid_window_length", "Window length must be an integer.")
    try:
        result = calculate_rolling_sample_standard_deviations(
            values,
            window_length=window_length,
        )
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    timestamps, ordering_status = map_timestamps(
        mapped.get("timestamps"),
        observation_count=len(values),
        output_start=window_length - 1,
    )
    return {
        "standard_deviations": list(result),
        "window_length": window_length,
        "window_end_timestamps": timestamps,
        "ordering_status": ordering_status,
    }


__all__ = ["AdapterFailure", "execute"]
