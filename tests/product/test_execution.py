from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from defined_quant.execution import execute_plan
from defined_quant.method_records import MethodRecordStore
from defined_quant.planning import compile_plan
from defined_quant.registry import ProtocolRegistry, load_registry
from defined_quant_protocol.execution import (
    AdapterExecutionFailure,
    AdapterExecutionRequest,
    AdapterExecutionResult,
    AdapterExecutionSuccess,
    ExecutionFailure,
    ExecutionFailureCode,
    RunStatus,
    StepStatus,
)
from defined_quant_protocol.registry import (
    BackendKind,
    BackendRole,
    DataEgress,
    RecipeInputBinding,
    RecipeValueSource,
    RuntimeIdentity,
    RuntimeLocality,
    TransportKind,
)
from defined_quant_protocol.resolution import (
    AvailabilityReason,
    AvailabilitySnapshot,
    AvailabilityStatus,
    BackendRolePolicy,
    CapabilityPolicyRule,
    CompiledPlan,
    ImplementationAvailability,
    InstallationStatus,
    PlanProposal,
    PlanRecord,
    PolicyImplementation,
    RequirementStatus,
    ResolutionPolicy,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 1, tzinfo=UTC)
HASH = "1" * 64
RUNTIME = RuntimeIdentity(name="test_runtime", version="1.0.0", artifact_hash=HASH)


def _registry() -> ProtocolRegistry:
    return load_registry(root=ROOT / "registry").as_protocol_registry()


def _policy(registry: ProtocolRegistry, *, priority: int = 10) -> ResolutionPolicy:
    capability = next(item for item in registry.capabilities if item.id == "returns.simple")
    backend = registry.backends[0]
    implementation = next(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )
    return ResolutionPolicy(
        id="execution_policy",
        version="1.0.0",
        capability_rules=(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=(
                    PolicyImplementation(
                        implementation=implementation.ref,
                        priority=priority,
                    ),
                ),
                backend_roles=(
                    BackendRolePolicy(
                        role=BackendRole.RUNTIME,
                        allowed_backends=(backend.ref,),
                        allowed_kinds=(BackendKind.DQ_NATIVE,),
                        allowed_transports=(TransportKind.IN_PROCESS,),
                        allowed_localities=(RuntimeLocality.LOCAL,),
                        network_allowed=False,
                        allowed_data_egress=(DataEgress.NONE,),
                    ),
                ),
            ),
        ),
    )


def _availability(
    registry: ProtocolRegistry,
    *,
    available: bool = True,
) -> AvailabilitySnapshot:
    implementations = registry.implementations
    return AvailabilitySnapshot(
        snapshot_id="execution_snapshot",
        observed_at=NOW,
        evaluator=RUNTIME,
        implementations=tuple(
            ImplementationAvailability(
                implementation=implementation.ref,
                adapter=implementation.adapter,
                backend_bindings=implementation.backend_bindings,
                enabled=True,
                installation=(
                    InstallationStatus.INSTALLED
                    if available
                    else InstallationStatus.NOT_INSTALLED
                ),
                artifact=RequirementStatus.SATISFIED,
                dependencies=RequirementStatus.SATISFIED,
                credentials=RequirementStatus.NOT_REQUIRED,
                licence=RequirementStatus.NOT_REQUIRED,
                entitlement=RequirementStatus.NOT_REQUIRED,
                transport=RequirementStatus.SATISFIED,
                reachability=RequirementStatus.NOT_REQUIRED,
                status=(
                    AvailabilityStatus.AVAILABLE
                    if available
                    else AvailabilityStatus.UNAVAILABLE
                ),
                reasons=(
                    (AvailabilityReason.READY,)
                    if available
                    else (AvailabilityReason.NOT_INSTALLED,)
                ),
            )
            for implementation in implementations
        ),
    )


def _plan(
    registry: ProtocolRegistry,
    policy: ResolutionPolicy,
    availability: AvailabilitySnapshot,
) -> PlanRecord:
    outcome = compile_plan(
        PlanProposal(
            method_id="dq.market_data.simple_return",
            method_version="1.0.0",
            financial_inputs={"prices": [100.0, 110.0, 99.0]},
            conventions={"price_kind": "adjusted"},
        ),
        registry=registry,
        policy=policy,
        availability=availability,
    )
    assert isinstance(outcome, CompiledPlan)
    return PlanRecord(plan=outcome, compiler=RUNTIME, compiled_at=NOW)


