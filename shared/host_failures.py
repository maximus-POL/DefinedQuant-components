"""Closed, transport-neutral host failures and operation-error projection."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from defined_quant._immutable_json import freeze_json_object
from defined_quant_protocol import (
    OperationError,
    OperationErrorCode,
    OperationFailure,
    OperationRequest,
    PortDifference,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_COMPONENT_ID = re.compile(r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REFERENCE_VERSION = re.compile(r"^v[0-9]{1,3}$")
HOST_SUCCESS_TEXT = "Defined Quant host outcome: ok; use structuredContent."


class HostOutcome(StrEnum):
    """Closed semantic outcomes returned by every host surface."""

    NEEDS_INFORMATION = "needs_information"
    REFUSED = "refused"
    FAILED = "failed"


class HostFailureCode(StrEnum):
    """Closed failure vocabulary shared by the service and future transports."""

    INVALID_TOOL_REQUEST = "invalid_tool_request"
    UNSUPPORTED_HOST_SCHEMA = "unsupported_host_schema"
    UNSUPPORTED_PROTOCOL_VERSION = "unsupported_protocol_version"
    UNSUPPORTED_REFERENCE_VERSION = "unsupported_reference_version"
    COMPONENT_NOT_FOUND = "component_not_found"
    COMPONENT_IDENTITY_MISMATCH = "component_identity_mismatch"
    MISSING_CONVENTION = "missing_convention"
    INCOMPATIBLE_PORTS = "incompatible_ports"
    UNSUPPORTED_BINDING = "unsupported_binding"
    MULTIPLE_NON_LITERAL_SOURCES = "multiple_non_literal_sources"
    INVALID_DATASET = "invalid_dataset"
    UNSUPPORTED_DATA_FORMAT = "unsupported_data_format"
    INPUT_ROOT_DENIED = "input_root_denied"
    UNSAFE_INPUT_PATH = "unsafe_input_path"
    INPUT_LIMIT_EXCEEDED = "input_limit_exceeded"
    INVALID_FIELD_MAPPING = "invalid_field_mapping"
    REFERENCE_NOT_FOUND = "reference_not_found"
    REFERENCE_SCOPE_DENIED = "reference_scope_denied"
    RECORD_CORRUPT = "record_corrupt"
    CACHE_FULL = "cache_full"
    RECORD_PUBLICATION_FAILED = "record_publication_failed"
    WORKER_TIMEOUT = "worker_timeout"
    WORKER_CANCELLED = "worker_cancelled"
    WORKER_CRASHED = "worker_crashed"
    WORKER_CAPACITY = "worker_capacity"
    WORKER_RESOURCE_LIMIT = "worker_resource_limit"
    COMPONENT_REFUSED = "component_refused"
    COMPONENT_FAILED = "component_failed"
    INVALID_COMPONENT_INPUT = "invalid_component_input"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    COMPONENT_CONTRACT_ERROR = "component_contract_error"
    ARTIFACT_PUBLICATION_FAILED = "artifact_publication_failed"
    RESULT_LIMIT_EXCEEDED = "result_limit_exceeded"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    INTERNAL_FAILURE = "internal_failure"


class TrustLabel(StrEnum):
    """Closed trust labels used by host responses."""

    CONTRACT_METADATA_ONLY = "contract_metadata_only"
    INSTALLED_SUBJECT_INSPECTED = "installed_subject_inspected"
    UNVERIFIED_CALLER_DATA = "unverified_caller_data"
    STRUCTURAL_COMPATIBILITY_ONLY = "structural_compatibility_only"
    UNMANAGED_EXECUTION = "unmanaged_execution"
    NO_VERIFIED_RESULT = "no_verified_result"


_TRUST_STATEMENTS: Mapping[TrustLabel, str] = MappingProxyType(
    {
        TrustLabel.CONTRACT_METADATA_ONLY: (
            "Contract-only catalog metadata; no component was imported or executed."
        ),
        TrustLabel.INSTALLED_SUBJECT_INSPECTED: (
            "Installed component identity and schema were inspected; no calculation was executed."
        ),
        TrustLabel.UNVERIFIED_CALLER_DATA: (
            "Registered caller-supplied data is unverified and unauthenticated."
        ),
        TrustLabel.STRUCTURAL_COMPATIBILITY_ONLY: (
            "Semantic-port compatibility is structural only; it neither transfers data nor "
            "authorizes execution."
        ),
        TrustLabel.UNMANAGED_EXECUTION: (
            "Unsigned host-reconciled record for an unmanaged local operation; digests bind its "
            "request, selected subject, result, and declared artifact bytes. Data authenticity, "
            "financial approval, correctness, and independent execution are not attested."
        ),
        TrustLabel.NO_VERIFIED_RESULT: (
            "No dataset, component result, or digest-checked artifact was returned."
        ),
    }
)


@dataclass(frozen=True, slots=True)
class HostFailureSpec:
    """Fixed safe presentation and retry policy for one host code."""

    outcome: HostOutcome
    message: str
    retry_allowed: bool
    retry_condition: str


HOST_FAILURE_SPECS: Mapping[HostFailureCode, HostFailureSpec] = MappingProxyType(
    {
        HostFailureCode.INVALID_TOOL_REQUEST: HostFailureSpec(
            HostOutcome.REFUSED,
            "The request does not satisfy the closed tool schema.",
            False,
            "Correct the request before submitting a new call.",
        ),
        HostFailureCode.UNSUPPORTED_HOST_SCHEMA: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested host schema version is not supported.",
            False,
            "Use a host schema version declared by this server.",
        ),
        HostFailureCode.UNSUPPORTED_PROTOCOL_VERSION: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested Defined Quant protocol version is not supported.",
            False,
            "Use a protocol version declared compatible by the installed release.",
        ),
        HostFailureCode.UNSUPPORTED_REFERENCE_VERSION: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested content-addressed reference version is not supported.",
            False,
            "Use a reference version declared by this server.",
        ),
        HostFailureCode.COMPONENT_NOT_FOUND: HostFailureSpec(
            HostOutcome.FAILED,
            "The requested component is not available in the active catalog.",
            False,
            "Search the active catalog and submit an available component ID.",
        ),
        HostFailureCode.COMPONENT_IDENTITY_MISMATCH: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested component ID, version, or subject hash does not match the verified "
            "installed identity.",
            False,
            "Inspect the component again and submit the exact returned identity.",
        ),
        HostFailureCode.MISSING_CONVENTION: HostFailureSpec(
            HostOutcome.NEEDS_INFORMATION,
            "An answer-changing financial convention must be supplied explicitly.",
            False,
            "Obtain the missing convention from the user before submitting a new call.",
        ),
        HostFailureCode.INCOMPATIBLE_PORTS: HostFailureSpec(
            HostOutcome.REFUSED,
            "The selected output and input semantic ports are not compatible.",
            False,
            "Select a compatible component field mapping before submitting a new call.",
        ),
        HostFailureCode.UNSUPPORTED_BINDING: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested data binding form is not supported by this host version.",
            False,
            "Use a literal or one supported immutable source record.",
        ),
        HostFailureCode.MULTIPLE_NON_LITERAL_SOURCES: HostFailureSpec(
            HostOutcome.REFUSED,
            "An operation may use at most one non-literal source record.",
            False,
            "Use one dataset or one upstream operation source before submitting a new call.",
        ),
        HostFailureCode.INVALID_DATASET: HostFailureSpec(
            HostOutcome.REFUSED,
            "The supplied data does not satisfy the closed dataset contract.",
            False,
            "Correct and register the dataset again.",
        ),
        HostFailureCode.UNSUPPORTED_DATA_FORMAT: HostFailureSpec(
            HostOutcome.REFUSED,
            "The supplied data format is not supported.",
            False,
            "Use one of the explicitly supported inline, JSON, or CSV forms.",
        ),
        HostFailureCode.INPUT_ROOT_DENIED: HostFailureSpec(
            HostOutcome.REFUSED,
            "Local file access is not permitted by the configured input roots.",
            False,
            "Use inline data or restart the server with an explicitly configured root.",
        ),
        HostFailureCode.UNSAFE_INPUT_PATH: HostFailureSpec(
            HostOutcome.REFUSED,
            "The local input path failed closed path and file-type checks.",
            False,
            "Use a regular non-symlink file beneath a configured root.",
        ),
        HostFailureCode.INPUT_LIMIT_EXCEEDED: HostFailureSpec(
            HostOutcome.REFUSED,
            "The request or dataset exceeds a configured byte or count limit.",
            False,
            "Reduce the request, file, row, column, or cell size before submitting a new call.",
        ),
        HostFailureCode.INVALID_FIELD_MAPPING: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested source-to-input field mapping is invalid.",
            False,
            "Correct the explicit field mapping before submitting a new call.",
        ),
        HostFailureCode.REFERENCE_NOT_FOUND: HostFailureSpec(
            HostOutcome.FAILED,
            "The requested immutable reference does not exist in the active session.",
            False,
            "Register the dataset or execute the operation again to obtain an active reference.",
        ),
        HostFailureCode.REFERENCE_SCOPE_DENIED: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested immutable reference is not available to the active session scope.",
            False,
            "Use a reference issued within the active session and authorized scope.",
        ),
        HostFailureCode.RECORD_CORRUPT: HostFailureSpec(
            HostOutcome.FAILED,
            "The immutable stored record failed schema or digest verification and was refused.",
            False,
            "Do not reuse the record; terminate the affected session and create a new record.",
        ),
        HostFailureCode.CACHE_FULL: HostFailureSpec(
            HostOutcome.FAILED,
            "The session storage quota cannot accept another immutable record.",
            True,
            "Retry only in a new session or after an operator increases the configured quota.",
        ),
        HostFailureCode.RECORD_PUBLICATION_FAILED: HostFailureSpec(
            HostOutcome.FAILED,
            "The immutable host record could not be published safely.",
            True,
            "Retry only after the host storage condition has been resolved.",
        ),
        HostFailureCode.WORKER_TIMEOUT: HostFailureSpec(
            HostOutcome.FAILED,
            "The bounded worker exceeded its wall-clock execution limit.",
            True,
            "Retry only after reducing the workload.",
        ),
        HostFailureCode.WORKER_CANCELLED: HostFailureSpec(
            HostOutcome.FAILED,
            "The bounded worker operation was cancelled and its process tree was terminated.",
            True,
            "Retry only when the caller explicitly requests a new execution.",
        ),
        HostFailureCode.WORKER_CRASHED: HostFailureSpec(
            HostOutcome.FAILED,
            "The bounded worker exited without a valid typed response.",
            True,
            "Retry at most once after the host replaces the failed worker.",
        ),
        HostFailureCode.WORKER_CAPACITY: HostFailureSpec(
            HostOutcome.FAILED,
            "The bounded worker concurrency limit is currently occupied.",
            True,
            "Retry only after another worker request has completed.",
        ),
        HostFailureCode.WORKER_RESOURCE_LIMIT: HostFailureSpec(
            HostOutcome.FAILED,
            "The bounded worker exceeded its configured memory or captured-output resource limit.",
            False,
            "Reduce the workload before submitting a new call.",
        ),
        HostFailureCode.COMPONENT_REFUSED: HostFailureSpec(
            HostOutcome.REFUSED,
            "The component refused the requested calculation within its declared scope.",
            False,
            "Change the unsupported request or choose another component before submitting a new "
            "call.",
        ),
        HostFailureCode.COMPONENT_FAILED: HostFailureSpec(
            HostOutcome.FAILED,
            "The component failed outside its supported refusal protocol.",
            False,
            "Do not retry the unchanged request against the same installed component identity.",
        ),
        HostFailureCode.INVALID_COMPONENT_INPUT: HostFailureSpec(
            HostOutcome.REFUSED,
            "The resolved input does not satisfy the component's canonical input model.",
            False,
            "Correct the component input before submitting a new call.",
        ),
        HostFailureCode.OUTPUT_VALIDATION_FAILED: HostFailureSpec(
            HostOutcome.FAILED,
            "The component output failed its canonical output model or identity checks.",
            False,
            "Do not retry until the installed component implementation is corrected or replaced.",
        ),
        HostFailureCode.COMPONENT_CONTRACT_ERROR: HostFailureSpec(
            HostOutcome.FAILED,
            "The installed component or its declarative contract failed integrity checks.",
            False,
            "Do not retry until the installed catalog or component is corrected.",
        ),
        HostFailureCode.ARTIFACT_PUBLICATION_FAILED: HostFailureSpec(
            HostOutcome.FAILED,
            "The operation output or declared artifact could not be published safely.",
            False,
            "Do not retry automatically; an operator must resolve the host publication condition.",
        ),
        HostFailureCode.RESULT_LIMIT_EXCEEDED: HostFailureSpec(
            HostOutcome.REFUSED,
            "The requested response, page, operation output, or artifact exceeds a configured "
            "limit.",
            False,
            "Request a smaller bounded page or reduce the operation output before submitting a "
            "new call.",
        ),
        HostFailureCode.ARTIFACT_NOT_FOUND: HostFailureSpec(
            HostOutcome.FAILED,
            "The requested artifact is not declared by the validated operation manifest.",
            False,
            "Use an artifact identifier declared by the validated operation response.",
        ),
        HostFailureCode.INTERNAL_FAILURE: HostFailureSpec(
            HostOutcome.FAILED,
            "The host failed internally without a safe domain-specific outcome.",
            False,
            "Do not retry automatically; report the stable host code to the operator.",
        ),
    }
)

_INPUT_LIMIT_NAMES = frozenset(
    {
        "tool_arguments_bytes",
        "inline_json_source_bytes",
        "local_file_bytes",
        "normalized_dataset_payload_bytes",
        "dataset_rows",
        "dataset_columns",
        "decoded_cell_bytes",
    }
)
_RESULT_LIMIT_NAMES = frozenset(
    {
        "search_response_bytes",
        "structured_tool_response_bytes",
        "dataset_preview_page_bytes",
        "result_page_bytes",
        "message_page_bytes",
        "artifact_decoded_bytes",
        "resource_response_frame_bytes",
        "normalized_result_member_bytes",
        "result_top_level_fields",
        "operation_artifacts",
        "operation_bundle_bytes",
    }
)


def _utf8_size(value: str) -> int | None:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _field_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and (size := _utf8_size(value)) is not None
        and 1 <= size <= 240
    )


def _component_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.isascii()
        and 1 <= len(value) <= 240
        and _COMPONENT_ID.fullmatch(value) is not None
    )


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and value.isascii() and _SAFE_ID.fullmatch(value) is not None


def _safe_integer(value: object, *, minimum: int = -_MAX_SAFE_INTEGER) -> bool:
    return type(value) is int and minimum <= value <= _MAX_SAFE_INTEGER


def _exact_keys(
    details: Mapping[str, object],
    required: Collection[str],
    optional: Collection[str] = (),
) -> bool:
    keys = set(details)
    return set(required) <= keys <= (set(required) | set(optional))


def _string_array(
    value: object,
    *,
    predicate: Any,
    maximum: int,
) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) <= maximum
        and all(predicate(item) for item in value)
    )


def _validate_failure_details(code: HostFailureCode, details: Mapping[str, object]) -> None:
    valid = False
    if code in {
        HostFailureCode.UNSUPPORTED_BINDING,
        HostFailureCode.MULTIPLE_NON_LITERAL_SOURCES,
        HostFailureCode.UNSUPPORTED_DATA_FORMAT,
        HostFailureCode.INPUT_ROOT_DENIED,
        HostFailureCode.UNSAFE_INPUT_PATH,
        HostFailureCode.REFERENCE_NOT_FOUND,
        HostFailureCode.REFERENCE_SCOPE_DENIED,
        HostFailureCode.RECORD_CORRUPT,
        HostFailureCode.CACHE_FULL,
        HostFailureCode.RECORD_PUBLICATION_FAILED,
        HostFailureCode.WORKER_TIMEOUT,
        HostFailureCode.WORKER_CANCELLED,
        HostFailureCode.WORKER_CRASHED,
        HostFailureCode.WORKER_CAPACITY,
        HostFailureCode.WORKER_RESOURCE_LIMIT,
        HostFailureCode.ARTIFACT_PUBLICATION_FAILED,
        HostFailureCode.INTERNAL_FAILURE,
    }:
        valid = not details
    elif code in {
        HostFailureCode.INVALID_TOOL_REQUEST,
        HostFailureCode.MISSING_CONVENTION,
        HostFailureCode.INVALID_FIELD_MAPPING,
        HostFailureCode.INVALID_COMPONENT_INPUT,
    }:
        valid = _exact_keys(details, {"fields"}) and _string_array(
            details.get("fields"), predicate=_field_name, maximum=32
        )
    elif code is HostFailureCode.UNSUPPORTED_HOST_SCHEMA:
        valid = _exact_keys(details, {"requested_version"}) and _safe_integer(
            details.get("requested_version")
        )
    elif code is HostFailureCode.UNSUPPORTED_PROTOCOL_VERSION:
        value = details.get("requested_version")
        valid = (
            _exact_keys(details, {"requested_version"})
            and isinstance(value, str)
            and (size := _utf8_size(value)) is not None
            and size <= 128
        )
    elif code is HostFailureCode.UNSUPPORTED_REFERENCE_VERSION:
        value = details.get("requested_version")
        valid = (
            _exact_keys(details, {"requested_version"})
            and isinstance(value, str)
            and value.isascii()
            and _REFERENCE_VERSION.fullmatch(value) is not None
        )
    elif code in {
        HostFailureCode.COMPONENT_NOT_FOUND,
        HostFailureCode.COMPONENT_IDENTITY_MISMATCH,
        HostFailureCode.COMPONENT_REFUSED,
        HostFailureCode.COMPONENT_FAILED,
        HostFailureCode.OUTPUT_VALIDATION_FAILED,
    }:
        valid = _exact_keys(details, {"component_id"}) and _component_id(
            details.get("component_id")
        )
    elif code is HostFailureCode.COMPONENT_CONTRACT_ERROR:
        valid = _exact_keys(details, set(), {"component_id"}) and (
            "component_id" not in details or _component_id(details.get("component_id"))
        )
    elif code is HostFailureCode.INVALID_DATASET:
        valid = _exact_keys(details, {"finding_codes"}) and _string_array(
            details.get("finding_codes"), predicate=_safe_id, maximum=32
        )
    elif code in {HostFailureCode.INPUT_LIMIT_EXCEEDED, HostFailureCode.RESULT_LIMIT_EXCEEDED}:
        names = (
            _INPUT_LIMIT_NAMES
            if code is HostFailureCode.INPUT_LIMIT_EXCEEDED
            else _RESULT_LIMIT_NAMES
        )
        limit_name = details.get("limit_name")
        valid = (
            _exact_keys(details, {"limit_name", "maximum", "actual"})
            and isinstance(limit_name, str)
            and limit_name in names
            and _safe_integer(details.get("maximum"), minimum=0)
            and _safe_integer(details.get("actual"), minimum=0)
        )
    elif code is HostFailureCode.ARTIFACT_NOT_FOUND:
        valid = _exact_keys(details, {"artifact_id"}) and _safe_id(details.get("artifact_id"))
    elif code is HostFailureCode.INCOMPATIBLE_PORTS:
        differences = details.get("differences")
        valid = _exact_keys(details, {"differences"}) and isinstance(differences, Sequence)
        if valid:
            assert isinstance(differences, Sequence)
            valid = len(differences) <= 8
            for difference in differences:
                try:
                    PortDifference.model_validate(difference)
                except Exception:
                    valid = False
                    break
    if not valid:
        raise ValueError(f"details do not satisfy the closed schema for {code.value}")


class Trust(BaseModel):
    """One fixed trust statement selected solely by its closed label."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: TrustLabel
    statement: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_statement(self) -> Trust:
        if self.statement != _TRUST_STATEMENTS[self.label]:
            raise ValueError("trust statement does not match its fixed label")
        return self


