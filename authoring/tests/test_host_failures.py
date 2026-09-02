"""Closed-vocabulary and redaction tests for transport-neutral host failures."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from defined_quant.host_failures import (
    HOST_FAILURE_SPECS,
    HOST_SUCCESS_TEXT,
    OPERATION_ERROR_DEFAULT_CODES,
    HostError,
    HostFailure,
    HostFailureCode,
    HostFailureException,
    HostOutcome,
    Trust,
    TrustLabel,
    host_failure,
    host_success,
    map_operation_error,
    map_operation_failure,
    tool_success_result_projection,
    trust_notice,
)
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    OperationError,
    OperationErrorCode,
    OperationFailure,
    OperationRequest,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
FAILURE_FIXTURE = ROOT / "docs" / "local_mcp" / "host_failures.json"
COMPONENT = ComponentRef(
    id="dq.market_data.simple_return",
    version="0.3.4",
    subject_hash="a" * 64,
)


def _fixture() -> dict[str, Any]:
    value = json.loads(FAILURE_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _valid_details(code: HostFailureCode) -> dict[str, Any]:
    if code in {
        HostFailureCode.INVALID_TOOL_REQUEST,
        HostFailureCode.MISSING_CONVENTION,
        HostFailureCode.INVALID_FIELD_MAPPING,
        HostFailureCode.INVALID_COMPONENT_INPUT,
    }:
        return {"fields": []}
    if code is HostFailureCode.UNSUPPORTED_HOST_SCHEMA:
        return {"requested_version": 2}
    if code is HostFailureCode.UNSUPPORTED_PROTOCOL_VERSION:
        return {"requested_version": "9.0.0"}
    if code is HostFailureCode.UNSUPPORTED_REFERENCE_VERSION:
        return {"requested_version": "v2"}
    if code in {
        HostFailureCode.METHOD_NOT_FOUND,
        HostFailureCode.METHOD_IDENTITY_MISMATCH,
    }:
        return {"method_id": "dq.market_data.simple_return"}
    if code in {
        HostFailureCode.COMPONENT_NOT_FOUND,
        HostFailureCode.COMPONENT_IDENTITY_MISMATCH,
        HostFailureCode.COMPONENT_REFUSED,
        HostFailureCode.COMPONENT_FAILED,
        HostFailureCode.OUTPUT_VALIDATION_FAILED,
    }:
        return {"component_id": COMPONENT.id}
    if code is HostFailureCode.INCOMPATIBLE_PORTS:
        return {
            "differences": [
                {
                    "dimension": "convention",
                    "producer_value": "simple_periodic_return",
                    "consumer_value": "log_periodic_return",
                }
            ]
        }
    if code is HostFailureCode.INVALID_DATASET:
        return {"finding_codes": ["missing_value"]}
    if code is HostFailureCode.INPUT_LIMIT_EXCEEDED:
        return {"limit_name": "dataset_rows", "maximum": 40_000, "actual": 40_001}
    if code is HostFailureCode.COMPONENT_CONTRACT_ERROR:
        return {}
    if code is HostFailureCode.RESULT_LIMIT_EXCEEDED:
        return {
            "limit_name": "operation_bundle_bytes",
            "maximum": 134_217_728,
            "actual": 134_217_729,
        }
    if code is HostFailureCode.ARTIFACT_NOT_FOUND:
        return {"artifact_id": "returns_chart"}
    return {}


def _operation_request(component: ComponentRef = COMPONENT) -> OperationRequest:
    return OperationRequest(
        component=component,
        input={"prices": [100.0, 101.0], "price_kind": "adjusted"},
        provenance=CallerProvenance(
            source_kind="synthetic",
            interpretation_method="caller_structured",
            label="Host failure test.",
        ),
    )


def _operation_error(
    code: OperationErrorCode,
    *,
    component: ComponentRef | None = COMPONENT,
    details: dict[str, Any] | None = None,
) -> OperationError:
    return OperationError(
        code=code,
        message="adversarial runtime text /private/secret raw-value",
        component=component,
        details=details or {},
    )


def test_static_vocabulary_matches_the_authoritative_fixture_exactly() -> None:
    fixture = _fixture()
    host_codes = fixture["host_codes"]

    assert [code.value for code in HostFailureCode] == [item["code"] for item in host_codes]
    assert [outcome.value for outcome in HostOutcome] == fixture["outcomes"]
    assert set(HOST_FAILURE_SPECS) == set(HostFailureCode)

    for item in host_codes:
        code = HostFailureCode(item["code"])
        spec = HOST_FAILURE_SPECS[code]
        assert spec.outcome.value == item["outcome"]
        assert spec.message == item["message"]
        assert spec.retry_allowed is item["retry_allowed"]
        assert spec.retry_condition == item["retry_condition"]


@pytest.mark.parametrize("code", list(HostFailureCode))
def test_every_host_code_builds_only_its_fixed_closed_failure(code: HostFailureCode) -> None:
    failure = host_failure(code, details=_valid_details(code))
    spec = HOST_FAILURE_SPECS[code]

    assert failure.host_schema_version == 1
    assert failure.outcome is spec.outcome
    assert failure.error.code is code
    assert failure.error.message == spec.message
    assert failure.error.retry_allowed is spec.retry_allowed
    assert failure.error.details == _valid_details(code)
    assert failure.trust == trust_notice(TrustLabel.NO_VERIFIED_RESULT)


def test_fixed_message_outcome_trust_and_details_cannot_be_forged() -> None:
    with pytest.raises(ValidationError):
        HostError(
            code=HostFailureCode.INTERNAL_FAILURE,
            message="leaked /private/path",
            retry_allowed=False,
            details={},
        )
    with pytest.raises(ValidationError):
        Trust(label=TrustLabel.NO_VERIFIED_RESULT, statement="Trust me.")

    error = host_failure(HostFailureCode.INTERNAL_FAILURE).error
    with pytest.raises(ValidationError):
        HostFailure(
            outcome=HostOutcome.REFUSED,
            error=error,
            trust=trust_notice(),
        )
    with pytest.raises(ValidationError):
        host_failure(HostFailureCode.COMPONENT_NOT_FOUND)
    with pytest.raises(ValidationError):
        host_failure(
            HostFailureCode.INTERNAL_FAILURE,
            details={"path": "/private/path"},
        )


def test_host_envelopes_are_recursively_immutable_and_tool_projection_is_exact() -> None:
    failure = host_failure(
        HostFailureCode.INVALID_TOOL_REQUEST,
        details={"fields": ["price_kind"]},
    )
    with pytest.raises(TypeError):
        failure.error.details["path"] = "/private/secret"
    fields = failure.error.details["fields"]
    assert isinstance(fields, list)
    with pytest.raises(TypeError):
        fields.append("unknown")

    success = host_success(
        {"nested": {"items": [1, 2]}},
        trust=TrustLabel.UNVERIFIED_CALLER_DATA,
    )
    nested = success.data["nested"]
    assert isinstance(nested, dict)
    items = nested["items"]
    assert isinstance(items, list)
    with pytest.raises(TypeError):
        items.append(3)

    assert tool_success_result_projection(
        {"nested": {"items": [1, 2]}},
        trust=TrustLabel.UNVERIFIED_CALLER_DATA,
    ) == {
        "content": [{"type": "text", "text": HOST_SUCCESS_TEXT}],
        "structuredContent": success.model_dump(mode="json"),
        "isError": False,
    }


@pytest.mark.parametrize(
    ("code", "details"),
    [
        (HostFailureCode.INVALID_TOOL_REQUEST, {"fields": ["é" * 121]}),
        (HostFailureCode.INVALID_TOOL_REQUEST, {"fields": ["field"] * 33}),
        (HostFailureCode.UNSUPPORTED_HOST_SCHEMA, {"requested_version": True}),
        (
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            {"requested_version": 9_007_199_254_740_992},
        ),
        (HostFailureCode.UNSUPPORTED_PROTOCOL_VERSION, {"requested_version": "é" * 65}),
        (HostFailureCode.UNSUPPORTED_REFERENCE_VERSION, {"requested_version": "version2"}),
        (HostFailureCode.COMPONENT_NOT_FOUND, {"component_id": "dq.market_data." + "a" * 230}),
        (HostFailureCode.INVALID_DATASET, {"finding_codes": ["Uppercase"]}),
        (
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            {"limit_name": "arbitrary", "maximum": 1, "actual": 2},
        ),
        (
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            {"limit_name": ["dataset_rows"], "maximum": 1, "actual": 2},
        ),
        (
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            {"limit_name": "operation_bundle_bytes", "maximum": -1, "actual": 2},
        ),
        (HostFailureCode.ARTIFACT_NOT_FOUND, {"artifact_id": "../secret"}),
        (HostFailureCode.INCOMPATIBLE_PORTS, {"differences": [{}]}),
        (
            HostFailureCode.INCOMPATIBLE_PORTS,
            {
                "differences": [
                    {
                        "dimension": "convention",
                        "producer_value": "simple_periodic_return",
                        "consumer_value": "log_periodic_return",
                    }
                ]
                * 9
            },
        ),
    ],
)
def test_details_enforce_closed_byte_count_and_value_limits(
    code: HostFailureCode,
    details: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        host_failure(code, details=details)


def test_exception_carries_only_a_fixed_safe_failure() -> None:
    error = HostFailureException(HostFailureCode.RECORD_CORRUPT)

    assert error.code is HostFailureCode.RECORD_CORRUPT
    assert str(error) == HOST_FAILURE_SPECS[HostFailureCode.RECORD_CORRUPT].message
    assert error.failure == host_failure(HostFailureCode.RECORD_CORRUPT)


def test_operation_mapping_is_exhaustive_and_matches_fixture_defaults() -> None:
    fixture = _fixture()
    raw_mappings = fixture["operation_error_mappings"]
    expected_defaults = {
        OperationErrorCode(item["operation_error_code"]): HostFailureCode(
            item.get("host_code", item.get("default_host_code"))
        )
        for item in raw_mappings
    }

    assert set(OPERATION_ERROR_DEFAULT_CODES) == set(OperationErrorCode)
    assert OPERATION_ERROR_DEFAULT_CODES == expected_defaults


@pytest.mark.parametrize("code", list(OperationErrorCode))
def test_every_operation_error_projects_to_its_frozen_default(code: OperationErrorCode) -> None:
    failure = map_operation_error(
        _operation_error(code),
        validated_request=_operation_request(),
        component_input_fields={"prices", "price_kind"},
    )
    expected = OPERATION_ERROR_DEFAULT_CODES[code]

    assert failure.error.code is expected
    assert failure.error.message == HOST_FAILURE_SPECS[expected].message
    serialized = json.dumps(failure.model_dump(mode="json"), ensure_ascii=False)
    assert "adversarial runtime text" not in serialized
    assert "/private/secret" not in serialized
    assert "raw-value" not in serialized


def test_invalid_component_input_projection_is_ordered_deduplicated_and_schema_bound() -> None:
    error = _operation_error(
        OperationErrorCode.INVALID_COMPONENT_INPUT,
        details={
            "errors": [
                {"loc": ["price_kind"], "msg": "/private/secret"},
                {"loc": ["unknown"], "msg": "raw-value"},
                {"loc": ["prices", 0]},
                {"loc": ["price_kind"]},
                {"loc": [0]},
                {"loc": "prices"},
            ]
        },
    )

    failure = map_operation_error(
        error,
        component_input_fields={"prices", "price_kind"},
    )

    assert failure.error.code is HostFailureCode.INVALID_COMPONENT_INPUT
    assert failure.error.details == {"fields": ["price_kind", "prices"]}


def test_ambiguous_input_projects_only_safe_nonempty_questions_and_caps_at_32() -> None:
    field_names = [f"field_{index}" for index in range(40)]
    questions = [
        *({"field": field} for field in field_names),
        {"field": "field_0"},
        {"field": "unknown"},
        {"field": "/private/secret"},
    ]
    error = _operation_error(
        OperationErrorCode.COMPONENT_REFUSED,
        details={
            "component_error": {
                "code": "ambiguous_input",
                "message": "raw-value",
                "details": {"questions": questions},
            }
        },
    )

    failure = map_operation_error(error, component_input_fields=set(field_names))

    assert failure.error.code is HostFailureCode.MISSING_CONVENTION
    assert failure.error.details == {"fields": field_names[:32]}
    assert failure.outcome is HostOutcome.NEEDS_INFORMATION


@pytest.mark.parametrize(
    "component_error",
    [
        {"code": "domain_error", "details": {"questions": [{"field": "price_kind"}]}},
        {"code": "ambiguous_input", "details": {"questions": []}},
        {"code": "ambiguous_input", "details": {"questions": [{"field": "unknown"}]}},
        {"code": "ambiguous_input", "details": {"questions": "price_kind"}},
    ],
)
def test_malformed_or_non_ambiguous_refusals_keep_component_refused(
    component_error: dict[str, Any],
) -> None:
    failure = map_operation_error(
        _operation_error(
            OperationErrorCode.COMPONENT_REFUSED,
            details={"component_error": component_error},
        ),
        component_input_fields={"price_kind"},
    )

    assert failure.error.code is HostFailureCode.COMPONENT_REFUSED
    assert failure.error.details == {"component_id": COMPONENT.id}


def test_component_id_projection_prefers_request_and_fails_closed_when_absent() -> None:
    other = ComponentRef(
        id="dq.market_data.log_return",
        version="1.0.0",
        subject_hash="b" * 64,
    )
    projected = map_operation_error(
        _operation_error(OperationErrorCode.COMPONENT_NOT_FOUND, component=other),
        validated_request=_operation_request(),
    )
    absent = map_operation_error(
        _operation_error(OperationErrorCode.COMPONENT_NOT_FOUND, component=None)
    )

    assert projected.error.details == {"component_id": COMPONENT.id}
    assert absent.error.code is HostFailureCode.INTERNAL_FAILURE
    assert absent.error.details == {}


def test_optional_component_id_is_omitted_and_operation_failure_wrapper_matches() -> None:
    error = _operation_error(OperationErrorCode.COMPONENT_CONTRACT_ERROR, component=None)
    direct = map_operation_error(error)
    wrapped = map_operation_failure(OperationFailure(error=error))

    assert direct == wrapped
    assert direct.error.code is HostFailureCode.COMPONENT_CONTRACT_ERROR
    assert direct.error.details == {}


def test_operation_projection_refuses_protocol_valid_but_host_overlong_component_id() -> None:
    overlong = ComponentRef(
        id="dq.market_data." + "a" * 230,
        version="1.0.0",
        subject_hash="c" * 64,
    )
    failure = map_operation_error(
        _operation_error(OperationErrorCode.COMPONENT_EXECUTION_FAILED, component=overlong)
    )

    assert failure.error.code is HostFailureCode.INTERNAL_FAILURE
    assert failure.error.details == {}


def test_host_failure_runtime_has_no_mcp_import() -> None:
    path = ROOT / "shared" / "host_failures.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "mcp" not in roots
    assert "fastmcp" not in roots
