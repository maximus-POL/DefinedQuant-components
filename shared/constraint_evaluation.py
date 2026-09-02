"""Closed methods-first constraint evaluation with no executable extension points."""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Mapping, Sequence, Sized
from numbers import Real
from typing import Any

MEASURES = frozenset(
    {
        "identity",
        "count",
        "interval_count",
        "stdev",
        "min",
        "max",
        "all_finite",
        "all_valid_calendar_month_labels",
        "has_duplicates",
        "is_consecutive_calendar_months",
        "is_strictly_increasing",
    }
)
OPERATORS = frozenset({"lt", "le", "gt", "ge", "eq", "ne", "in_set", "not_in_set"})
MAX_EXPRESSION_DEPTH = 3

_CALENDAR_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])", re.ASCII)


class ConstraintEvaluationError(ValueError):
    """A safe failure to evaluate one closed registry constraint."""

    def __init__(self, message: str, *, subject_id: str | None = None) -> None:
        self.subject_id = subject_id
        super().__init__(message)


def _error(message: str, *, subject_id: str | None) -> ConstraintEvaluationError:
    return ConstraintEvaluationError(message, subject_id=subject_id)


def _sequence(value: Any, measure: str, *, subject_id: str | None) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _error(f"measure {measure!r} requires a sequence value", subject_id=subject_id)
    return value


def _real(value: Any, measure: str, *, subject_id: str | None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error(f"measure {measure!r} requires numeric values", subject_id=subject_id)
    return float(value)


def _identity(value: Any, *, subject_id: str | None) -> Any:
    del subject_id
    return value


def _count(value: Any, *, subject_id: str | None) -> int:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sized):
        raise _error("measure 'count' requires a sized non-string value", subject_id=subject_id)
    return len(value)


def _interval_count(value: Any, *, subject_id: str | None) -> int:
    values = _sequence(value, "interval_count", subject_id=subject_id)
    return max(len(values) - 1, 0)


def _stdev(value: Any, *, subject_id: str | None) -> float:
    values = _sequence(value, "stdev", subject_id=subject_id)
    numbers = [_real(item, "stdev", subject_id=subject_id) for item in values]
    if len(numbers) < 2:
        raise _error("measure 'stdev' requires at least two values", subject_id=subject_id)
    result = statistics.stdev(numbers)
    if not math.isfinite(result):
        raise _error("measure 'stdev' produced a non-finite value", subject_id=subject_id)
    return result


def _minimum(value: Any, *, subject_id: str | None) -> Any:
    values = _sequence(value, "min", subject_id=subject_id)
    if not values:
        raise _error("measure 'min' is undefined for an empty sequence", subject_id=subject_id)
    try:
        return min(values)
    except (TypeError, ValueError) as exc:
        raise _error(
            "measure 'min' is undefined for the supplied values",
            subject_id=subject_id,
        ) from exc


def _maximum(value: Any, *, subject_id: str | None) -> Any:
    values = _sequence(value, "max", subject_id=subject_id)
    if not values:
        raise _error("measure 'max' is undefined for an empty sequence", subject_id=subject_id)
    try:
        return max(values)
    except (TypeError, ValueError) as exc:
        raise _error(
            "measure 'max' is undefined for the supplied values",
            subject_id=subject_id,
        ) from exc


def _all_finite(value: Any, *, subject_id: str | None) -> bool:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        numbers = [_real(item, "all_finite", subject_id=subject_id) for item in value]
    else:
        numbers = [_real(value, "all_finite", subject_id=subject_id)]
    return all(math.isfinite(item) for item in numbers)


def _all_valid_calendar_month_labels(value: Any, *, subject_id: str | None) -> bool:
    values = _sequence(value, "all_valid_calendar_month_labels", subject_id=subject_id)
    return all(
        isinstance(item, str) and _CALENDAR_MONTH.fullmatch(item) is not None
        for item in values
    )


def _has_duplicates(value: Any, *, subject_id: str | None) -> bool:
    values = _sequence(value, "has_duplicates", subject_id=subject_id)
    return any(item in values[:index] for index, item in enumerate(values))


def _is_consecutive_calendar_months(value: Any, *, subject_id: str | None) -> bool:
    values = _sequence(value, "is_consecutive_calendar_months", subject_id=subject_id)
    if not _all_valid_calendar_month_labels(value, subject_id=subject_id):
        return False
    ordinals = tuple(
        int(item[:4]) * 12 + int(item[5:]) - 1
        for item in values
        if isinstance(item, str)
    )
    return all(
        right == left + 1
        for left, right in zip(ordinals, ordinals[1:], strict=False)
    )


