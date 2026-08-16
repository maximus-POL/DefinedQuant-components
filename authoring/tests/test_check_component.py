"""Tests for the generic callable contract enforced by the catalog checker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from defined_quant.types import ComponentOutput
from defined_quant.validation import MEASURES
from defined_quant_protocol import (
    PortCardinality,
    PortConcept,
    PortConvention,
    PortDirection,
    PortFrequency,
    PortOrdering,
    PortProvenanceRequirement,
    PortShape,
    PortUnit,
    semantic_port_metadata,
)
from pydantic import BaseModel, Field

from authoring.check_component import (
    _validate_callable_signature,
    _validate_discovery,
    _validate_formula_surfaces,
    _validate_guidance,
    _validate_output_model,
    _validate_semantic_port_metadata,
)


class ExampleInputs(BaseModel):
    values: tuple[float, ...]
    label: str | None = None


INPUT_FIELDS = set(ExampleInputs.model_fields)


class ValidOutput(ComponentOutput):
    value: float


class InvalidOutput(BaseModel):
    value: float


def test_constraint_schema_measure_vocabulary_matches_evaluator() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "contract.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_measures = schema["$defs"]["operand"]["oneOf"][1]["properties"]["measure"][
        "enum"
    ]

    assert set(schema_measures) == MEASURES


def _port_metadata(
    direction: PortDirection,
    *,
    shape: PortShape = PortShape.ORDERED_SERIES,
    cardinality: PortCardinality = PortCardinality.ONE_OR_MORE,
) -> dict[str, object]:
    return semantic_port_metadata(
        direction=direction,
        concept=PortConcept.PERIODIC_RETURN_SERIES,
        unit=PortUnit.DECIMAL,
        shape=shape,
        cardinality=cardinality,
        convention=PortConvention.LOG_PERIODIC_RETURN,
        ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
        frequency=PortFrequency.INHERITED,
        provenance_requirement=PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND,
    )


def test_output_must_extend_the_shared_component_envelope() -> None:
    _validate_output_model("example.component", ValidOutput)

    with pytest.raises(ValueError, match="must extend defined_quant.types.ComponentOutput"):
        _validate_output_model("example.component", InvalidOutput)


def test_component_models_require_closed_directional_semantic_ports() -> None:
    class ValidInputPort(BaseModel):
        values: tuple[float, ...] = Field(
            min_length=1,
            json_schema_extra=_port_metadata(PortDirection.INPUT)
        )

    class MissingPort(BaseModel):
        values: tuple[float, ...]

    class AdHocPort(BaseModel):
        values: tuple[float, ...] = Field(
            json_schema_extra={"unit": "decimal", "convention": "log_return"}
        )

    class ScalarClaimingSeries(BaseModel):
        value: float = Field(json_schema_extra=_port_metadata(PortDirection.INPUT))

    class SeriesClaimingScalar(BaseModel):
        values: tuple[float, ...] = Field(
            min_length=1,
            json_schema_extra=_port_metadata(
                PortDirection.INPUT,
                shape=PortShape.SCALAR,
                cardinality=PortCardinality.EXACTLY_ONE,
            )
        )

    assert _validate_semantic_port_metadata(
        ValidInputPort,
        direction="input",
    ) == []
    assert any(
        "at least one input semantic port" in error
        for error in _validate_semantic_port_metadata(MissingPort, direction="input")
    )
    assert any(
        "missing 'x-defined-quant-port'" in error
        for error in _validate_semantic_port_metadata(AdHocPort, direction="input")
    )
    assert any(
        "expected 'output'" in error
        for error in _validate_semantic_port_metadata(
            ValidInputPort,
            direction="output",
        )
    )
    assert any(
        "contradicts its Pydantic field shape 'scalar'" in error
        for error in _validate_semantic_port_metadata(
            ScalarClaimingSeries,
            direction="input",
        )
    )
    assert any(
        "contradicts its Pydantic field shape 'ordered_series'" in error
        for error in _validate_semantic_port_metadata(
            SeriesClaimingScalar,
            direction="input",
        )
    )


def test_callable_accepts_every_input_as_an_explicit_keyword() -> None:
    def component(*, values: tuple[float, ...], label: str | None = None) -> None:
        del values, label

    _validate_callable_signature(component, INPUT_FIELDS)


def test_callable_may_accept_inputs_through_var_keyword() -> None:
    def component(**kwargs: object) -> None:
        del kwargs

    _validate_callable_signature(component, INPUT_FIELDS)


def test_callable_rejects_an_input_that_cannot_be_passed_by_keyword() -> None:
    def component(*, values: tuple[float, ...]) -> None:
        del values

    with pytest.raises(ValueError, match="not accepted as keywords: label"):
        _validate_callable_signature(component, INPUT_FIELDS)


def test_callable_rejects_required_parameters_absent_from_inputs() -> None:
    def component(
        *,
        values: tuple[float, ...],
        label: str | None = None,
        hidden_convention: str,
    ) -> None:
        del values, label, hidden_convention

    with pytest.raises(ValueError, match="absent from Inputs: hidden_convention"):
        _validate_callable_signature(component, INPUT_FIELDS)


def test_callable_rejects_positional_only_parameters() -> None:
    def component(
        values: tuple[float, ...],
        /,
        *,
        label: str | None = None,
    ) -> None:
        del values, label

    with pytest.raises(ValueError, match="positional-only parameters are not allowed: values"):
        _validate_callable_signature(component, INPUT_FIELDS)


def test_discovery_rejects_normalized_duplicates() -> None:
    errors = _validate_discovery(
        Path("contract.yaml"),
        {
            "aliases": ["Arithmetic Return", " arithmetic   return "],
            "intents": ["calculate_return"],
            "input_concepts": ["price_series"],
            "output_concepts": ["return_series"],
        },
    )

    assert any("duplicates after normalization" in error for error in errors)


def test_discovery_concepts_are_bounded_identifiers() -> None:
    errors = _validate_discovery(
        Path("contract.yaml"),
        {
            "aliases": ["return"] * 17,
            "intents": ["Calculate Return"],
            "input_concepts": ["price-series"],
            "output_concepts": ["return_series"],
        },
    )

    assert any("between 1 and 16" in error for error in errors)
    assert sum("lower snake_case identifiers" in error for error in errors) == 2


def test_formula_surfaces_must_match_component_formula(tmp_path: Path) -> None:
    formula = "r = (current − previous) / previous"
    (tmp_path / "README.md").write_text(
        f"# Example\n\n## Formula\n\n`{formula}`\n\n## Output\n",
        encoding="utf-8",
    )

    assert _validate_formula_surfaces(
        tmp_path,
        {"display": {"formula": formula}},
        formula,
    ) == []

    contract_errors = _validate_formula_surfaces(
        tmp_path,
        {"display": {"formula": "r = current / previous - 1"}},
        formula,
    )
    assert any("display.formula must exactly match" in error for error in contract_errors)

    (tmp_path / "README.md").write_text(
        "# Example\n\n## Formula\n\n`r = current / previous - 1`\n",
        encoding="utf-8",
    )
    readme_errors = _validate_formula_surfaces(
        tmp_path,
        {"display": {"formula": formula}},
        formula,
    )
    assert any("Formula section must include" in error for error in readme_errors)


def test_input_independent_warning_comparisons_are_rejected_as_disclosures() -> None:
    guidance = {
        "constraints": [
            {
                "id": "always_visible",
                "severity": "warning",
                "when": {
                    "all": [
                        {
                            "left": {"field": "values", "measure": "count"},
                            "op": "gt",
                            "right": {"value": 0},
                        },
                        {
                            "left": {"value": True},
                            "op": "eq",
                            "right": {"value": True},
                        },
                    ]
                },
                "message": "Always visible context.",
            }
        ],
        "required_questions": [],
        "allowed_defaults": [],
    }

    errors = _validate_guidance(
        Path("contract.yaml"),
        guidance,
        {"values"},
        {"values"},
        {},
    )

    assert any(
        "input-independent comparison" in error
        and "constant output context belongs in Output.disclosures" in error
        for error in errors
    )


def test_field_dependent_warning_constraints_remain_valid() -> None:
    guidance = {
        "constraints": [
            {
                "id": "empty_values",
                "severity": "warning",
                "when": {
                    "left": {"field": "values", "measure": "count"},
                    "op": "eq",
                    "right": {"value": 0},
                },
                "message": "The supplied values are empty.",
            }
        ],
        "required_questions": [],
        "allowed_defaults": [],
    }

    errors = _validate_guidance(
        Path("contract.yaml"),
        guidance,
        {"values"},
        {"values"},
        {},
    )

    assert errors == []
