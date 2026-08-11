"""Regression tests for the executable evidence format and collector runtime."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest
from defined_quant.market_data.simple_return.component import Inputs, Output
from pydantic import BaseModel, Field

from authoring import evidence_runtime
from authoring.check_component import _evidence_test_ids, _validate_evidence
from authoring.evidence_runtime import (
    execute_numerical_case,
    load_evidence,
    materialize_fixture,
)

ROOT = Path(__file__).resolve().parents[2]
COMPONENT_DIR = ROOT / "categories" / "market_data" / "simple_return"
EVIDENCE_PATH = COMPONENT_DIR / "evidence.yaml"


class InputValidationInputs(BaseModel):
    values: tuple[float, ...] = Field(min_length=1)


class InputValidationOutput(BaseModel):
    result: float


def _record(evidence: dict[str, Any], section: str, record_id: str) -> dict[str, Any]:
    return next(record for record in evidence[section] if record["id"] == record_id)


def _schema_errors(evidence: dict[str, Any]) -> list[jsonschema.ValidationError]:
    schema = json.loads(
        (ROOT / "authoring" / "schemas" / "evidence.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator_class = jsonschema.validators.validator_for(schema)
    return list(validator_class(schema).iter_errors(evidence))


def test_reserved_fixtures_are_closed_and_materialize_exactly() -> None:
    value = materialize_fixture(
        {
            "values": [
                {"$float": "nan"},
                {"$float": "positive_infinity"},
                {"$float": "negative_infinity"},
            ],
            "repeated": {"$repeat": {"value": 3.5, "count": 3}},
        }
    )

    assert math.isnan(value["values"][0])
    assert value["values"][1:] == [math.inf, -math.inf]
    assert value["repeated"] == [3.5, 3.5, 3.5]
    with pytest.raises(ValueError, match="unsupported \\$float"):
        materialize_fixture({"$float": "not_a_number"})
    with pytest.raises(ValueError, match="between 1 and 10000"):
        materialize_fixture({"$repeat": {"value": 1, "count": 0}})
    with pytest.raises(ValueError, match="reserved \\$-prefixed"):
        materialize_fixture({"$invented": "dialect"})


@pytest.mark.parametrize("raw", ['{"value": NaN}', "value: .nan\n"])
def test_raw_non_finite_numbers_are_rejected(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "evidence.yaml"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="non-finite|non-standard JSON constant"):
        load_evidence(path)


def test_authored_input_is_the_input_that_executes() -> None:
    evidence = load_evidence(EVIDENCE_PATH)
    record = deepcopy(_record(evidence, "boundary_cases", "bc_001"))
    execute_numerical_case(COMPONENT_DIR, record)

    record["inputs"]["prices"] = [100.0, 102.0]
    with pytest.raises(AssertionError, match="not approximately"):
        execute_numerical_case(COMPONENT_DIR, record)


def test_authored_expectation_is_the_assertion_that_executes() -> None:
    evidence = load_evidence(EVIDENCE_PATH)
    record = deepcopy(_record(evidence, "boundary_cases", "bc_001"))
    record["expect"]["assertions"][0]["value"] = [0.02]

    with pytest.raises(AssertionError, match="not approximately"):
        execute_numerical_case(COMPONENT_DIR, record)


def test_schema_rejects_ad_hoc_expected_dialects() -> None:
    evidence = load_evidence(EVIDENCE_PATH)
    record = _record(evidence, "boundary_cases", "bc_016_at_visualization_limit")
    record["expected"] = {
        "return_counts": [500, 501],
        "visualization_counts": [1, 0],
        "full_returns_preserved": True,
    }

    errors = _schema_errors(evidence)

    assert errors
    assert any("expected" in error.message for error in errors)


def test_schema_accepts_only_closed_input_validation_issues() -> None:
    evidence = load_evidence(EVIDENCE_PATH)
    record = _record(evidence, "boundary_cases", "bc_001")
    record["expect"] = {
        "outcome": "input_validation_error",
        "issues": [{"path": "/prices", "type": "too_short"}],
    }

    assert not _schema_errors(evidence)

    record["expect"]["violation_ids"] = []
    assert _schema_errors(evidence)

    del record["expect"]["violation_ids"]
    record["expect"]["issues"][0]["message"] = "unstable prose is not evidence"
    assert _schema_errors(evidence)


def test_input_validation_expectation_compares_exact_structured_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evidence_runtime,
        "component_models",
        lambda _component_dir: (InputValidationInputs, InputValidationOutput),
    )
    monkeypatch.setattr(
        evidence_runtime,
        "load_component",
        lambda _component_dir: pytest.fail("invalid inputs must not execute the component"),
    )
    record: dict[str, Any] = {
        "inputs": {"values": []},
        "expect": {
            "outcome": "input_validation_error",
            "issues": [{"path": "/values", "type": "too_short"}],
        },
    }

    execute_numerical_case(COMPONENT_DIR, record)

    record["expect"]["issues"] = [{"path": "/other", "type": "too_short"}]
    with pytest.raises(AssertionError, match="do not equal"):
        execute_numerical_case(COMPONENT_DIR, record)


def test_input_validation_expectation_fails_when_inputs_are_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evidence_runtime,
        "component_models",
        lambda _component_dir: (InputValidationInputs, InputValidationOutput),
    )
    monkeypatch.setattr(evidence_runtime, "load_component", lambda _component_dir: object())

    with pytest.raises(AssertionError, match="Inputs were valid"):
        execute_numerical_case(
            COMPONENT_DIR,
            {
                "inputs": {"values": [1.0]},
                "expect": {
                    "outcome": "input_validation_error",
                    "issues": [{"path": "/values", "type": "too_short"}],
                },
            },
        )


def test_checker_rejects_pseudo_inputs_against_the_component_model() -> None:
    evidence = load_evidence(EVIDENCE_PATH)
    record = _record(evidence, "boundary_cases", "bc_016_at_visualization_limit")
    record["inputs"] = {
        "synthetic_constant_price_fixtures": [501, 502],
        "price_kind": "adjusted",
    }

    errors = _validate_evidence(
        EVIDENCE_PATH,
        evidence,
        COMPONENT_DIR,
        Inputs,
        set(Inputs.model_fields),
        set(Output.model_fields),
    )

    assert any("synthetic_constant_price_fixtures" in error for error in errors)


def test_checker_verifies_expected_input_validation_issues() -> None:
    record: dict[str, Any] = {
        "id": "invalid_values",
        "description": "Empty values fail the declared input cardinality.",
        "inputs": {"values": []},
        "expect": {
            "outcome": "input_validation_error",
            "issues": [{"path": "/values", "type": "too_short"}],
        },
    }
    evidence = {
        "known_answers": [],
        "invariants": [],
        "boundary_cases": [record],
        "cross_checks": [],
        "agent_cases": [],
    }

    errors = _validate_evidence(
        EVIDENCE_PATH,
        evidence,
        COMPONENT_DIR,
        InputValidationInputs,
        set(InputValidationInputs.model_fields),
        set(InputValidationOutput.model_fields),
    )
    assert not errors

    record["expect"]["issues"] = [{"path": "/values", "type": "missing"}]
    errors = _validate_evidence(
        EVIDENCE_PATH,
        evidence,
        COMPONENT_DIR,
        InputValidationInputs,
        set(InputValidationInputs.model_fields),
        set(InputValidationOutput.model_fields),
    )
    assert any("input validation issues" in error and "do not equal" in error for error in errors)


def test_blessing_names_every_generated_and_invariant_case() -> None:
    evidence = load_evidence(EVIDENCE_PATH)
    test_ids = _evidence_test_ids(evidence)

    assert "known_answer_ka_001" in test_ids
    assert "boundary_case_bc_016_at_visualization_limit" in test_ids
    assert "boundary_case_bc_016_above_visualization_limit" in test_ids
    assert "agent_case_missing_price_kind" in test_ids
    assert "test_evidence_inv_004" in test_ids
