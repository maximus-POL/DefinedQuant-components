"""Thin adapter for ``statistics.sample_standard_deviation``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .._boundary import (
    AdapterFailure,
    CalculationError,
    adapter_failure,
    map_payload,
    translate_calculation,
)
from .kernel import calculate_sample_standard_deviation

_FIELDS = frozenset({"values"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map one canonical sample to a standard-deviation result."""

    mapped = map_payload(payload, fields=_FIELDS, required=_FIELDS)
    values = mapped["values"]
    if isinstance(values, str | bytes) or not isinstance(values, Sequence):
        raise adapter_failure("invalid_values", "Values must be an ordered numeric sequence.")
    try:
        result = calculate_sample_standard_deviation(values)
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    return {
        "standard_deviation": result,
        "sample_size": len(values),
        "degrees_of_freedom_adjustment": 1,
    }


__all__ = ["AdapterFailure", "execute"]