class HostSuccess(BaseModel):
    """Complete transport-neutral successful host envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host_schema_version: Literal[1] = 1
    outcome: Literal["ok"] = "ok"
    data: dict[str, JsonValue]
    trust: Trust

    @field_validator("data")
    @classmethod
    def freeze_data(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return freeze_json_object(value)


class HostError(BaseModel):
    """Fixed, redacted host error with code-specific closed details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: HostFailureCode
    message: str = Field(min_length=1, max_length=500)
    retry_allowed: bool
    details: dict[str, JsonValue]

    @field_validator("details")
    @classmethod
    def freeze_details(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return freeze_json_object(value)

    @model_validator(mode="after")
    def validate_fixed_fields(self) -> HostError:
        spec = HOST_FAILURE_SPECS[self.code]
        if self.message != spec.message or self.retry_allowed is not spec.retry_allowed:
            raise ValueError("host error presentation does not match its fixed code")
        _validate_failure_details(self.code, self.details)
        return self


class HostFailure(BaseModel):
    """Complete transport-neutral expected-failure envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host_schema_version: Literal[1] = 1
    outcome: HostOutcome
    error: HostError
    trust: Trust

    @model_validator(mode="after")
    def validate_outcome(self) -> HostFailure:
        if self.outcome is not HOST_FAILURE_SPECS[self.error.code].outcome:
            raise ValueError("host outcome does not match its fixed code")
        return self


def trust_notice(label: TrustLabel = TrustLabel.NO_VERIFIED_RESULT) -> Trust:
    """Construct a trust notice without accepting caller-authored wording."""

    return Trust(label=label, statement=_TRUST_STATEMENTS[label])


def host_success(
    data: Mapping[str, Any],
    *,
    trust: TrustLabel,
) -> HostSuccess:
    """Construct the one closed success envelope used by future transports."""

    return HostSuccess(data=dict(data), trust=trust_notice(trust))


def tool_success_result_projection(
    data: Mapping[str, Any],
    *,
    trust: TrustLabel,
) -> dict[str, Any]:
    """Project the complete frozen tool result for pre-transport byte accounting."""

    envelope = host_success(data, trust=trust).model_dump(mode="json")
    return {
        "content": [{"type": "text", "text": HOST_SUCCESS_TEXT}],
        "structuredContent": envelope,
        "isError": False,
    }


def host_failure(
    code: HostFailureCode,
    *,
    details: Mapping[str, JsonValue] | None = None,
    trust: TrustLabel = TrustLabel.NO_VERIFIED_RESULT,
) -> HostFailure:
    """Construct one closed failure using only fixed presentation data."""

    spec = HOST_FAILURE_SPECS[code]
    return HostFailure(
        outcome=spec.outcome,
        error=HostError(
            code=code,
            message=spec.message,
            retry_allowed=spec.retry_allowed,
            details=dict(details or {}),
        ),
        trust=trust_notice(trust),
    )


class HostFailureException(Exception):
    """Transport-neutral control-flow exception carrying one safe host failure."""

    def __init__(
        self,
        code: HostFailureCode,
        *,
        details: Mapping[str, JsonValue] | None = None,
        trust: TrustLabel = TrustLabel.NO_VERIFIED_RESULT,
    ) -> None:
        self.failure = host_failure(code, details=details, trust=trust)
        super().__init__(self.failure.error.message)

    @property
    def code(self) -> HostFailureCode:
        """Return the stable code without exposing exception text."""

        return self.failure.error.code


OPERATION_ERROR_DEFAULT_CODES: Mapping[OperationErrorCode, HostFailureCode] = MappingProxyType(
    {
        OperationErrorCode.INVALID_OPERATION_REQUEST: HostFailureCode.INVALID_TOOL_REQUEST,
        OperationErrorCode.INVALID_JSON: HostFailureCode.INVALID_TOOL_REQUEST,
        OperationErrorCode.COMPONENT_NOT_FOUND: HostFailureCode.COMPONENT_NOT_FOUND,
        OperationErrorCode.COMPONENT_IDENTITY_MISMATCH: (
            HostFailureCode.COMPONENT_IDENTITY_MISMATCH
        ),
        OperationErrorCode.INVALID_COMPONENT_INPUT: HostFailureCode.INVALID_COMPONENT_INPUT,
        OperationErrorCode.COMPONENT_REFUSED: HostFailureCode.COMPONENT_REFUSED,
        OperationErrorCode.COMPONENT_EXECUTION_FAILED: HostFailureCode.COMPONENT_FAILED,
        OperationErrorCode.OUTPUT_VALIDATION_FAILED: HostFailureCode.OUTPUT_VALIDATION_FAILED,
        OperationErrorCode.COMPONENT_CONTRACT_ERROR: HostFailureCode.COMPONENT_CONTRACT_ERROR,
        OperationErrorCode.INVALID_OUTPUT_DIRECTORY: HostFailureCode.ARTIFACT_PUBLICATION_FAILED,
        OperationErrorCode.OUTPUT_EXISTS: HostFailureCode.ARTIFACT_PUBLICATION_FAILED,
        OperationErrorCode.ARTIFACT_WRITE_FAILED: HostFailureCode.ARTIFACT_PUBLICATION_FAILED,
        OperationErrorCode.OPERATION_FAILED: HostFailureCode.INTERNAL_FAILURE,
    }
)


def _verified_fields(values: Collection[str]) -> frozenset[str]:
    if isinstance(values, str):
        return frozenset()
    return frozenset(value for value in values if _field_name(value))


def _project_fields(values: object, verified_fields: frozenset[str]) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    projected: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value not in verified_fields or value in seen:
            continue
        seen.add(value)
        projected.append(value)
        if len(projected) == 32:
            break
    return projected


def _component_input_fields(error: OperationError, verified_fields: frozenset[str]) -> list[str]:
    raw_errors = error.details.get("errors")
    if not isinstance(raw_errors, list):
        return []
    candidates: list[object] = []
    for raw_error in raw_errors:
        if not isinstance(raw_error, Mapping):
            continue
        location = raw_error.get("loc")
        if isinstance(location, Sequence) and not isinstance(
            location, (str, bytes, bytearray)
        ) and location:
            candidates.append(location[0])
    return _project_fields(candidates, verified_fields)


def _missing_convention_fields(
    error: OperationError, verified_fields: frozenset[str]
) -> list[str]:
    component_error = error.details.get("component_error")
    if not isinstance(component_error, Mapping) or component_error.get("code") != "ambiguous_input":
        return []
    component_details = component_error.get("details")
    if not isinstance(component_details, Mapping):
        return []
    questions = component_details.get("questions")
    if not isinstance(questions, list):
        return []
    candidates = [question.get("field") for question in questions if isinstance(question, Mapping)]
    return _project_fields(candidates, verified_fields)


def _project_component_id(
    error: OperationError,
    validated_request: OperationRequest | None,
) -> str | None:
    candidates: list[object] = []
    if validated_request is not None:
        candidates.append(validated_request.component.id)
    if error.component is not None:
        candidates.append(error.component.id)
    for value in candidates:
        if _component_id(value):
            assert isinstance(value, str)
            return value
    return None


def map_operation_error(
    error: OperationError,
    *,
    validated_request: OperationRequest | None = None,
    component_input_fields: Collection[str] = (),
) -> HostFailure:
    """Project one typed runtime error through the exhaustive redacted host mapping."""

    try:
        code = OPERATION_ERROR_DEFAULT_CODES[error.code]
        verified_fields = _verified_fields(component_input_fields)
        if error.code in {
            OperationErrorCode.INVALID_OPERATION_REQUEST,
            OperationErrorCode.INVALID_JSON,
        }:
            return host_failure(code, details={"fields": []})
        if error.code is OperationErrorCode.INVALID_COMPONENT_INPUT:
            fields: list[JsonValue] = list(_component_input_fields(error, verified_fields))
            return host_failure(
                code,
                details={"fields": fields},
            )
        if error.code is OperationErrorCode.COMPONENT_REFUSED:
            projected_fields = _missing_convention_fields(error, verified_fields)
            if projected_fields:
                fields = list(projected_fields)
                return host_failure(
                    HostFailureCode.MISSING_CONVENTION,
                    details={"fields": fields},
                )
        component_id = _project_component_id(error, validated_request)
        if error.code in {
            OperationErrorCode.COMPONENT_NOT_FOUND,
            OperationErrorCode.COMPONENT_IDENTITY_MISMATCH,
            OperationErrorCode.COMPONENT_REFUSED,
            OperationErrorCode.COMPONENT_EXECUTION_FAILED,
            OperationErrorCode.OUTPUT_VALIDATION_FAILED,
        }:
            if component_id is None:
                return host_failure(HostFailureCode.INTERNAL_FAILURE)
            return host_failure(code, details={"component_id": component_id})
        if error.code is OperationErrorCode.COMPONENT_CONTRACT_ERROR:
            details: dict[str, JsonValue] = (
                {"component_id": component_id} if component_id is not None else {}
            )
            return host_failure(code, details=details)
        return host_failure(code)
    except Exception:
        return host_failure(HostFailureCode.INTERNAL_FAILURE)


def map_operation_failure(
    failure: OperationFailure,
    *,
    validated_request: OperationRequest | None = None,
    component_input_fields: Collection[str] = (),
) -> HostFailure:
    """Project the error carried by one typed operation failure."""

    return map_operation_error(
        failure.error,
        validated_request=validated_request,
        component_input_fields=component_input_fields,
    )


__all__ = [
    "HOST_FAILURE_SPECS",
    "HOST_SUCCESS_TEXT",
    "OPERATION_ERROR_DEFAULT_CODES",
    "HostError",
    "HostFailure",
    "HostFailureCode",
    "HostFailureException",
    "HostFailureSpec",
    "HostSuccess",
    "HostOutcome",
    "Trust",
    "TrustLabel",
    "host_failure",
    "host_success",
    "map_operation_error",
    "map_operation_failure",
    "trust_notice",
    "tool_success_result_projection",
]
