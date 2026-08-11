"""Stable executable-evidence projection of Pydantic input validation errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise AssertionError(f"{label} must be a string-keyed object")
    return value


def _json_pointer(location: tuple[int | str, ...]) -> str:
    return "".join(
        f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in location
    )


def input_validation_issues(error: ValidationError) -> tuple[tuple[str, str], ...]:
    """Return the stable, closed evidence projection of Pydantic input errors."""

    issues = (
        (_json_pointer(tuple(issue["loc"])), str(issue["type"]))
        for issue in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    )
    return tuple(sorted(issues))


def expected_input_validation_issues(
    expectation: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Parse and canonically order authored input-validation issues."""

    raw_issues = expectation.get("issues")
    if not isinstance(raw_issues, list) or not raw_issues:
        raise AssertionError("input_validation_error expectations require non-empty issues")
    issues: list[tuple[str, str]] = []
    for raw_issue in raw_issues:
        issue = _as_mapping(raw_issue, label="input validation issue")
        path = issue.get("path")
        error_type = issue.get("type")
        if not isinstance(path, str) or not isinstance(error_type, str):
            raise AssertionError("input validation issues require string path and type")
        issues.append((path, error_type))
    if len(set(issues)) != len(issues):
        raise AssertionError("input validation issues must be unique")
    return tuple(sorted(issues))


__all__ = ["expected_input_validation_issues", "input_validation_issues"]
