"""Thin adapter for ``statistics.square_root_annualize``."""

from __future__ import annotations

from collections.abc import Mapping

from .._boundary import AdapterFailure, CalculationError, map_payload, translate_calculation
from .kernel import annualize_square_root

_FIELDS = frozenset({"periodic_value", "annualization_factor"})


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map explicit canonical inputs to scalar square-root annualization."""

    mapped = map_payload(payload, fields=_FIELDS, required=_FIELDS)
    try:
        result = annualize_square_root(
            mapped["periodic_value"],  # type: ignore[arg-type]
            mapped["annualization_factor"],  # type: ignore[arg-type]
        )
    except CalculationError as exc:
        raise translate_calculation(exc) from exc
    return {"annualized_value": result}


__all__ = ["AdapterFailure", "execute"]
