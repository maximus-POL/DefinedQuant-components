"""Closed helpers shared by trusted DQ-native adapter boundaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


class CalculationError(ValueError):
    """Closed failure raised by a pure DQ-native capability kernel."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {"violation_ids": [code]})


class AdapterFailure(RuntimeError):
    """Safe, closed failure translated at a trusted adapter boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retry_allowed = False


def adapter_failure(code: str, message: str) -> AdapterFailure:
    """Create one failure carrying only its closed violation identifier."""

    return AdapterFailure(code, message, details={"violation_ids": [code]})


def map_payload(
    payload: Mapping[str, object],
    *,
    fields: frozenset[str],
    required: frozenset[str],
) -> Mapping[str, object]:
    """Refuse undeclared control fields and require the canonical input fields."""

    if not isinstance(payload, Mapping):
        raise adapter_failure("invalid_adapter_input", "Adapter input must be an object.")
    if any(not isinstance(field, str) for field in payload):
        raise adapter_failure(
            "invalid_adapter_input",
            "Adapter input field names must be strings.",
        )
    unknown = tuple(sorted(set(payload) - fields))
    if unknown:
        raise AdapterFailure(
            "invalid_adapter_input",
            "Adapter input contains undeclared fields.",
            details={"fields": list(unknown)},
        )
    missing = tuple(sorted(required - set(payload)))
    if missing:
        raise AdapterFailure(
            "invalid_adapter_input",
            "Adapter input omits required fields.",
            details={"fields": list(missing)},
        )
    return payload


def translate_calculation(error: CalculationError) -> AdapterFailure:
    """Translate a closed kernel failure without exposing implementation details."""

    return AdapterFailure(error.code, error.message, details=error.details)


def map_timestamps(
    value: object,
    *,
    observation_count: int,
    output_start: int = 0,
) -> tuple[list[str] | None, str]:
    """Validate canonical timestamp alignment and return normalized output timestamps."""

    if value is None:
        return None, "unverified"
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise adapter_failure(
            "invalid_timestamps",
            "Timestamps must be null or an ordered sequence.",
        )
    parsed: list[tuple[datetime, str]] = []
    for item in value:
        if isinstance(item, datetime):
            timestamp = item
        elif isinstance(item, str):
            try:
                timestamp = datetime.fromisoformat(item.replace("Z", "+00:00"))
            except ValueError as exc:
                raise adapter_failure(
                    "invalid_timestamps",
                    "Every timestamp must be a valid timezone-aware RFC 3339 value.",
                ) from exc
        else:
            raise adapter_failure(
                "invalid_timestamps",
                "Every timestamp must be a valid timezone-aware RFC 3339 value.",
            )
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise adapter_failure(
                "invalid_timestamps",
                "Every timestamp must include a timezone offset.",
            )
        parsed.append((timestamp, timestamp.isoformat().replace("+00:00", "Z")))
    if len(parsed) != observation_count:
        raise adapter_failure(
            "timestamp_length_mismatch",
            "Timestamps and observations must align one-to-one.",
        )
    instants = tuple(item[0] for item in parsed)
    if len(set(instants)) != len(instants):
        raise adapter_failure("duplicate_timestamps", "Duplicate timestamps are not accepted.")
    if any(current < previous for previous, current in zip(instants, instants[1:], strict=False)):
        raise adapter_failure(
            "non_increasing_timestamps",
            "Timestamps must be strictly increasing and are never silently reordered.",
        )
    return [item[1] for item in parsed[output_start:]], "verified"


__all__ = [
    "AdapterFailure",
    "CalculationError",
    "adapter_failure",
    "map_payload",
    "map_timestamps",
    "translate_calculation",
]