class _SuccessfulCatalog:
    def __init__(self, *, output: dict[str, object] | None = None) -> None:
        self.requests: list[AdapterExecutionRequest] = []
        self.output = output

    def invoke(self, request: AdapterExecutionRequest) -> AdapterExecutionResult:
        self.requests.append(request)
        output = self.output or {
            "returns": [0.1, -0.1],
            "return_kind": "simple",
            "return_timestamps": None,
            "ordering_status": "unverified",
        }
        return AdapterExecutionSuccess(
            request_hash=request.request_hash,
            adapter_runtime=RUNTIME,
            canonical_output=output,
        )


class _FailingCatalog:
    def __init__(self) -> None:
        self.requests: list[AdapterExecutionRequest] = []

    def invoke(self, request: AdapterExecutionRequest) -> AdapterExecutionResult:
        self.requests.append(request)
        return AdapterExecutionFailure(
            request_hash=request.request_hash,
            adapter_runtime=RUNTIME,
            failure=ExecutionFailure(
                code=ExecutionFailureCode.INTERNAL_FAILURE,
                message="The trusted adapter reported a safe failure.",
                retryable=False,
            ),
        )


def test_execute_plan_runs_only_the_compiled_implementation_and_publishes_run() -> None:
    registry = _registry()
    policy = _policy(registry)
    availability = _availability(registry)
    plan = _plan(registry, policy, availability)
    catalog = _SuccessfulCatalog()
    store = MethodRecordStore()

    run = execute_plan(
        plan,
        registry=registry,
        policy=policy,
        availability=availability,
        adapter_catalog=catalog,
        record_store=store,
        executor=RUNTIME,
        clock=lambda: NOW,
    )

    assert run.status == RunStatus.SUCCEEDED
    assert len(catalog.requests) == 1
    assert catalog.requests[0].implementation == plan.plan.steps[0].implementation
    assert catalog.requests[0].runtime_fallback_allowed is False
    assert run.steps[0].status == StepStatus.SUCCEEDED
    assert run.canonical_method_output == {
        "declared_frequency": None,
        "gap_check": "not_assessed",
        "ordering_status": "unverified",
        "price_kind": "adjusted",
        "return_kind": "simple",
        "return_timestamps": None,
        "returns": [0.1, -0.1],
    }
    assert store.get_plan(plan.ref) == plan
    assert store.get_run(run.ref) == run
    assert run.execution_request.request_hash != run.run_hash


def test_runtime_unavailability_fails_without_invoking_or_falling_back() -> None:
    registry = _registry()
    policy = _policy(registry)
    compiled_availability = _availability(registry)
    plan = _plan(registry, policy, compiled_availability)
    catalog = _SuccessfulCatalog()

    run = execute_plan(
        plan,
        registry=registry,
        policy=policy,
        availability=_availability(registry, available=False),
        adapter_catalog=catalog,
        record_store=MethodRecordStore(),
        executor=RUNTIME,
        clock=lambda: NOW,
    )

    assert run.status == RunStatus.FAILED
    assert catalog.requests == []
    assert run.steps[0].failure is not None
    assert run.steps[0].failure.code == ExecutionFailureCode.IMPLEMENTATION_UNAVAILABLE
    assert run.canonical_method_output is None


def test_policy_drift_fails_before_adapter_invocation() -> None:
    registry = _registry()
    compile_policy = _policy(registry)
    availability = _availability(registry)
    plan = _plan(registry, compile_policy, availability)
    catalog = _SuccessfulCatalog()

    run = execute_plan(
        plan,
        registry=registry,
        policy=_policy(registry, priority=11),
        availability=availability,
        adapter_catalog=catalog,
        record_store=MethodRecordStore(),
        executor=RUNTIME,
        clock=lambda: NOW,
    )

    assert run.status == RunStatus.FAILED
    assert catalog.requests == []
    assert run.steps[0].failure is not None
    assert run.steps[0].failure.code == ExecutionFailureCode.POLICY_DRIFT


