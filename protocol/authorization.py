"""Deterministic validation receipts and manual-only authorization records."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .canonical import CANONICALIZATION_ID, canonical_hash, canonical_hash_framing
from .operation import ComponentRef, RunnerIdentity
from .plan import ANALYSIS_PLAN_HASH_DOMAIN, DATASET_BINDING_HASH_DOMAIN, AnalysisPlan
from .policy import EXECUTION_POLICY_HASH_DOMAIN, ExecutionPolicy, SafeId
from .version import PROTOCOL_VERSION

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
VALIDATION_RECEIPT_HASH_DOMAIN = "managed.validation_receipt.v1"
MANUAL_APPROVAL_HASH_DOMAIN = "managed.manual_approval.v1"
AUTHORIZATION_BINDING_HASH_DOMAIN = "managed.authorization_binding.v1"

AuthorizationText: TypeAlias = Annotated[str, Field(min_length=1, max_length=500)]


class ValidationIssueCode(StrEnum):
    """Stable failure reasons returned before a managed component can run."""

    EXECUTION_SCOPE_DENIED = "execution_scope_denied"
    PLAN_POLICY_MISMATCH = "plan_policy_mismatch"
    PLAN_COMPONENT_IDENTITY_MISMATCH = "plan_component_identity_mismatch"
    PLAN_COMPONENT_LIFECYCLE_DENIED = "plan_component_lifecycle_denied"
    PLAN_REQUIRED_QUESTION_UNRESOLVED = "plan_required_question_unresolved"
    PLAN_RESOLVED_QUESTION_MISMATCH = "plan_resolved_question_mismatch"
    MANAGED_REQUIRED_INPUT_MISSING = "managed_required_input_missing"
    MANAGED_TIMESTAMP_REQUIRED = "managed_timestamp_required"
    INVALID_COMPONENT_INPUT = "invalid_component_input"
    COMPONENT_CONSTRAINT_VIOLATION = "component_constraint_violation"
    COMPONENT_CONTRACT_ERROR = "component_contract_error"


class ValidationIssue(BaseModel):
    """One closed, inspectable plan-validation failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ValidationIssueCode
    message: AuthorizationText
    field: str | None = Field(default=None, min_length=1, max_length=160)


class ClarificationQuestion(BaseModel):
    """A material question that blocks receipt issuance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: SafeId
    target_field: SafeId
    prompt: AuthorizationText
    materiality: AuthorizationText


class ValidationCheck(BaseModel):
    """One deterministic check recorded by a successful validator run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: SafeId
    status: Literal["passed"] = "passed"