def _is_strictly_increasing(value: Any, *, subject_id: str | None) -> bool:
    values = _sequence(value, "is_strictly_increasing", subject_id=subject_id)
    try:
        return all(
            left < right for left, right in zip(values, values[1:], strict=False)
        )
    except TypeError as exc:
        raise _error(
            "measure 'is_strictly_increasing' requires mutually comparable values",
            subject_id=subject_id,
        ) from exc


_MEASURES = {
    "identity": _identity,
    "count": _count,
    "interval_count": _interval_count,
    "stdev": _stdev,
    "min": _minimum,
    "max": _maximum,
    "all_finite": _all_finite,
    "all_valid_calendar_month_labels": _all_valid_calendar_month_labels,
    "has_duplicates": _has_duplicates,
    "is_consecutive_calendar_months": _is_consecutive_calendar_months,
    "is_strictly_increasing": _is_strictly_increasing,
}


def _operand(value: Any, fields: Mapping[str, Any], *, subject_id: str | None) -> Any:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _error("constraint operand must be an object", subject_id=subject_id)
    keys = set(value)
    has_literal = "value" in value
    has_field = "field" in value
    if has_literal == has_field:
        raise _error(
            "constraint operand must contain exactly one of 'value' or 'field'",
            subject_id=subject_id,
        )
    if has_literal:
        if keys != {"value"}:
            raise _error(
                "literal constraint operands may contain only 'value'",
                subject_id=subject_id,
            )
        return value["value"]
    if not keys <= {"field", "measure"}:
        raise _error("field operand contains unknown keys", subject_id=subject_id)
    field = value["field"]
    if not isinstance(field, str) or field not in fields:
        raise _error("constraint references an unavailable field", subject_id=subject_id)
    measure = value.get("measure", "identity")
    if not isinstance(measure, str) or measure not in _MEASURES:
        raise _error(f"unknown constraint measure: {measure!r}", subject_id=subject_id)
    return _MEASURES[measure](fields[field], subject_id=subject_id)


def _compare(operator: Any, left: Any, right: Any, *, subject_id: str | None) -> bool:
    if not isinstance(operator, str) or operator not in OPERATORS:
        raise _error(f"unknown constraint operator: {operator!r}", subject_id=subject_id)
    try:
        if operator == "lt":
            return bool(left < right)
        if operator == "le":
            return bool(left <= right)
        if operator == "gt":
            return bool(left > right)
        if operator == "ge":
            return bool(left >= right)
        if operator == "eq":
            return bool(left == right)
        if operator == "ne":
            return bool(left != right)
        if not isinstance(right, (list, tuple, set, frozenset)):
            raise _error(
                f"operator {operator!r} requires a set-like right operand",
                subject_id=subject_id,
            )
        return left in right if operator == "in_set" else left not in right
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ConstraintEvaluationError):
            raise
        raise _error(
            f"operator {operator!r} is undefined for the supplied operands",
            subject_id=subject_id,
        ) from exc


def _evaluate(
    expression: Any,
    fields: Mapping[str, Any],
    *,
    depth: int,
    subject_id: str | None,
) -> bool:
    if depth > MAX_EXPRESSION_DEPTH:
        raise _error(
            f"constraint expression exceeds maximum depth {MAX_EXPRESSION_DEPTH}",
            subject_id=subject_id,
        )
    if not isinstance(expression, Mapping) or any(
        not isinstance(key, str) for key in expression
    ):
        raise _error("constraint expression must be an object", subject_id=subject_id)
    keys = set(expression)
    if keys in ({"all"}, {"any"}):
        branch = "all" if "all" in expression else "any"
        children = expression[branch]
        if not isinstance(children, list) or not children:
            raise _error(
                f"constraint '{branch}' must contain a non-empty list",
                subject_id=subject_id,
            )
        results = (
            _evaluate(child, fields, depth=depth + 1, subject_id=subject_id)
            for child in children
        )
        return all(results) if branch == "all" else any(results)
    if keys != {"left", "op", "right"}:
        raise _error(
            "comparison expression must contain exactly 'left', 'op', and 'right'",
            subject_id=subject_id,
        )
    return _compare(
        expression["op"],
        _operand(expression["left"], fields, subject_id=subject_id),
        _operand(expression["right"], fields, subject_id=subject_id),
        subject_id=subject_id,
    )


def evaluate_constraint(
    expression: Mapping[str, Any],
    fields: Mapping[str, Any],
    *,
    subject_id: str | None = None,
) -> bool:
    """Evaluate one already schema-validated methods-first constraint."""

    return _evaluate(expression, fields, depth=1, subject_id=subject_id)


__all__ = [
    "MAX_EXPRESSION_DEPTH",
    "MEASURES",
    "OPERATORS",
    "ConstraintEvaluationError",
    "evaluate_constraint",
]
