"""Contract tests for shared datapoint-level lineage models."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from defined_quant.types import ComponentOutput, Derivation, InputRef, OutputRef, Unit
from pydantic import Field, ValidationError


class ExampleOutput(ComponentOutput):
    values: tuple[float, ...] = Field(min_length=1)


class ScalarOutput(ComponentOutput):
    value: float


class BooleanOutput(ComponentOutput):
    flag: bool


class TextOutput(ComponentOutput):
    label: str


class BooleanSeriesOutput(ComponentOutput):
    flags: tuple[bool, ...]


def _derivation(
    *,
    output_field: str = "values",
    output_index: int = 0,
    value: float = 2.0,
) -> Derivation:
    return Derivation(
        output=OutputRef(field=output_field, index=output_index),
        inputs=(InputRef(field="source_values", index=0),),
        expression="source_values[0] * 2",
        value=value,
    )


def _payload() -> dict[str, Any]:
    return {
        "component_id": "dq.test.example",
        "version": "1.0.0",
        "subject_hash": "0" * 64,
        "unit": Unit.DECIMAL,
        "values": [2.0, 3.0],
        "derivations": [_derivation().model_dump(mode="python")],
    }


def test_valid_derivation_is_closed_frozen_and_serializable() -> None:
    output = ExampleOutput.model_validate(_payload())

    assert output.derivations[0].output == OutputRef(field="values", index=0)
    assert output.derivations[0].inputs[0].citation_id is None
    with pytest.raises(ValidationError):
        OutputRef.model_validate({"field": "values", "index": 0, "extra": True})
    with pytest.raises(ValidationError):
        output.derivations[0].__setattr__("value", 3.0)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"output": {"field": "missing", "index": 0}}, "does not exist"),
        ({"output": {"field": "values", "index": 2}}, "out of range"),
        ({"value": 9.0}, "must equal"),
        ({"output": {"field": "unit", "index": 0}}, "numeric value"),
    ],
)
def test_output_rejects_dangling_or_false_derivations(
    mutation: dict[str, Any],
    message: str,
) -> None:
    payload = _payload()
    payload["derivations"][0].update(mutation)

    with pytest.raises(ValidationError, match=message):
        ExampleOutput.model_validate(payload)


def test_output_rejects_duplicate_output_locations() -> None:
    payload = _payload()
    payload["derivations"].append(deepcopy(payload["derivations"][0]))

    with pytest.raises(ValidationError, match="output locations must be unique"):
        ExampleOutput.model_validate(payload)


def test_scalar_derivation_uses_index_zero_and_exact_value() -> None:
    payload = {
        "component_id": "dq.test.scalar",
        "version": "1.0.0",
        "subject_hash": "0" * 64,
        "unit": Unit.DECIMAL,
        "value": 2.0,
        "derivations": [
            _derivation(output_field="value").model_dump(mode="python")
        ],
    }

    output = ScalarOutput.model_validate(payload)
    assert output.derivations[0].output == OutputRef(field="value", index=0)

    payload["derivations"][0]["output"]["index"] = 1
    with pytest.raises(ValidationError, match="scalar derivation output.*index 0"):
        ScalarOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("output_model", "field", "field_value", "derivation_value"),
    [
        (BooleanOutput, "flag", True, 1.0),
        (TextOutput, "label", "1.0", 1.0),
        (BooleanSeriesOutput, "flags", (True,), 1.0),
    ],
)
def test_scalar_derivations_reject_boolean_and_nonnumeric_targets(
    output_model: type[ComponentOutput],
    field: str,
    field_value: object,
    derivation_value: float,
) -> None:
    payload = {
        "component_id": "dq.test.scalar",
        "version": "1.0.0",
        "subject_hash": "0" * 64,
        "unit": Unit.DECIMAL,
        field: field_value,
        "derivations": [
            _derivation(
                output_field=field,
                value=derivation_value,
            ).model_dump(mode="python")
        ],
    }

    with pytest.raises(ValidationError, match="numeric value"):
        output_model.model_validate(payload)


def test_derivation_rejects_duplicate_inputs_blank_expression_and_nonfinite_value() -> None:
    source = InputRef(field="source_values", index=0)
    with pytest.raises(ValidationError, match="input locations must be unique"):
        Derivation(
            output=OutputRef(field="values", index=0),
            inputs=(source, source.model_copy(update={"citation_id": "source_cell"})),
            expression="source_values[0]",
            value=2.0,
        )
    with pytest.raises(ValidationError, match="must not be blank"):
        Derivation(
            output=OutputRef(field="values", index=0),
            inputs=(source,),
            expression="   ",
            value=2.0,
        )
    with pytest.raises(ValidationError):
        Derivation(
            output=OutputRef(field="values", index=0),
            inputs=(source,),
            expression="source_values[0]",
            value=float("inf"),
        )