class ValidationFailure(BaseModel):
    """Failed plan validation; this shape can never be approved as a receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["needs_information", "blocked"]
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    issues: tuple[ValidationIssue, ...] = Field(min_length=1)
    questions: tuple[ClarificationQuestion, ...] = ()

    @model_validator(mode="after")
    def validate_status(self) -> ValidationFailure:
        if self.status == "needs_information" and not self.questions:
            raise ValueError("needs_information failures require clarification questions")
        if self.status == "blocked" and self.questions:
            raise ValueError("blocked failures cannot contain clarification questions")
        question_ids = [question.question_id for question in self.questions]
        target_fields = [question.target_field for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("clarification question IDs must be unique")
        if len(set(target_fields)) != len(target_fields):
            raise ValueError("clarification question target fields must be unique")
        return self


class ValidationReceipt(BaseModel):
    """Success-only deterministic binding of one exact plan and policy evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["validated"] = "validated"
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    policy_hash: str = Field(pattern=_SHA256_PATTERN)
    component: ComponentRef
    dataset_hash: str = Field(pattern=_SHA256_PATTERN)
    validator: RunnerIdentity
    checks: tuple[ValidationCheck, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_checks(self) -> ValidationReceipt:
        check_ids = [check.check_id for check in self.checks]
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("validation receipt check IDs must be unique")
        return self

    @property
    def receipt_hash(self) -> str:
        """Return the complete deterministic validation-record digest."""

        return canonical_hash(
            self.model_dump(mode="json"),
            domain=VALIDATION_RECEIPT_HASH_DOMAIN,
        )


PlanValidationOutcome: TypeAlias = Annotated[
    ValidationReceipt | ValidationFailure,
    Field(discriminator="status"),
]


class ApprovalRecord(BaseModel):
    """One human authorization of an exact plan and validation receipt.

    There is intentionally no policy, automatic, fallback, or nullable future-authorization
    field. ``approved_at`` is operational approval-record content, not plan semantics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    authorization_kind: Literal["manual"] = "manual"
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    validation_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    approved_by: str = Field(min_length=1, max_length=160)
    approved_at: AwareDatetime
    note: str | None = Field(default=None, min_length=1, max_length=500)

    @property
    def approval_hash(self) -> str:
        """Bind the exact manual approval record, including actor and approval time."""

        return canonical_hash(
            self.model_dump(mode="json"),
            domain=MANUAL_APPROVAL_HASH_DOMAIN,
        )


class AuthorizationBinding(BaseModel):
    """Roots captured before a later managed runner may call a component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    validation_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    approval_hash: str = Field(pattern=_SHA256_PATTERN)
    component: ComponentRef
    dataset_hash: str = Field(pattern=_SHA256_PATTERN)

    @property
    def authorization_hash(self) -> str:
        """Return the digest of all captured authorization roots."""

        return canonical_hash(
            self.model_dump(mode="json"),
            domain=AUTHORIZATION_BINDING_HASH_DOMAIN,
        )


class AuthorizationErrorCode(StrEnum):
    """Stable chain-revalidation failures before managed execution."""

    PLAN_REVISION_STALE = "plan_revision_stale"
    VALIDATION_RECEIPT_MISMATCH = "validation_receipt_mismatch"
    APPROVAL_BINDING_MISMATCH = "approval_binding_mismatch"
    COMPONENT_BINDING_MISMATCH = "component_binding_mismatch"
    DATASET_BINDING_MISMATCH = "dataset_binding_mismatch"
    POLICY_BINDING_MISMATCH = "policy_binding_mismatch"
    AUTHORIZATION_BINDING_MISMATCH = "authorization_binding_mismatch"


class AuthorizationFailure(BaseModel):
    """Typed refusal to accept a stale or inconsistent authorization chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["failed"] = "failed"
    code: AuthorizationErrorCode
    message: AuthorizationText


class AuthorizationSuccess(BaseModel):
    """Successfully revalidated authorization roots; no calculation has run yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["authorized"] = "authorized"
    binding: AuthorizationBinding


AuthorizationOutcome: TypeAlias = Annotated[
    AuthorizationSuccess | AuthorizationFailure,
    Field(discriminator="status"),
]


def validation_receipt_hash(receipt: ValidationReceipt) -> str:
    """Compatibility function for explicit receipt hashing."""

    return receipt.receipt_hash


def approval_record_hash(approval: ApprovalRecord) -> str:
    """Compatibility function for explicit approval hashing."""

    return approval.approval_hash


def authorization_binding_hash(binding: AuthorizationBinding) -> str:
    """Compatibility function for explicit authorization-root hashing."""

    return binding.authorization_hash


def managed_authorization_protocol_schema() -> dict[str, Any]:
    """Return the public descriptor for the atomic managed-authorization surface."""

    return {
        "package": "defined_quant_protocol",
        "name": "defined_quant_managed_authorization",
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": 1,
        "execution_mode": "managed_authorization_only",
        "canonicalization_id": CANONICALIZATION_ID,
        "hash_algorithm": "sha256",
        "hash_framing": canonical_hash_framing(),
        "hash_domains": {
            "policy": EXECUTION_POLICY_HASH_DOMAIN,
            "dataset": DATASET_BINDING_HASH_DOMAIN,
            "plan": ANALYSIS_PLAN_HASH_DOMAIN,
            "validation_receipt": VALIDATION_RECEIPT_HASH_DOMAIN,
            "manual_approval": MANUAL_APPROVAL_HASH_DOMAIN,
            "authorization_binding": AUTHORIZATION_BINDING_HASH_DOMAIN,
        },
        "schemas": {
            "execution_policy": ExecutionPolicy.model_json_schema(),
            "analysis_plan": AnalysisPlan.model_json_schema(),
            "validation_receipt": ValidationReceipt.model_json_schema(),
            "validation_failure": ValidationFailure.model_json_schema(),
            "validation_outcome": TypeAdapter(PlanValidationOutcome).json_schema(),
            "manual_approval": ApprovalRecord.model_json_schema(),
            "authorization_binding": AuthorizationBinding.model_json_schema(),
            "authorization_success": AuthorizationSuccess.model_json_schema(),
            "authorization_failure": AuthorizationFailure.model_json_schema(),
            "authorization_outcome": TypeAdapter(AuthorizationOutcome).json_schema(),
        },
    }


__all__ = [
    "ApprovalRecord",
    "AUTHORIZATION_BINDING_HASH_DOMAIN",
    "MANUAL_APPROVAL_HASH_DOMAIN",
    "AuthorizationBinding",
    "AuthorizationErrorCode",
    "AuthorizationFailure",
    "AuthorizationOutcome",
    "AuthorizationSuccess",
    "ClarificationQuestion",
    "PlanValidationOutcome",
    "ValidationCheck",
    "ValidationFailure",
    "ValidationIssue",
    "ValidationIssueCode",
    "ValidationReceipt",
    "VALIDATION_RECEIPT_HASH_DOMAIN",
    "approval_record_hash",
    "authorization_binding_hash",
    "managed_authorization_protocol_schema",
    "validation_receipt_hash",
]
