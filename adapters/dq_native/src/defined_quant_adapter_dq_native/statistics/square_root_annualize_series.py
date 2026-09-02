"""Thin adapter for ``statistics.square_root_annualize_series``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .._boundary import (
    AdapterFailure,
    CalculationError,
    adapter_failure,
    map_payload,
    translate_calculation,
)
from .kernel import annualize_square_root_series

_FIELDS = frozenset({"periodic_values", "annualization_factor"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map explicit canonical inputs to series square-root annualization."""

    mapped = map_payload(payload, fields=_FIELDS, required=_FIELDS)
    values = mapped["periodic_values"]
    if isinstance(values, str | bytes) or not isinstance(values, Sequence):
        raise adapter_failure(
            "invalid_periodic_values",
            "Periodic values must be an ordered numeric sequence.",
        )
    try:
        result = annualize_square_root_series(
            values,
            mapped["annualization_factor"],  # type: ignore[arg-type]
        )
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    return {"annualized_values": list(result)}


__all__ = ["AdapterFailure", "execute"]
