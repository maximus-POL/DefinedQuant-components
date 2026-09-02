"""Thin canonical adapter for the DQ-native ``returns.simple`` implementation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from .._boundary import AdapterFailure, map_payload, map_timestamps
from .kernel import SimpleReturnCalculationError, calculate_simple_returns

_INPUT_FIELDS = frozenset({"prices", "timestamps"})


def _failure(code: str, message: str) -> AdapterFailure:
    return AdapterFailure(code, message, details={"violation_ids": [code]})


def _map_timestamps(
    value: object,
    *,
    price_count: int,
) -> tuple[tuple[str, ...] | None, Literal["verified", "unverified"]]:
    timestamps, status = map_timestamps(
        value,
        observation_count=price_count,
        output_start=1,
    )
    return None if timestamps is None else tuple(timestamps), status  # type: ignore[return-value]


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    """Map canonical input to the kernel and return canonical capability output."""

    map_payload(payload, fields=_INPUT_FIELDS, required=frozenset({"prices"}))
    raw_prices = payload["prices"]
    if isinstance(raw_prices, str | bytes) or not isinstance(raw_prices, Sequence):
        raise _failure("invalid_prices", "Prices must be an ordered numeric sequence.")

    try:
        returns = calculate_simple_returns(raw_prices)
    except SimpleReturnCalculationError as exc:
        raise AdapterFailure(
            exc.code,
            exc.message,
            details={"violation_ids": [exc.code]},
        ) from exc

    return_timestamps, ordering_status = _map_timestamps(
        payload.get("timestamps"),
        price_count=len(raw_prices),
    )
    return {
        "returns": list(returns),
        "return_kind": "simple",
        "return_timestamps": (
            None if return_timestamps is None else list(return_timestamps)
        ),
        "ordering_status": ordering_status,
    }


__all__ = ["AdapterFailure", "execute"]
