"""Closed JSON Schema subset used at Defined Quant registry boundaries.

The registry intentionally accepts a small, deterministic subset instead of delegating
validation to adapter packages.  This keeps method and capability validation provider-neutral,
portable, and within the core distribution's Pydantic-only dependency boundary.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from defined_quant_protocol import canonical_json_bytes

_SUPPORTED_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "$schema",
        "additionalProperties",
        "anyOf",
        "const",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "x-defined-quant-port",
    }
)
_RFC3339_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$",
    re.ASCII,
)


def schema_is_supported(
    schema: Mapping[str, Any],
    root: Mapping[str, Any] | None = None,
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> bool:
    """Return whether ``schema`` uses only the closed executable subset."""

    root_schema = schema if root is None else root
    if set(schema) - _SUPPORTED_KEYS:
        return False
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            return False
        if reference in seen_refs:
            return False
        definitions = root_schema.get("$defs")
        if not isinstance(definitions, Mapping):
            return False
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping) or not schema_is_supported(
            target,
            root_schema,
            seen_refs=seen_refs | {reference},
        ):
            return False
    definitions = schema.get("$defs")
    if definitions is not None and (
        not isinstance(definitions, Mapping)
        or any(
            not isinstance(name, str)
            or not isinstance(value, Mapping)
            or not schema_is_supported(value, root_schema)
            for name, value in definitions.items()
        )
    ):
        return False
    branches = schema.get("anyOf")
    if branches is not None and (
        not isinstance(branches, list)
        or not branches
        or any(
            not isinstance(branch, Mapping)
            or not schema_is_supported(branch, root_schema)
            for branch in branches
        )
    ):
        return False
    declared_type = schema.get("type")
    if declared_type is not None and declared_type not in {
        "array",
        "boolean",
        "integer",
        "null",
        "number",
        "object",
        "string",
    }:
        return False
    properties = schema.get("properties")
    if properties is not None and (
        not isinstance(properties, Mapping)
        or any(
            not isinstance(field, str)
            or not isinstance(value, Mapping)
            or not schema_is_supported(value, root_schema)
            for field, value in properties.items()
        )
    ):
        return False
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or any(not isinstance(field, str) for field in required)
        or len(set(required)) != len(required)
    ):
        return False
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        return False
    items = schema.get("items")
    if items is not None and (
        not isinstance(items, Mapping) or not schema_is_supported(items, root_schema)
    ):
        return False
    format_name = schema.get("format")
    if format_name is not None and format_name != "date-time":
        return False
    pattern = schema.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            return False
        try:
            re.compile(pattern)
        except re.error:
            return False
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and (
            isinstance(schema[key], bool)
            or not isinstance(schema[key], int)
            or schema[key] < 0
        ):
            return False
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in schema and (
            isinstance(schema[key], bool)
            or not isinstance(schema[key], int | float)
            or not math.isfinite(float(schema[key]))
        ):
            return False
    for key in ("const", "default"):
        if key in schema:
            try:
                canonical_json_bytes(schema[key])
            except (TypeError, ValueError):
                return False
    allowed = schema.get("enum")
    if allowed is not None:
        try:
            if (
                not isinstance(allowed, list)
                or not allowed
                or len({canonical_json_bytes(item) for item in allowed}) != len(allowed)
            ):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _valid_datetime(value: str) -> bool:
    matched = _RFC3339_DATETIME.fullmatch(value)
    if matched is None:
        return False
    year, month, day, hour, minute, second = (
        int(matched.group(index)) for index in range(1, 7)
    )
    if year == 0 or month not in range(1, 13) or hour > 23 or minute > 59 or second > 59:
        return False
    month_lengths = (
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    if day == 0 or day > month_lengths[month - 1]:
        return False
    offset_hour = matched.group(8)
    offset_minute = matched.group(9)
    return not (
        offset_hour is not None
        and offset_minute is not None
        and (int(offset_hour) > 23 or int(offset_minute) > 59)
    )


def schema_matches(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any] | None = None,
) -> bool:
    """Validate one JSON value against the closed subset without coercion."""

    root_schema = schema if root is None else root
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definitions = root_schema.get("$defs")
        if not isinstance(definitions, Mapping):
            return False
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping) or not schema_matches(value, target, root_schema):
            return False
    branches = schema.get("anyOf")
    if isinstance(branches, list) and not any(
        isinstance(branch, Mapping) and schema_matches(value, branch, root_schema)
        for branch in branches
    ):
        return False
    if "const" in schema and canonical_json_bytes(value) != canonical_json_bytes(
        schema["const"]
    ):
        return False
    allowed = schema.get("enum")
    if isinstance(allowed, list) and canonical_json_bytes(value) not in {
        canonical_json_bytes(item) for item in allowed
    }:
        return False

    declared_type = schema.get("type")
    if declared_type == "null":
        return value is None
    if declared_type == "boolean":
        return isinstance(value, bool)
    if declared_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif declared_type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        if not math.isfinite(float(value)):
            return False
    elif declared_type == "string":
        if not isinstance(value, str):
            return False
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return False
        if schema.get("format") == "date-time" and not _valid_datetime(value):
            return False
    elif declared_type == "array":
        if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
            return False
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping) and any(
            not schema_matches(item, item_schema, root_schema) for item in value
        ):
            return False
    elif declared_type == "object":
        if not isinstance(value, Mapping):
            return False
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            return False
        if any(field not in value for field in required):
            return False
        if schema.get("additionalProperties") is False and any(
            field not in properties for field in value
        ):
            return False
        for field, item in value.items():
            property_schema = properties.get(field)
            if isinstance(property_schema, Mapping) and not schema_matches(
                item, property_schema, root_schema
            ):
                return False

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, int | float) and value < minimum:
            return False
        if isinstance(maximum, int | float) and value > maximum:
            return False
        if isinstance(exclusive_minimum, int | float) and value <= exclusive_minimum:
            return False
        if isinstance(exclusive_maximum, int | float) and value >= exclusive_maximum:
            return False
    return True


def validate_instance(value: Any, schema: Mapping[str, Any], *, name: str) -> None:
    """Raise a stable validation error for an unsupported schema or invalid value."""

    if not schema_is_supported(schema):
        raise ValueError(f"{name} uses an unsupported schema")
    if not schema_matches(value, schema):
        raise ValueError(f"{name} does not match its canonical schema")


__all__ = ["schema_is_supported", "schema_matches", "validate_instance"]
