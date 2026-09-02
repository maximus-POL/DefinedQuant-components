"""Trusted-adapter execution envelopes and immutable methods-first records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ._immutable_json import freeze_json
from .canonical import canonical_hash, canonical_json_bytes
from .registry import (
    AdapterRef,
    BackendBinding,
    BackendRef,
    BackendRole,
    CapabilityRef,
    ImplementationRef,
    JsonObject,
    MethodRef,
    RegistryText,
    RuntimeIdentity,
    RuntimeLocality,
    SafeId,
    Sha256,
    TransportKind,
)
from .resolution import (
    AvailabilityStatus,
    CompiledStep,
    ImplementationAvailability,
    PlanRecord,
    PlanRef,
    ValidationWarning,
    WarningSource,
    WarningSourceKind,
)

PLAN_EXECUTION_REQUEST_HASH_DOMAIN = "execution.plan_request"
ADAPTER_EXECUTION_REQUEST_HASH_DOMAIN = "execution.adapter_request"
ADAPTER_EXECUTION_RESULT_HASH_DOMAIN = "execution.adapter_result"
STEP_RECORD_HASH_DOMAIN = "records.step"
RUN_RECORD_HASH_DOMAIN = "records.run"


class _ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def _freeze_nested_json(self) -> Self:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            frozen = freeze_json(value)
            if frozen is not value:
                object.__setattr__(self, field_name, frozen)
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        del deep
        # Preserve default discriminator tags in nested execution-result unions.
        values = self.model_dump(mode="python")
        if update is not None:
            values.update(update)
        return type(self).model_validate(values)


def _semantic_dump(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _canonical_order(values: Sequence[Any], *, key: Any, name: str) -> None:
    if tuple(values) != tuple(sorted(values, key=key)):
        raise ValueError(f"{name} must use canonical order")


def _unique(values: Sequence[Any], *, key: Any, name: str) -> None:
    keys = tuple(key(value) for value in values)
    if len(set(keys)) != len(keys):
        raise ValueError(f"{name} must be unique")


class DatasetRef(_ClosedModel):
    """A canonical dataset identity bound into a methods-first run record."""

    reference: Annotated[str, Field(pattern=r"^dqds:[0-9a-f]{64}$")]

    @property
    def record_hash(self) -> str:
        return self.reference.rsplit(":", 1)[1]


class StepRef(_ClosedModel):
    reference: Annotated[str, Field(pattern=r"^dqstep:[0-9a-f]{64}$")]

    @property
    def step_hash(self) -> str:
        return self.reference.removeprefix("dqstep:")

    @classmethod
    def from_hash(cls, step_hash: str) -> StepRef:
        return cls(reference=f"dqstep:{step_hash}")


class PlanExecutionRequest(_ClosedModel):
    schema_version: Literal[1] = 1
    plan: PlanRef
    plan_record_hash: Sha256
    requester: RuntimeIdentity
    requested_at: AwareDatetime
    datasets: tuple[DatasetRef, ...] = ()
    runtime_fallback_allowed: Literal[False] = False

    @model_validator(mode="after")
    def _validate_request(self) -> PlanExecutionRequest:
        _unique(self.datasets, key=lambda item: item.reference, name="execution datasets")
        _canonical_order(
            self.datasets,
            key=lambda item: item.reference,
            name="execution datasets",
        )
        return self

    @property
    def request_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=PLAN_EXECUTION_REQUEST_HASH_DOMAIN)


class ProviderAuthenticationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    NOT_ATTEMPTED = "not_attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ProviderEntitlementStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    NOT_CHECKED = "not_checked"
    CONFIRMED = "confirmed"
    DENIED = "denied"
    UNKNOWN = "unknown"


class RemoteExecutionIntegrity(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PROVIDER_ATTESTED = "provider_attested"
    UNATTESTED = "unattested"
    UNKNOWN = "unknown"


class ProviderInteraction(_ClosedModel):
    """Non-secret provider facts reported by trusted adapter code."""

    backend_role: BackendRole
    backend: BackendRef
    authentication: ProviderAuthenticationStatus
    entitlement: ProviderEntitlementStatus
    remote_execution_integrity: RemoteExecutionIntegrity
    provider_request_id: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,319}$"),
    ] | None = None
    response_digest: Sha256 | None = None

    @model_validator(mode="after")
    def _validate_interaction(self) -> ProviderInteraction:
        if self.backend_role == BackendRole.RUNTIME:
            raise ValueError("provider interaction must identify a non-runtime backend role")
        return self


class ArtifactRecord(_ClosedModel):
    artifact_id: SafeId
    media_type: Annotated[
        str,
        Field(pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"),
    ]
    digest: Sha256
    byte_length: Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
    member_path: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$"),
    ]

    @model_validator(mode="after")
    def _validate_member_path(self) -> ArtifactRecord:
        if any(part in {".", ".."} for part in self.member_path.split("/")):
            raise ValueError("artifact member path cannot contain traversal segments")
        return self


class FailureFact(_ClosedModel):
    """One adapter-sanitized, non-secret failure detail."""

    key: SafeId
    value: Annotated[str, Field(min_length=1, max_length=1000)]


class ExecutionFailureCode(StrEnum):
    IMPLEMENTATION_UNAVAILABLE = "implementation_unavailable"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    ARTIFACT_ID_CONFLICT = "artifact_id_conflict"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    AUTHENTICATION_UNAVAILABLE = "authentication_unavailable"
    LICENCE_UNAVAILABLE = "licence_unavailable"
    ENTITLEMENT_UNAVAILABLE = "entitlement_unavailable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    MALFORMED_BACKEND_RESPONSE = "malformed_backend_response"
    INPUT_VALIDATION_FAILED = "input_validation_failed"
    INPUT_MAPPING_FAILED = "input_mapping_failed"
    OUTPUT_MAPPING_FAILED = "output_mapping_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    POLICY_DRIFT = "policy_drift"
    DATA_HANDLING_REFUSED = "data_handling_refused"
    DEPENDENCY_FAILED = "dependency_failed"
    CANCELLED = "cancelled"
    INTERNAL_FAILURE = "internal_failure"


class ExecutionFailure(_ClosedModel):
    code: ExecutionFailureCode
    message: RegistryText
    retryable: bool
    backend_role: BackendRole | None = None
    backend: BackendRef | None = None
    facts: tuple[FailureFact, ...] = ()

    @model_validator(mode="after")
    def _validate_failure(self) -> ExecutionFailure:
        if (self.backend_role is None) != (self.backend is None):
            raise ValueError("failure backend role and identity must occur together")
        _unique(self.facts, key=lambda item: item.key, name="failure facts")
        _canonical_order(self.facts, key=lambda item: item.key, name="failure facts")
        return self


class AdapterExecutionRequest(_ClosedModel):
    schema_version: Literal[1] = 1
    plan: PlanRef
    execution_request_hash: Sha256
    step_id: SafeId
    capability: CapabilityRef
    implementation: ImplementationRef
    adapter: AdapterRef
    backend_bindings: tuple[BackendBinding, ...] = Field(min_length=1)
    transport: TransportKind
    locality: RuntimeLocality
    execution_availability: ImplementationAvailability
    canonical_inputs: JsonObject
    dependency_steps: tuple[StepRef, ...] = ()
    attempt: Annotated[int, Field(strict=True, ge=1, le=100)] = 1
    runtime_fallback_allowed: Literal[False] = False

    @model_validator(mode="after")
    def _validate_adapter_request(self) -> AdapterExecutionRequest:
        if self.execution_availability.status != AvailabilityStatus.AVAILABLE:
            raise ValueError("adapter invocation requires an available exact implementation")
        if (
            self.execution_availability.implementation != self.implementation
            or self.execution_availability.adapter != self.adapter
            or self.execution_availability.backend_bindings != self.backend_bindings
        ):
            raise ValueError("execution availability must bind the compiled realization")
        backends = {item.role: item.backend for item in self.backend_bindings}
        if backends.get(BackendRole.RUNTIME) != self.adapter.backend:
            raise ValueError("adapter execution requires its exact runtime backend")
        canonical_json_bytes(self.canonical_inputs)
        _unique(
            self.dependency_steps,
            key=lambda item: item.reference,
            name="adapter dependency steps",
        )
        _canonical_order(
            self.dependency_steps,
            key=lambda item: item.reference,
            name="adapter dependency steps",
        )
        return self

    @property
    def request_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=ADAPTER_EXECUTION_REQUEST_HASH_DOMAIN)


class AdapterExecutionSuccess(_ClosedModel):
    status: Literal["success"] = "success"
    schema_version: Literal[1] = 1
    request_hash: Sha256
    adapter_runtime: RuntimeIdentity
    canonical_output: JsonObject
    provider_interactions: tuple[ProviderInteraction, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()
    warnings: tuple[ValidationWarning, ...] = ()

    @model_validator(mode="after")
    def _validate_success(self) -> AdapterExecutionSuccess:
        canonical_json_bytes(self.canonical_output)
        _unique(
            self.provider_interactions,
            key=lambda item: (item.backend_role, item.backend.spec_hash),
            name="provider interactions",
        )
        _canonical_order(
            self.provider_interactions,
            key=lambda item: (item.backend_role.value, item.backend.id, item.backend.spec_hash),
            name="provider interactions",
        )
        _unique(self.artifacts, key=lambda item: item.artifact_id, name="adapter artifacts")
        _canonical_order(
            self.artifacts,
            key=lambda item: item.artifact_id,
            name="adapter artifacts",
        )
        _unique(
            self.warnings,
            key=lambda item: (
                item.source.kind,
                item.source.subject_id,
                item.code,
                item.field,
            ),
            name="adapter warnings",
        )
        _canonical_order(
            self.warnings,
            key=lambda item: (
                item.source.kind.value,
                item.source.subject_id,
                item.code,
                item.field or "",
            ),
            name="adapter warnings",
        )
        return self

    @property
    def result_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=ADAPTER_EXECUTION_RESULT_HASH_DOMAIN)


class AdapterExecutionFailure(_ClosedModel):
    status: Literal["failure"] = "failure"
    schema_version: Literal[1] = 1
    request_hash: Sha256
    adapter_runtime: RuntimeIdentity
    failure: ExecutionFailure
    provider_interactions: tuple[ProviderInteraction, ...] = ()

    @model_validator(mode="after")
    def _validate_failure(self) -> AdapterExecutionFailure:
        _unique(
            self.provider_interactions,
            key=lambda item: (item.backend_role, item.backend.spec_hash),
            name="provider interactions",
        )
        _canonical_order(
            self.provider_interactions,
            key=lambda item: (item.backend_role.value, item.backend.id, item.backend.spec_hash),
            name="provider interactions",
        )
        return self

    @property
    def result_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=ADAPTER_EXECUTION_RESULT_HASH_DOMAIN)


AdapterExecutionResult: TypeAlias = Annotated[
    AdapterExecutionSuccess | AdapterExecutionFailure,
    Field(discriminator="status"),
]


class ValidationStatus(StrEnum):
    NOT_PERFORMED = "not_performed"
    PASSED = "passed"
    FAILED = "failed"


class ValidationIssue(_ClosedModel):
    path: Annotated[str, Field(min_length=1, max_length=320)]
    code: SafeId
    message: RegistryText


class CanonicalValidation(_ClosedModel):
    status: ValidationStatus
    schema_hash: Sha256
    validator: RuntimeIdentity
    issues: tuple[ValidationIssue, ...] = ()

    @model_validator(mode="after")
    def _validate_receipt(self) -> CanonicalValidation:
        _unique(self.issues, key=lambda item: (item.path, item.code), name="validation issues")
        _canonical_order(
            self.issues,
            key=lambda item: (item.path, item.code),
            name="validation issues",
        )
        if (self.status == ValidationStatus.FAILED) != bool(self.issues):
            raise ValueError("failed validation requires issues; other statuses forbid them")
        return self


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepRecord(_ClosedModel):
    record_kind: Literal["step"] = "step"
    schema_version: Literal[1] = 1
    status: StepStatus
    plan: PlanRef
    execution_request_hash: Sha256
    method: MethodRef
    compiled_step: CompiledStep
    canonical_inputs: JsonObject
    dependency_steps: tuple[StepRef, ...] = ()
    input_validation: CanonicalValidation
    execution_availability: ImplementationAvailability
    adapter_request: AdapterExecutionRequest | None = None
    adapter_result: AdapterExecutionResult | None = None
    output_validation: CanonicalValidation
    canonical_output: JsonObject | None = None
    failure: ExecutionFailure | None = None
    warnings: tuple[ValidationWarning, ...] = ()
    executor: RuntimeIdentity
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_step(self) -> StepRecord:
        if self.finished_at < self.started_at:
            raise ValueError("step completion cannot precede its start")
        canonical_json_bytes(self.canonical_inputs)
        if self.canonical_output is not None:
            canonical_json_bytes(self.canonical_output)
        _unique(self.dependency_steps, key=lambda item: item.reference, name="step dependencies")
        _canonical_order(
            self.dependency_steps,
            key=lambda item: item.reference,
            name="step dependencies",
        )
        capability_warning_source = WarningSource(
            kind=WarningSourceKind.CAPABILITY,
            subject_id=self.compiled_step.capability.id,
        )
        adapter_warning_source = WarningSource(
            kind=WarningSourceKind.ADAPTER,
            subject_id=self.compiled_step.adapter.id,
        )
        if any(
            item.source not in {capability_warning_source, adapter_warning_source}
            for item in self.warnings
        ):
            raise ValueError("step warnings must identify its capability or exact adapter")
        _unique(
            self.warnings,
            key=lambda item: (
                item.source.kind,
                item.source.subject_id,
                item.code,
                item.field,
            ),
            name="step warnings",
        )
        _canonical_order(
            self.warnings,
            key=lambda item: (
                item.source.kind.value,
                item.source.subject_id,
                item.code,
                item.field or "",
            ),
            name="step warnings",
        )
        if (
            self.execution_availability.implementation != self.compiled_step.implementation
            or self.execution_availability.adapter != self.compiled_step.adapter
            or self.execution_availability.backend_bindings
            != self.compiled_step.backend_bindings
        ):
            raise ValueError("step availability must bind the compiled implementation")
        if self.adapter_request is not None:
            if self.input_validation.status != ValidationStatus.PASSED:
                raise ValueError("adapter request requires passed canonical input validation")
            request = self.adapter_request
            if (
                request.plan != self.plan
                or request.execution_request_hash != self.execution_request_hash
                or request.step_id != self.compiled_step.step_id
                or request.capability != self.compiled_step.capability
                or request.implementation != self.compiled_step.implementation
                or request.adapter != self.compiled_step.adapter
                or request.backend_bindings != self.compiled_step.backend_bindings
                or request.transport != self.compiled_step.transport
                or request.locality != self.compiled_step.locality
                or request.canonical_inputs != self.canonical_inputs
                or request.dependency_steps != self.dependency_steps
            ):
                raise ValueError("adapter request must bind this exact compiled step")
        if self.adapter_result is not None:
            if self.adapter_request is None:
                raise ValueError("adapter result requires its exact request")
            if self.adapter_result.request_hash != self.adapter_request.request_hash:
                raise ValueError("adapter result must bind its exact request")
            if isinstance(self.adapter_result, AdapterExecutionSuccess) and any(
                warning.source != adapter_warning_source
                for warning in self.adapter_result.warnings
            ):
                raise ValueError("adapter warnings must identify the exact compiled adapter")
            allowed_provider_bindings = {
                (binding.role, binding.backend)
                for binding in self.compiled_step.backend_bindings
                if binding.role != BackendRole.RUNTIME
            }
            if any(
                (interaction.backend_role, interaction.backend)
                not in allowed_provider_bindings
                for interaction in self.adapter_result.provider_interactions
            ):
                raise ValueError(
                    "provider interactions must bind a compiled non-runtime backend"
                )

        if self.status == StepStatus.SUCCEEDED:
            if (
                not isinstance(self.adapter_result, AdapterExecutionSuccess)
                or self.adapter_request is None
                or self.input_validation.status != ValidationStatus.PASSED
                or self.output_validation.status != ValidationStatus.PASSED
                or self.canonical_output != self.adapter_result.canonical_output
                or self.failure is not None
            ):
                raise ValueError("successful step requires validated adapter output and no failure")
        elif self.status == StepStatus.SKIPPED:
            if (
                self.adapter_request is not None
                or self.adapter_result is not None
                or self.canonical_output is not None
                or self.input_validation.status != ValidationStatus.NOT_PERFORMED
                or self.output_validation.status != ValidationStatus.NOT_PERFORMED
                or self.failure is None
                or self.failure.code != ExecutionFailureCode.DEPENDENCY_FAILED
            ):
                raise ValueError("skipped step must record a dependency failure without invocation")
        else:
            if (
                self.failure is None
                or self.canonical_output is not None
                or self.output_validation.status == ValidationStatus.PASSED
            ):
                raise ValueError(
                    "failed step requires a failure and cannot publish canonical output"
                )
            if self.input_validation.status == ValidationStatus.FAILED and (
                self.adapter_request is not None
                or self.adapter_result is not None
                or self.output_validation.status != ValidationStatus.NOT_PERFORMED
                or self.failure.code != ExecutionFailureCode.INPUT_VALIDATION_FAILED
            ):
                raise ValueError(
                    "failed canonical input validation must prevent adapter invocation"
                )
            if self.input_validation.status == ValidationStatus.NOT_PERFORMED and (
                self.adapter_request is not None or self.adapter_result is not None
            ):
                raise ValueError(
                    "unvalidated canonical input cannot reach an adapter invocation"
                )
        return self

    @property
    def step_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=STEP_RECORD_HASH_DOMAIN)

    @property
    def ref(self) -> StepRef:
        return StepRef.from_hash(self.step_hash)


class RunRef(_ClosedModel):
    reference: Annotated[str, Field(pattern=r"^dqrun:[0-9a-f]{64}$")]

    @property
    def run_hash(self) -> str:
        return self.reference.removeprefix("dqrun:")

    @classmethod
    def from_hash(cls, run_hash: str) -> RunRef:
        return cls(reference=f"dqrun:{run_hash}")


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunRecord(_ClosedModel):
    record_kind: Literal["run"] = "run"
    schema_version: Literal[1] = 1
    status: RunStatus
    plan_record: PlanRecord
    execution_request: PlanExecutionRequest
    steps: tuple[StepRecord, ...] = Field(min_length=1)
    datasets: tuple[DatasetRef, ...] = ()
    method_input_validation: CanonicalValidation
    method_output_validation: CanonicalValidation
    canonical_method_output: JsonObject | None = None
    method_warnings: tuple[ValidationWarning, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()
    warnings: tuple[ValidationWarning, ...] = ()
    failure: ExecutionFailure | None = None
    executor: RuntimeIdentity
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_run(self) -> RunRecord:
        if self.finished_at < self.started_at:
            raise ValueError("run completion cannot precede its start")
        plan = self.plan_record.plan
        if (
            self.execution_request.plan != plan.ref
            or self.execution_request.plan_record_hash != self.plan_record.record_hash
            or self.datasets != self.execution_request.datasets
        ):
            raise ValueError("run request must bind this exact plan record and dataset set")
        if self.status == RunStatus.SUCCEEDED and (
            self.method_input_validation.status != ValidationStatus.PASSED
        ):
            raise ValueError("successful run requires passed Method input validation")
        if tuple(item.compiled_step.step_id for item in self.steps) != tuple(
            item.step_id for item in plan.steps
        ):
            raise ValueError(
                "complete run requires one step record per compiled step in plan order"
            )
        for record, compiled in zip(self.steps, plan.steps, strict=True):
            if (
                record.plan != plan.ref
                or record.execution_request_hash != self.execution_request.request_hash
                or record.method != plan.method
                or record.compiled_step != compiled
            ):
                raise ValueError("run step record contradicts the compiled plan")
        _unique(self.datasets, key=lambda item: item.reference, name="run datasets")
        _canonical_order(self.datasets, key=lambda item: item.reference, name="run datasets")
        method_warning_source = WarningSource(
            kind=WarningSourceKind.METHOD,
            subject_id=plan.method.id,
        )
        if any(item.source != method_warning_source for item in self.method_warnings):
            raise ValueError("method-output warnings must identify the exact method")
        _unique(
            self.method_warnings,
            key=lambda item: (
                item.source.kind,
                item.source.subject_id,
                item.code,
                item.field,
            ),
            name="method-output warnings",
        )
        _canonical_order(
            self.method_warnings,
            key=lambda item: (
                item.source.kind.value,
                item.source.subject_id,
                item.code,
                item.field or "",
            ),
            name="method-output warnings",
        )

        step_artifacts = tuple(
            artifact
            for step in self.steps
            if isinstance(step.adapter_result, AdapterExecutionSuccess)
            for artifact in step.adapter_result.artifacts
        )
        artifact_ids = tuple(item.artifact_id for item in step_artifacts)
        artifact_conflict = len(set(artifact_ids)) != len(artifact_ids)
        expected_artifacts = (
            ()
            if artifact_conflict
            else tuple(sorted(step_artifacts, key=lambda item: item.artifact_id))
        )
        if self.artifacts != expected_artifacts:
            raise ValueError("run artifacts must exactly index all successful step artifacts")
        _unique(self.artifacts, key=lambda item: item.artifact_id, name="run artifacts")
        if artifact_conflict and (
            self.status != RunStatus.FAILED
            or self.failure is None
            or self.failure.code != ExecutionFailureCode.ARTIFACT_ID_CONFLICT
        ):
            raise ValueError("colliding step artifact IDs require an explicit failed run")
        warnings_by_identity: dict[
            tuple[WarningSourceKind, str, str, str | None],
            ValidationWarning,
        ] = {}
        for warning in (
            *plan.warnings,
            *(warning for step in self.steps for warning in step.warnings),
            *self.method_warnings,
        ):
            identity = (
                warning.source.kind,
                warning.source.subject_id,
                warning.code,
                warning.field,
            )
            existing = warnings_by_identity.get(identity)
            if existing is not None and existing != warning:
                raise ValueError(
                    "plan, step, and method warnings contradict one canonical warning identity"
                )
            warnings_by_identity[identity] = warning
        expected_warnings = tuple(
            sorted(
                warnings_by_identity.values(),
                key=lambda item: (
                    item.source.kind.value,
                    item.source.subject_id,
                    item.code,
                    item.field or "",
                ),
            )
        )
        if self.warnings != expected_warnings:
            raise ValueError(
                "run warnings must equal the canonical de-duplicated plan, step, and "
                "method-warning union"
            )
        _unique(
            self.warnings,
            key=lambda item: (
                item.source.kind,
                item.source.subject_id,
                item.code,
                item.field,
            ),
            name="run warnings",
        )
        _canonical_order(
            self.warnings,
            key=lambda item: (
                item.source.kind.value,
                item.source.subject_id,
                item.code,
                item.field or "",
            ),
            name="run warnings",
        )
        if self.canonical_method_output is not None:
            canonical_json_bytes(self.canonical_method_output)

        if self.status == RunStatus.SUCCEEDED:
            if (
                any(item.status != StepStatus.SUCCEEDED for item in self.steps)
                or self.canonical_method_output is None
                or self.method_output_validation.status != ValidationStatus.PASSED
                or self.failure is not None
            ):
                raise ValueError("successful run requires every step and method output to validate")
        else:
            all_steps_succeeded = all(
                item.status == StepStatus.SUCCEEDED for item in self.steps
            )
            final_validation_failed = bool(
                all_steps_succeeded
                and self.method_output_validation.status == ValidationStatus.FAILED
                and self.failure is not None
                and self.failure.code == ExecutionFailureCode.OUTPUT_VALIDATION_FAILED
            )
            final_mapping_failed = bool(
                all_steps_succeeded
                and self.method_output_validation.status
                == ValidationStatus.NOT_PERFORMED
                and self.failure is not None
                and self.failure.code == ExecutionFailureCode.OUTPUT_MAPPING_FAILED
            )
            if (
                self.canonical_method_output is not None
                or self.method_output_validation.status == ValidationStatus.PASSED
                or self.failure is None
                or (
                    all_steps_succeeded
                    and not (final_validation_failed or final_mapping_failed)
                )
            ):
                raise ValueError(
                    "failed run requires a failed/skipped step or failed canonical "
                    "method-output validation"
                )
        return self

    @property
    def run_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=RUN_RECORD_HASH_DOMAIN)

    @property
    def ref(self) -> RunRef:
        return RunRef.from_hash(self.run_hash)


def plan_execution_request_hash(request: PlanExecutionRequest) -> str:
    return request.request_hash


def adapter_execution_request_hash(request: AdapterExecutionRequest) -> str:
    return request.request_hash


def adapter_execution_result_hash(result: AdapterExecutionResult) -> str:
    return result.result_hash


def step_record_hash(record: StepRecord) -> str:
    return record.step_hash


def run_record_hash(record: RunRecord) -> str:
    return record.run_hash


__all__ = [
    "ADAPTER_EXECUTION_REQUEST_HASH_DOMAIN",
    "ADAPTER_EXECUTION_RESULT_HASH_DOMAIN",
    "PLAN_EXECUTION_REQUEST_HASH_DOMAIN",
    "RUN_RECORD_HASH_DOMAIN",
    "STEP_RECORD_HASH_DOMAIN",
    "AdapterExecutionFailure",
    "AdapterExecutionRequest",
    "AdapterExecutionResult",
    "AdapterExecutionSuccess",
    "ArtifactRecord",
    "CanonicalValidation",
    "DatasetRef",
    "ExecutionFailure",
    "ExecutionFailureCode",
    "FailureFact",
    "PlanExecutionRequest",
    "ProviderAuthenticationStatus",
    "ProviderEntitlementStatus",
    "ProviderInteraction",
    "RemoteExecutionIntegrity",
    "RunRecord",
    "RunRef",
    "RunStatus",
    "StepRecord",
    "StepRef",
    "StepStatus",
    "ValidationIssue",
    "ValidationStatus",
    "adapter_execution_request_hash",
    "adapter_execution_result_hash",
    "plan_execution_request_hash",
    "run_record_hash",
    "step_record_hash",
]