def test_forged_compiled_dataflow_fails_before_adapter_invocation() -> None:
    registry = _registry()
    policy = _policy(registry)
    availability = _availability(registry)
    original = _plan(registry, policy, availability)
    compiled_step = original.plan.steps[0]
    altered_bindings = tuple(
        RecipeInputBinding(
            target_field=binding.target_field,
            source=(
                RecipeValueSource(kind="authored_constant", value=[1.0, 2.0])
                if binding.target_field == "prices"
                else binding.source
            ),
        )
        for binding in compiled_step.input_bindings
    )
    forged_plan = original.plan.model_copy(
        update={
            "steps": (
                compiled_step.model_copy(update={"input_bindings": altered_bindings}),
            )
        }
    )
    forged_record = PlanRecord(
        plan=forged_plan,
        compiler=original.compiler,
        compiled_at=original.compiled_at,
    )
    catalog = _SuccessfulCatalog()

    run = execute_plan(
        forged_record,
        registry=registry,
        policy=policy,
        availability=availability,
        adapter_catalog=catalog,
        record_store=MethodRecordStore(),
        executor=RUNTIME,
        clock=lambda: NOW,
    )

    assert run.status == RunStatus.FAILED
    assert catalog.requests == []
    assert run.steps[0].failure is not None
    assert run.steps[0].failure.code == ExecutionFailureCode.ARTIFACT_MISMATCH


def test_forged_resolved_method_inputs_fail_before_adapter_invocation() -> None:
    registry = _registry()
    policy = _policy(registry)
    availability = _availability(registry)
    original = _plan(registry, policy, availability)
    forged_inputs = dict(original.plan.resolved_financial_inputs)
    forged_inputs["prices"] = []
    forged_record = PlanRecord(
        plan=original.plan.model_copy(
            update={"resolved_financial_inputs": forged_inputs}
        ),
        compiler=original.compiler,
        compiled_at=original.compiled_at,
    )
    catalog = _SuccessfulCatalog()

    run = execute_plan(
        forged_record,
        registry=registry,
        policy=policy,
        availability=availability,
        adapter_catalog=catalog,
        record_store=MethodRecordStore(),
        executor=RUNTIME,
        clock=lambda: NOW,
    )

    assert run.status == RunStatus.FAILED
    assert run.method_input_validation.status.value == "failed"
    assert catalog.requests == []
    assert run.steps[0].failure is not None
    assert run.steps[0].failure.code == ExecutionFailureCode.INPUT_VALIDATION_FAILED


def test_malformed_adapter_output_cannot_publish_false_success() -> None:
    registry = _registry()
    policy = _policy(registry)
    availability = _availability(registry)
    plan = _plan(registry, policy, availability)
    catalog = _SuccessfulCatalog(output={"returns": [0.1, -0.1]})

    run = execute_plan(
        plan,
        registry=registry,
        policy=policy,
        availability=availability,
        adapter_catalog=catalog,
        record_store=MethodRecordStore(),
        executor=RUNTIME,
        clock=lambda: NOW,
    )

    assert run.status == RunStatus.FAILED
    assert run.steps[0].status == StepStatus.FAILED
    assert run.steps[0].failure is not None
    assert run.steps[0].failure.code == ExecutionFailureCode.OUTPUT_VALIDATION_FAILED
    assert run.steps[0].canonical_output is None


def test_adapter_failure_is_recorded_without_substitution() -> None:
    registry = _registry()
    policy = _policy(registry)
    availability = _availability(registry)
    plan = _plan(registry, policy, availability)
    catalog = _FailingCatalog()

    run = execute_plan(
        plan,
        registry=registry,
        policy=policy,
        availability=availability,
        adapter_catalog=catalog,
        record_store=MethodRecordStore(),
        executor=RUNTIME,
        clock=lambda: NOW,
    )

    assert run.status == RunStatus.FAILED
    assert len(catalog.requests) == 1
    assert run.steps[0].adapter_result is not None
    assert run.steps[0].failure is not None
    assert run.steps[0].failure.code == ExecutionFailureCode.INTERNAL_FAILURE
