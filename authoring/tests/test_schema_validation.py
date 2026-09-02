from __future__ import annotations

from types import MappingProxyType

import pytest
from defined_quant.schema_validation import (
    schema_is_supported,
    schema_matches,
    validate_instance,
)


def test_closed_schema_validation_is_strict_and_provider_neutral() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "prices": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "number", "exclusiveMinimum": 0},
            },
            "timestamps": {
                "anyOf": [
                    {"type": "array", "items": {"type": "string", "format": "date-time"}},
                    {"type": "null"},
                ]
            },
        },
        "required": ["prices"],
    }

    assert schema_is_supported(schema)
    assert schema_matches(
        {"prices": [100.0, 101.0], "timestamps": ["2026-01-01T00:00:00Z"]},
        schema,
    )
    assert not schema_matches({"prices": [100.0]}, schema)
    assert not schema_matches({"prices": [True, 101.0]}, schema)
    assert not schema_matches({"prices": [100.0, 101.0], "backend": "dq_native"}, schema)
    assert schema_matches(
        MappingProxyType({"prices": (100.0, 101.0), "timestamps": None}),
        schema,
    )


def test_schema_control_fields_remain_closed() -> None:
    schema = {"type": "object", "properties": {}, "required": [], "x-executable": "code"}

    assert not schema_is_supported(schema)
    with pytest.raises(ValueError, match="unsupported schema"):
        validate_instance({}, schema, name="method input")
