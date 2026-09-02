"""Exact compiled-plan execution through trusted adapter boundaries.

This module never resolves implementations.  It rechecks the active policy, exact registry
identities, current non-secret availability, and trusted adapter catalog, then invokes only the
implementation already frozen into the plan.  Any drift or unavailability produces immutable
failure records; selecting another implementation requires recompilation and a new plan identity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from defined_quant.constraint_evaluation import (
    ConstraintEvaluationError,
    evaluate_constraint,
)
from defined_quant.method_records import MethodRecordStore
from defined_quant.planning import implementation_availability, relevant_policy
from defined_quant.registry import ProtocolRegistry
from defined_quant.schema_validation import validate_instance
from defined_quant_protocol.canonical import canonical_hash
from defined_quant_protocol.execution import (
    AdapterExecutionFailure,
    AdapterExecutionRequest,
    AdapterExecutionResult,
    AdapterExecutionSuccess,
    ArtifactRecord,
    CanonicalValidation,
    ExecutionFailure,
    ExecutionFailureCode,
    PlanExecutionRequest,
    RunRecord,
    RunStatus,
    StepRecord,
    StepRef,
    StepStatus,
    ValidationIssue,
    ValidationStatus,
)
from defined_quant_protocol.ports import PortDirection
from defined_quant_protocol.registry import (
    BackendRole,
    CapabilitySpec,
    ConstraintSeverity,
    ImplementationRef,
    ImplementationSpec,
    MethodSpec,
    RecipeValueSource,
    RuntimeIdentity,
)
from defined_quant_protocol.resolution import (
    AvailabilityReason,
    AvailabilitySnapshot,
    AvailabilityStatus,
    CompiledStep,
    ImplementationAvailability,
    InstallationStatus,
    PlanRecord,
    RequirementStatus,
    ResolutionPolicy,
    ValidationWarning,
    WarningSource,
    WarningSourceKind,
)

CANONICAL_SCHEMA_HASH_DOMAIN = "validation.canonical_schema"
UNAVAILABLE_SCHEMA_HASH = canonical_hash(
    {"status": "schema_unavailable"},
    domain=CANONICAL_SCHEMA_HASH_DOMAIN,
)


class ExactAdapterCatalog(Protocol):
    """Structural boundary implemented by the host-owned trusted adapter catalog."""

    def invoke(self, request: AdapterExecutionRequest) -> AdapterExecutionResult: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _schema_hash(schema: Mapping[str, Any]) -> str:
    return canonical_hash(schema, domain=CANONICAL_SCHEMA_HASH_DOMAIN)


def _not_performed(
    validator: RuntimeIdentity,
    *,
    schema_hash: str = UNAVAILABLE_SCHEMA_HASH,
) -> CanonicalValidation:
    return CanonicalValidation(
        status=ValidationStatus.NOT_PERFORMED,
        schema_hash=schema_hash,
        validator=validator,
    )


def _canonical_validation(
    value: Any,
    *,
    schema: Mapping[str, Any],
    constraints: Sequence[Any],
    target: PortDirection,
    validator: RuntimeIdentity,
    subject_id: str,
    subject_kind: WarningSourceKind,
) -> tuple[CanonicalValidation, tuple[ValidationWarning, ...]]:
    issues: list[ValidationIssue] = []
    warnings: list[ValidationWarning] = []
    try:
        validate_instance(value, schema, name=f"{subject_id} {target.value}")
    except (TypeError, ValueError):
        issues.append(
            ValidationIssue(
                path="$",
                code="schema_mismatch",
                message="The canonical value does not match the registered schema.",
            )
        )
    if not issues:
        try:
            for constraint in constraints:
                if constraint.target != target:
                    continue
                if not evaluate_constraint(
                    constraint.expression,
                    value,
                    subject_id=subject_id,
                ):
                    continue
                if constraint.severity == ConstraintSeverity.BLOCKING:
                    issues.append(
                        ValidationIssue(
                            path="$",
                            code=constraint.id,
                            message=constraint.message,
                        )
                    )
                else:
                    warnings.append(
                        ValidationWarning(
                            source=WarningSource(
                                kind=subject_kind,
                                subject_id=subject_id,
                            ),
                            code=constraint.id,
                            message=constraint.message,
                        )
                    )
        except ConstraintEvaluationError:
            issues.append(
                ValidationIssue(
                    path="$",
                    code="constraint_evaluation_failed",
                    message="The registered constraint could not be evaluated safely.",
                )
            )
    receipt = CanonicalValidation(
        status=(ValidationStatus.FAILED if issues else ValidationStatus.PASSED),
        schema_hash=_schema_hash(schema),
        validator=validator,
        issues=tuple(sorted(issues, key=lambda item: (item.path, item.code))),
    )
    return (
        receipt,
        tuple(
            sorted(
                warnings,
                key=lambda item: (
                    item.source.kind.value,
                    item.source.subject_id,
                    item.code,
                    item.field or "",
                ),
            )
        ),
    )


def _unknown_availability(step: CompiledStep) -> ImplementationAvailability:
    return ImplementationAvailability(
        implementation=step.implementation,
        adapter=step.adapter,
        backend_bindings=step.backend_bindings,
        enabled=True,
        installation=InstallationStatus.UNKNOWN,
        artifact=RequirementStatus.UNKNOWN,
        dependencies=RequirementStatus.UNKNOWN,
        credentials=RequirementStatus.UNKNOWN,
        licence=RequirementStatus.UNKNOWN,
        entitlement=RequirementStatus.UNKNOWN,
        transport=RequirementStatus.UNKNOWN,
        reachability=RequirementStatus.UNKNOWN,
        status=AvailabilityStatus.UNKNOWN,
        reasons=(AvailabilityReason.STATUS_UNKNOWN,),
    )


def _exact_method(plan_record: PlanRecord, registry: ProtocolRegistry) -> MethodSpec | None:
    return next(
        (item for item in registry.methods if item.ref == plan_record.plan.method),
        None,
    )


def _exact_capability(
    step: CompiledStep,
    registry: ProtocolRegistry,
) -> CapabilitySpec | None:
    return next(
        (item for item in registry.capabilities if item.ref == step.capability),
        None,
    )


def _exact_implementation(
    reference: ImplementationRef,
    registry: ProtocolRegistry,
) -> ImplementationSpec | None:
    return next((item for item in registry.implementations if item.ref == reference), None)


def _source_value(
    source: RecipeValueSource,
    *,
    method_values: Mapping[str, Any],
    step_outputs: Mapping[str, Mapping[str, Any]],
) -> Any:
    if source.kind == "method_input":
        assert source.field is not None
        return method_values[source.field]
    if source.kind == "step_output":
        assert source.step_id is not None and source.field is not None
        return step_outputs[source.step_id][source.field]
    return source.value


def _step_inputs(
    step: CompiledStep,
    *,
    method_values: Mapping[str, Any],
    step_outputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        binding.target_field: _source_value(
            binding.source,
            method_values=method_values,
            step_outputs=step_outputs,
        )
        for binding in step.input_bindings
    }


def _method_output(
    plan_record: PlanRecord,
    *,
    step_outputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    method_values = (
        dict(plan_record.plan.resolved_financial_inputs)
        | dict(plan_record.plan.resolved_conventions)
    )
    return {
        binding.target_field: _source_value(
            binding.source,
            method_values=method_values,
            step_outputs=step_outputs,
        )
        for binding in plan_record.plan.result_bindings
    }


def _safe_failure(
    code: ExecutionFailureCode,
    message: str,
    *,
    retryable: bool = False,
) -> ExecutionFailure:
    return ExecutionFailure(code=code, message=message, retryable=retryable)


def _policy_matches(plan_record: PlanRecord, policy: ResolutionPolicy) -> bool:
    capability_hashes = {
        item.capability.contract_hash for item in plan_record.plan.steps
    }
    try:
        return relevant_policy(policy, capability_hashes).ref == plan_record.plan.policy
    except ValueError:
        return False


def _plan_matches_method(plan_record: PlanRecord, method: MethodSpec | None) -> bool:
    """Require the compiled dataflow to be the exact registered Method recipe.

    Resolution may add implementation, adapter, backend, policy, and availability bindings; it
    may not alter financial dataflow.  This preflight prevents a forged retained PlanRecord from
    executing another recipe while carrying a valid MethodRef.
    """

    if method is None or plan_record.plan.result_bindings != method.recipe.result_bindings:
        return False
    if len(plan_record.plan.steps) != len(method.recipe.steps):
        return False
    return all(
        compiled.step_id == authored.step_id
        and compiled.capability == authored.capability
        and compiled.depends_on == authored.depends_on
        and compiled.input_bindings == authored.input_bindings
        for compiled, authored in zip(
            plan_record.plan.steps,
            method.recipe.steps,
            strict=True,
        )
    )


def _invoke_adapter(
    catalog: ExactAdapterCatalog,
    request: AdapterExecutionRequest,
    executor: RuntimeIdentity,
) -> AdapterExecutionResult:
    try:
        result = catalog.invoke(request)
    except Exception:
        return AdapterExecutionFailure(
            request_hash=request.request_hash,
            adapter_runtime=executor,
            failure=_safe_failure(
                ExecutionFailureCode.ADAPTER_UNAVAILABLE,
                "The exact trusted adapter could not be invoked.",
            ),
        )
    if result.request_hash != request.request_hash:
        return AdapterExecutionFailure(
            request_hash=request.request_hash,
            adapter_runtime=executor,
            failure=_safe_failure(
                ExecutionFailureCode.INTERNAL_FAILURE,
                "The adapter result did not bind the exact invocation request.",
            ),
        )
    if isinstance(result, AdapterExecutionSuccess):
        expected_warning_source = WarningSource(
            kind=WarningSourceKind.ADAPTER,
            subject_id=request.adapter.id,
        )
        allowed_provider_bindings = {
            (binding.role, binding.backend)
            for binding in request.backend_bindings
            if binding.role != BackendRole.RUNTIME
        }
        if any(
            warning.source != expected_warning_source for warning in result.warnings
        ) or any(
            (interaction.backend_role, interaction.backend)
            not in allowed_provider_bindings
            for interaction in result.provider_interactions
        ):
            return AdapterExecutionFailure(
                request_hash=request.request_hash,
                adapter_runtime=executor,
                failure=_safe_failure(
                    ExecutionFailureCode.INTERNAL_FAILURE,
                    "The adapter result contained facts outside its compiled boundary.",
                ),
            )
    return result


def _failed_step(
    *,
    plan_record: PlanRecord,
    execution_request: PlanExecutionRequest,
    step: CompiledStep,
    availability: ImplementationAvailability,
    failure: ExecutionFailure,
    executor: RuntimeIdentity,
    clock: Callable[[], datetime],
    started_at: datetime,
    input_validation: CanonicalValidation | None = None,
    canonical_inputs: Mapping[str, Any] | None = None,
    adapter_request: AdapterExecutionRequest | None = None,
    adapter_result: AdapterExecutionResult | None = None,
    output_validation: CanonicalValidation | None = None,
    warnings: Sequence[ValidationWarning] = (),
    dependency_steps: Sequence[StepRef] = (),
) -> StepRecord:
    return StepRecord(
        status=StepStatus.FAILED,
        plan=plan_record.ref,
        execution_request_hash=execution_request.request_hash,
        method=plan_record.plan.method,
        compiled_step=step,
        canonical_inputs=dict(canonical_inputs or {}),
        dependency_steps=tuple(
            sorted(dependency_steps, key=lambda item: item.reference)
        ),
        input_validation=(input_validation or _not_performed(executor)),
        execution_availability=availability,
        adapter_request=adapter_request,
        adapter_result=adapter_result,
        output_validation=(output_validation or _not_performed(executor)),
        failure=failure,
        warnings=tuple(
            sorted(
                warnings,
                key=lambda item: (
                    item.source.kind.value,
                    item.source.subject_id,
                    item.code,
                    item.field or "",
                ),
            )
        ),
        executor=executor,
        started_at=started_at,
        finished_at=clock(),
    )


def _skipped_step(
    *,
    plan_record: PlanRecord,
    execution_request: PlanExecutionRequest,
    step: CompiledStep,
    availability: ImplementationAvailability,
    executor: RuntimeIdentity,
    clock: Callable[[], datetime],
    started_at: datetime,
    dependency_steps: Sequence[StepRef],
) -> StepRecord:
    return StepRecord(
        status=StepStatus.SKIPPED,
        plan=plan_record.ref,
        execution_request_hash=execution_request.request_hash,
        method=plan_record.plan.method,
        compiled_step=step,
        canonical_inputs={},
        dependency_steps=tuple(
            sorted(dependency_steps, key=lambda item: item.reference)
        ),
        input_validation=_not_performed(executor),
        execution_availability=availability,
        output_validation=_not_performed(executor),
        failure=_safe_failure(
            ExecutionFailureCode.DEPENDENCY_FAILED,
            "A required recipe dependency did not produce a canonical result.",
        ),
        executor=executor,
        started_at=started_at,
        finished_at=clock(),
    )


def _merge_warnings(
    *groups: Sequence[ValidationWarning],
) -> tuple[ValidationWarning, ...]:
    selected: dict[tuple[WarningSourceKind, str, str, str | None], ValidationWarning] = {}
    for warning in (item for group in groups for item in group):
        key = (
            warning.source.kind,
            warning.source.subject_id,
            warning.code,
            warning.field,
        )
        existing = selected.get(key)
        if existing is not None and existing != warning:
            raise ValueError("warning identity has contradictory messages")
        selected[key] = warning
    return tuple(
        sorted(
            selected.values(),
            key=lambda item: (
                item.source.kind.value,
                item.source.subject_id,
                item.code,
                item.field or "",
            ),
        )
    )


def execute_plan(
    plan_record: PlanRecord,
    *,
    registry: ProtocolRegistry,
    policy: ResolutionPolicy,
    availability: AvailabilitySnapshot,
    adapter_catalog: ExactAdapterCatalog,
    record_store: MethodRecordStore,
    executor: RuntimeIdentity,
    requester: RuntimeIdentity | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> RunRecord:
    """Execute one exact compiled plan and publish complete immutable records.

    There is intentionally no resolution-preference or fallback argument. A policy change,
    missing artifact, unavailable implementation, or adapter failure terminates that exact step;
    another implementation can run only after the caller compiles and stores another plan.
    """

    record_store.publish_plan(plan_record)
    started_at = clock()
    execution_request = PlanExecutionRequest(
        plan=plan_record.ref,
        plan_record_hash=plan_record.record_hash,
        requester=requester or executor,
        requested_at=started_at,
    )
    method = _exact_method(plan_record, registry)
    compiler_valid = plan_record.compiler == executor
    method_plan_valid = _plan_matches_method(plan_record, method)
    policy_valid = _policy_matches(plan_record, policy)
    method_values = (
        dict(plan_record.plan.resolved_financial_inputs)
        | dict(plan_record.plan.resolved_conventions)
    )
    method_input_validation = _not_performed(
        executor,
        schema_hash=(
            UNAVAILABLE_SCHEMA_HASH
            if method is None
            else _schema_hash(method.input_schema)
        ),
    )
    method_input_warnings: tuple[ValidationWarning, ...] = ()
    if method is not None:
        method_input_validation, method_input_warnings = _canonical_validation(
            method_values,
            schema=method.input_schema,
            constraints=method.constraints,
            target=PortDirection.INPUT,
            validator=executor,
            subject_id=method.id,
            subject_kind=WarningSourceKind.METHOD,
        )
    method_inputs_valid = bool(
        method_input_validation.status == ValidationStatus.PASSED
        and method_input_warnings == plan_record.plan.warnings
    )
    step_outputs: dict[str, Mapping[str, Any]] = {}
    step_records: list[StepRecord] = []

    for step in plan_record.plan.steps:
        step_started = clock()
        capability = _exact_capability(step, registry)
        implementation = _exact_implementation(step.implementation, registry)
        try:
            current_availability = (
                _unknown_availability(step)
                if implementation is None
                else implementation_availability(
                    availability,
                    implementation,
                    registry.backends,
                )
            )
        except ValueError:
            current_availability = _unknown_availability(step)
        dependency_records = tuple(
            item
            for dependency_id in step.depends_on
            for item in step_records
            if item.compiled_step.step_id == dependency_id
        )
        dependency_refs = tuple(item.ref for item in dependency_records)
        if any(item.status != StepStatus.SUCCEEDED for item in dependency_records):
            record = _skipped_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                executor=executor,
                clock=clock,
                started_at=step_started,
                dependency_steps=dependency_refs,
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue
        if not policy_valid:
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.POLICY_DRIFT,
                    "The active policy no longer matches the compiled policy slice.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                dependency_steps=dependency_refs,
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue
        if not compiler_valid or not method_plan_valid:
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.ARTIFACT_MISMATCH,
                    "The compiled dataflow does not match the exact registered Method recipe.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                dependency_steps=dependency_refs,
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue
        if not method_inputs_valid:
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.INPUT_VALIDATION_FAILED,
                    "The resolved Method inputs failed trusted execution-time validation.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                dependency_steps=dependency_refs,
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue
        if method is None or capability is None or implementation is None:
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.ARTIFACT_MISMATCH,
                    "The compiled registry identity is not present in the active registry.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                dependency_steps=dependency_refs,
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue
        try:
            canonical_inputs = _step_inputs(
                step,
                method_values=method_values,
                step_outputs=step_outputs,
            )
        except (KeyError, TypeError, ValueError):
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.INPUT_MAPPING_FAILED,
                    "The compiled recipe input binding could not be materialized.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                dependency_steps=dependency_refs,
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue
        input_validation, input_warnings = _canonical_validation(
            canonical_inputs,
            schema=capability.input_schema,
            constraints=capability.constraints,
            target=PortDirection.INPUT,
            validator=executor,
            subject_id=capability.id,
            subject_kind=WarningSourceKind.CAPABILITY,
        )
        if input_validation.status != ValidationStatus.PASSED:
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.INPUT_VALIDATION_FAILED,
                    "Canonical step input validation failed.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                input_validation=input_validation,
                canonical_inputs=canonical_inputs,
                warnings=input_warnings,
                dependency_steps=dependency_refs,
                output_validation=_not_performed(
                    executor,
                    schema_hash=_schema_hash(capability.output_schema),
                ),
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue
        if current_availability.status != AvailabilityStatus.AVAILABLE:
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.IMPLEMENTATION_UNAVAILABLE,
                    "The exact compiled implementation is not currently available.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                input_validation=input_validation,
                canonical_inputs=canonical_inputs,
                warnings=input_warnings,
                dependency_steps=dependency_refs,
                output_validation=_not_performed(
                    executor,
                    schema_hash=_schema_hash(capability.output_schema),
                ),
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue

        adapter_request = AdapterExecutionRequest(
            plan=plan_record.ref,
            execution_request_hash=execution_request.request_hash,
            step_id=step.step_id,
            capability=step.capability,
            implementation=step.implementation,
            adapter=step.adapter,
            backend_bindings=step.backend_bindings,
            transport=step.transport,
            locality=step.locality,
            execution_availability=current_availability,
            canonical_inputs=canonical_inputs,
            dependency_steps=tuple(
                sorted(dependency_refs, key=lambda item: item.reference)
            ),
        )
        adapter_result = _invoke_adapter(adapter_catalog, adapter_request, executor)
        if isinstance(adapter_result, AdapterExecutionFailure):
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=adapter_result.failure,
                executor=executor,
                clock=clock,
                started_at=step_started,
                input_validation=input_validation,
                canonical_inputs=canonical_inputs,
                adapter_request=adapter_request,
                adapter_result=adapter_result,
                warnings=input_warnings,
                dependency_steps=dependency_refs,
                output_validation=_not_performed(
                    executor,
                    schema_hash=_schema_hash(capability.output_schema),
                ),
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue

        output_validation, output_warnings = _canonical_validation(
            adapter_result.canonical_output,
            schema=capability.output_schema,
            constraints=capability.constraints,
            target=PortDirection.OUTPUT,
            validator=executor,
            subject_id=capability.id,
            subject_kind=WarningSourceKind.CAPABILITY,
        )
        step_warnings = _merge_warnings(
            input_warnings,
            output_warnings,
            adapter_result.warnings,
        )
        if output_validation.status != ValidationStatus.PASSED:
            record = _failed_step(
                plan_record=plan_record,
                execution_request=execution_request,
                step=step,
                availability=current_availability,
                failure=_safe_failure(
                    ExecutionFailureCode.OUTPUT_VALIDATION_FAILED,
                    "Canonical step output validation failed.",
                ),
                executor=executor,
                clock=clock,
                started_at=step_started,
                input_validation=input_validation,
                canonical_inputs=canonical_inputs,
                adapter_request=adapter_request,
                adapter_result=adapter_result,
                output_validation=output_validation,
                warnings=step_warnings,
                dependency_steps=dependency_refs,
            )
            record_store.publish_step(record)
            step_records.append(record)
            continue

        record = StepRecord(
            status=StepStatus.SUCCEEDED,
            plan=plan_record.ref,
            execution_request_hash=execution_request.request_hash,
            method=plan_record.plan.method,
            compiled_step=step,
            canonical_inputs=canonical_inputs,
            dependency_steps=tuple(
                sorted(dependency_refs, key=lambda item: item.reference)
            ),
            input_validation=input_validation,
            execution_availability=current_availability,
            adapter_request=adapter_request,
            adapter_result=adapter_result,
            output_validation=output_validation,
            canonical_output=adapter_result.canonical_output,
            warnings=step_warnings,
            executor=executor,
            started_at=step_started,
            finished_at=clock(),
        )
        record_store.publish_step(record)
        step_records.append(record)
        step_outputs[step.step_id] = adapter_result.canonical_output

    first_failure = next(
        (item.failure for item in step_records if item.failure is not None),
        None,
    )
    method_output: dict[str, Any] | None = None
    method_validation = _not_performed(
        executor,
        schema_hash=(
            UNAVAILABLE_SCHEMA_HASH
            if method is None
            else _schema_hash(method.output_schema)
        ),
    )
    method_warnings: tuple[ValidationWarning, ...] = ()
    terminal_failure = first_failure
    if first_failure is None and method is not None:
        try:
            candidate_output = _method_output(
                plan_record,
                step_outputs=step_outputs,
            )
        except (KeyError, TypeError, ValueError):
            terminal_failure = _safe_failure(
                ExecutionFailureCode.OUTPUT_MAPPING_FAILED,
                "The compiled Method output binding could not be materialized.",
            )
        else:
            method_validation, method_warnings = _canonical_validation(
                candidate_output,
                schema=method.output_schema,
                constraints=method.constraints,
                target=PortDirection.OUTPUT,
                validator=executor,
                subject_id=method.id,
                subject_kind=WarningSourceKind.METHOD,
            )
            if method_validation.status == ValidationStatus.PASSED:
                method_output = candidate_output
            else:
                terminal_failure = _safe_failure(
                    ExecutionFailureCode.OUTPUT_VALIDATION_FAILED,
                    "Canonical Method output validation failed.",
                )
    elif first_failure is None:
        terminal_failure = _safe_failure(
            ExecutionFailureCode.ARTIFACT_MISMATCH,
            "The compiled Method identity is absent from the active registry.",
        )

    step_artifacts = tuple(
        sorted(
            (
                artifact
                for step in step_records
                if isinstance(step.adapter_result, AdapterExecutionSuccess)
                for artifact in step.adapter_result.artifacts
            ),
            key=lambda item: item.artifact_id,
        )
    )
    artifact_ids = tuple(item.artifact_id for item in step_artifacts)
    artifacts: tuple[ArtifactRecord, ...]
    if len(set(artifact_ids)) != len(artifact_ids):
        artifacts = ()
        terminal_failure = _safe_failure(
            ExecutionFailureCode.ARTIFACT_ID_CONFLICT,
            "Successful steps emitted colliding run-level artifact identifiers.",
        )
    else:
        artifacts = step_artifacts
    run_warnings = _merge_warnings(
        plan_record.plan.warnings,
        *(item.warnings for item in step_records),
        method_warnings,
    )
    run = RunRecord(
        status=(RunStatus.SUCCEEDED if terminal_failure is None else RunStatus.FAILED),
        plan_record=plan_record,
        execution_request=execution_request,
        steps=tuple(step_records),
        datasets=execution_request.datasets,
        method_input_validation=method_input_validation,
        method_output_validation=method_validation,
        method_warnings=method_warnings,
        canonical_method_output=method_output,
        artifacts=artifacts,
        warnings=run_warnings,
        failure=terminal_failure,
        executor=executor,
        started_at=started_at,
        finished_at=clock(),
    )
    record_store.publish_run(run)
    return run


__all__ = [
    "CANONICAL_SCHEMA_HASH_DOMAIN",
    "ExactAdapterCatalog",
    "execute_plan",
]
