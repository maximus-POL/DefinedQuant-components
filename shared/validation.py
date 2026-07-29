"""Closed declarative constraint evaluation with no dynamic code execution."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence, Sized
from numbers import Real
from typing import Any

from defined_quant.catalog import component_record
from defined_quant.types import (
    AmbiguousInput,
    ContractEvaluationError,
    DomainError,
    Violation,
)

MEASURES = frozenset(
    {
        "identity",
        "count",
        "stdev",
        "min",
        "max",
        "all_finite",
        "has_duplicates",
        "is_strictly_increasing",
    }
)
OPERATORS = frozenset({"lt", "le", "gt", "ge", "eq", "ne", "in_set", "not_in_set"})
MAX_EXPRESSION_DEPTH = 3


def _contract_error(message: str, *, component_id: str | None = None) -> ContractEvaluationError:
    return ContractEvaluationError(message, component_id=component_id)


def _sequence(value: Any, measure: str, *, component_id: str | None) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _contract_error(
            f"measure {measure!r} requires a sequence value",
            component_id=component_id,
        )
    return value


def _real(value: Any, measure: str, *, component_id: str | None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _contract_error(
            f"measure {measure!r} requires numeric values",
            component_id=component_id,
        )
    return float(value)


def _measure_identity(value: Any, *, component_id: str | None) -> Any:
    del component_id
    return value


def _measure_count(value: Any, *, component_id: str | None) -> int:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sized):
        raise _contract_error(
            "measure 'count' requires a sized non-string value",
            component_id=component_id,
        )
    return len(value)


def _measure_stdev(value: Any, *, component_id: str | None) -> float:
    values = _sequence(value, "stdev", component_id=component_id)
    numbers = [_real(item, "stdev", component_id=component_id) for item in values]
    if len(numbers) < 2:
        raise _contract_error(
            "measure 'stdev' requires at least two values",
            component_id=component_id,
        )
    result = statistics.stdev(numbers)
    if not math.isfinite(result):
        raise _contract_error(
            "measure 'stdev' produced a non-finite value",
            component_id=component_id,
        )
    return result


def _measure_min(value: Any, *, component_id: str | None) -> Any:
    values = _sequence(value, "min", component_id=component_id)
    if not values:
        raise _contract_error(
            "measure 'min' is undefined for an empty sequence",
            component_id=component_id,
        )
    try:
        return min(values)
    except (TypeError, ValueError) as exc:
        raise _contract_error(
            "measure 'min' is undefined for the supplied values",
            component_id=component_id,
        ) from exc


def _measure_max(value: Any, *, component_id: str | None) -> Any:
    values = _sequence(value, "max", component_id=component_id)
    if not values:
        raise _contract_error(
            "measure 'max' is undefined for an empty sequence",
            component_id=component_id,
        )
    try:
        return max(values)
    except (TypeError, ValueError) as exc:
        raise _contract_error(
            "measure 'max' is undefined for the supplied values",
            component_id=component_id,
        ) from exc


def _measure_all_finite(value: Any, *, component_id: str | None) -> bool:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        numbers = [_real(item, "all_finite", component_id=component_id) for item in value]
    else:
        numbers = [_real(value, "all_finite", component_id=component_id)]
    return all(math.isfinite(item) for item in numbers)


def _measure_has_duplicates(value: Any, *, component_id: str | None) -> bool:
    values = _sequence(value, "has_duplicates", component_id=component_id)
    for index, item in enumerate(values):
        if any(item == prior for prior in values[:index]):
            return True
    return False


def _measure_is_strictly_increasing(value: Any, *, component_id: str | None) -> bool:
    values = _sequence(value, "is_strictly_increasing", component_id=component_id)
    try:
        return all(left < right for left, right in zip(values, values[1:], strict=False))
    except TypeError as exc:
        raise _contract_error(
            "measure 'is_strictly_increasing' requires mutually comparable values",
            component_id=component_id,
        ) from exc


_MEASURE_DISPATCH = {
    "identity": _measure_identity,
    "count": _measure_count,
    "stdev": _measure_stdev,
    "min": _measure_min,
    "max": _measure_max,
    "all_finite": _measure_all_finite,
    "has_duplicates": _measure_has_duplicates,
    "is_strictly_increasing": _measure_is_strictly_increasing,
}


def _resolve_operand(
    operand: Any,
    values: Mapping[str, Any],
    *,
    component_id: str | None,
) -> Any:
    if not isinstance(operand, Mapping) or any(not isinstance(key, str) for key in operand):
        raise _contract_error("constraint operand must be an object", component_id=component_id)
    keys = set(operand)
    has_value = "value" in operand
    has_field = "field" in operand
    if has_value == has_field:
        raise _contract_error(
            "constraint operand must contain exactly one of 'value' or 'field'",
            component_id=component_id,
        )
    if has_value:
        if keys != {"value"}:
            raise _contract_error(
                "literal constraint operands may contain only 'value'",
                component_id=component_id,
            )
        return operand["value"]

    if not keys.issubset({"field", "measure"}):
        raise _contract_error("field operand contains unknown keys", component_id=component_id)
    field = operand["field"]
    if not isinstance(field, str):
        raise _contract_error("constraint field must be a string", component_id=component_id)
    if field not in values:
        raise _contract_error(
            f"constraint references an unavailable input field: {field}",
            component_id=component_id,
        )
    measure = operand.get("measure", "identity")
    if not isinstance(measure, str) or measure not in MEASURES:
        raise _contract_error(
            f"unknown constraint measure: {measure!r}",
            component_id=component_id,
        )
    return _MEASURE_DISPATCH[measure](values[field], component_id=component_id)


def _compare(
    operator: str,
    left: Any,
    right: Any,
    *,
    component_id: str | None,
) -> bool:
    if operator not in OPERATORS:
        raise _contract_error(
            f"unknown constraint operator: {operator!r}",
            component_id=component_id,
        )
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
            raise _contract_error(
                f"operator {operator!r} requires a set-like right operand",
                component_id=component_id,
            )
        if operator == "in_set":
            return left in right
        return left not in right
    except (TypeError, ValueError) as exc:
        raise _contract_error(
            f"operator {operator!r} is undefined for the supplied operands",
            component_id=component_id,
        ) from exc


def _evaluate_expression(
    expression: Any,
    values: Mapping[str, Any],
    *,
    depth: int,
    component_id: str | None,
) -> bool:
    if depth > MAX_EXPRESSION_DEPTH:
        raise _contract_error(
            f"constraint expression exceeds maximum depth {MAX_EXPRESSION_DEPTH}",
            component_id=component_id,
        )
    if not isinstance(expression, Mapping) or any(
        not isinstance(key, str) for key in expression
    ):
        raise _contract_error("constraint expression must be an object", component_id=component_id)

    keys = set(expression)
    if keys == {"all"} or keys == {"any"}:
        branch = "all" if "all" in expression else "any"
        children = expression[branch]
        if not isinstance(children, list) or not children:
            raise _contract_error(
                f"constraint '{branch}' must contain a non-empty list",
                component_id=component_id,
            )
        if branch == "all":
            for child in children:
                if not _evaluate_expression(
                    child,
                    values,
                    depth=depth + 1,
                    component_id=component_id,
                ):
                    return False
            return True
        for child in children:
            if _evaluate_expression(
                child,
                values,
                depth=depth + 1,
                component_id=component_id,
            ):
                return True
        return False

    if keys != {"left", "op", "right"}:
        raise _contract_error(
            "comparison expression must contain exactly 'left', 'op', and 'right'",
            component_id=component_id,
        )
    operator = expression["op"]
    if not isinstance(operator, str):
        raise _contract_error("constraint operator must be a string", component_id=component_id)
    left = _resolve_operand(expression["left"], values, component_id=component_id)
    right = _resolve_operand(expression["right"], values, component_id=component_id)
    return _compare(operator, left, right, component_id=component_id)


def evaluate_expression(
    expression: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    component_id: str | None = None,
) -> bool:
    """Evaluate one validated v0 constraint expression."""

    return _evaluate_expression(expression, values, depth=1, component_id=component_id)


def _effective_values(
    guidance: Mapping[str, Any],
    supplied: Mapping[str, Any],
    *,
    component_id: str,
) -> dict[str, Any]:
    values = dict(supplied)
    defaults_raw = guidance.get("allowed_defaults", [])
    questions_raw = guidance.get("required_questions", [])
    if not isinstance(defaults_raw, list) or not isinstance(questions_raw, list):
        raise _contract_error(
            "allowed_defaults and required_questions must be lists",
            component_id=component_id,
        )

    defaults: dict[str, Mapping[str, Any]] = {}
    for item in defaults_raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("field"), str):
            raise _contract_error("invalid allowed_default record", component_id=component_id)
        field = item["field"]
        if "value" not in item:
            raise _contract_error(
                f"allowed default for {field!r} has no value",
                component_id=component_id,
            )
        defaults[field] = item

    missing: list[dict[str, Any]] = []
    questions: list[Mapping[str, Any]] = []
    for question in questions_raw:
        if not isinstance(question, Mapping):
            raise _contract_error("invalid required_question record", component_id=component_id)
        questions.append(question)
        field = question.get("resolves_to")
        if not isinstance(field, str):
            raise _contract_error(
                "required_question resolves_to must be a string",
                component_id=component_id,
            )
        if field in values:
            continue
        default_rule = defaults.get(field)
        question_default = question.get("default")
        if default_rule is None and question_default is None:
            missing.append(
                {
                    "id": question.get("id"),
                    "field": field,
                    "ask": question.get("ask"),
                    "why": question.get("why"),
                }
            )
    if missing:
        fields = ", ".join(str(item["field"]) for item in missing)
        raise AmbiguousInput(
            f"required convention-bearing input is missing: {fields}",
            component_id=component_id,
            details={"questions": missing},
        )

    for question in questions:
        field = question["resolves_to"]
        if field in values:
            continue
        default_rule = defaults.get(field)
        if default_rule is not None:
            only_if = default_rule.get("only_if")
            if only_if is None or evaluate_expression(
                only_if,
                values,
                component_id=component_id,
            ):
                values[field] = default_rule["value"]
                continue
        question_default = question.get("default")
        if question_default is not None:
            values[field] = question_default
            continue
        missing.append(
            {
                "id": question.get("id"),
                "field": field,
                "ask": question.get("ask"),
                "why": question.get("why"),
            }
        )

    if missing:
        fields = ", ".join(str(item["field"]) for item in missing)
        raise AmbiguousInput(
            f"required convention-bearing input is missing: {fields}",
            component_id=component_id,
            details={"questions": missing},
        )

    for field, default_rule in defaults.items():
        if field in values:
            continue
        only_if = default_rule.get("only_if")
        if only_if is None or evaluate_expression(
            only_if,
            values,
            component_id=component_id,
        ):
            values[field] = default_rule["value"]
    return values


def evaluate_constraints(
    guidance: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    component_id: str,
) -> tuple[Violation, ...]:
    """Evaluate every guidance rule and return all triggered violations."""

    constraints = guidance.get("constraints", [])
    if not isinstance(constraints, list):
        raise _contract_error("guidance constraints must be a list", component_id=component_id)
    violations: list[Violation] = []
    seen: set[str] = set()
    for rule in constraints:
        if not isinstance(rule, Mapping):
            raise _contract_error("constraint record must be an object", component_id=component_id)
        rule_id = rule.get("id")
        severity = rule.get("severity")
        message = rule.get("message")
        expression = rule.get("when")
        if not isinstance(rule_id, str) or not rule_id:
            raise _contract_error(
                "constraint id must be a non-empty string",
                component_id=component_id,
            )
        if rule_id in seen:
            raise _contract_error(f"duplicate constraint id: {rule_id}", component_id=component_id)
        seen.add(rule_id)
        if severity not in {"blocking", "warning"}:
            raise _contract_error(
                f"constraint {rule_id!r} has invalid severity",
                component_id=component_id,
            )
        if not isinstance(message, str) or not message:
            raise _contract_error(
                f"constraint {rule_id!r} has no message",
                component_id=component_id,
            )
        if expression is None:
            raise _contract_error(
                f"constraint {rule_id!r} has no expression",
                component_id=component_id,
            )
        if _evaluate_expression(expression, values, depth=1, component_id=component_id):
            violations.append(
                Violation(
                    rule=rule_id,
                    severity=severity,
                    message=message,
                )
            )
    return tuple(violations)


def preflight(component_id: str, /, **kwargs: Any) -> tuple[Violation, ...]:
    """Evaluate a component's enforceable guidance before calculation.

    Warning violations are returned.  All triggered blocking violations are raised together as a
    ``DomainError``.  The evaluator reads contract data only; it never imports component code.
    """

    record = component_record(component_id)
    guidance = record.metadata.get("guidance")
    if not isinstance(guidance, Mapping):
        raise _contract_error(
            "contract guidance must be an object",
            component_id=component_id,
        )
    values = _effective_values(guidance, kwargs, component_id=component_id)
    violations = evaluate_constraints(guidance, values, component_id=component_id)
    blocking = tuple(item for item in violations if item.severity == "blocking")
    if blocking:
        raise DomainError(
            "; ".join(item.message for item in blocking),
            component_id=component_id,
            details={
                "violations": [item.model_dump(mode="json") for item in blocking],
            },
        )
    return tuple(item for item in violations if item.severity == "warning")


__all__ = [
    "MAX_EXPRESSION_DEPTH",
    "MEASURES",
    "OPERATORS",
    "evaluate_constraints",
    "evaluate_expression",
    "preflight",
]
