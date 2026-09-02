from __future__ import annotations

from datetime import UTC, datetime

import defined_quant_protocol as public_protocol
import pytest
from defined_quant_protocol.execution import (
    AdapterExecutionRequest,
    AdapterExecutionSuccess,
    CanonicalValidation,
    ExecutionFailure,
    ExecutionFailureCode,
    PlanExecutionRequest,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    ValidationIssue,
    ValidationStatus,
)
from defined_quant_protocol.ports import PortDirection
from defined_quant_protocol.registry import (
    AdapterSpec,
    ArtifactPin,
    BackendBinding,
    BackendIdentity,
    BackendKind,
    BackendRole,
    BackendSpec,
    CapabilityKind,
    CapabilitySpec,
    ConstraintSpec,
    CredentialRequirement,
    DataBoundary,
    DataEgress,
    DiscoveryMetadata,
    EntitlementRequirement,
    EvidenceRef,
    EvidenceSubject,
    EvidenceSubjectKind,
    ImplementationSpec,
    LicenseBoundary,
    LicenseKind,
    MethodLifecycle,
    MethodSpec,
    Recipe,
    RecipeInputBinding,
    RecipeOutputBinding,
    RecipeStep,
    RecipeValueSource,
    RuntimeIdentity,
    RuntimeLocality,
    SchemaFieldBinding,
    SupportLevel,
    TransportKind,
    TrustAssertion,
    TrustDimension,
)
from defined_quant_protocol.resolution import (
    AppliedDefault,
    AvailabilityReason,
    AvailabilitySnapshot,
    AvailabilityStatus,
    BackendRolePolicy,
    CandidateDecision,
    CapabilityPolicyRule,
    CompilationOutcome,
    CompiledPlan,
    CompiledStep,
    FallbackBehavior,
    ImplementationAvailability,
    InstallationStatus,
    NeedsInformation,
    OriginReceiptSet,
    OriginVerification,
    PlanProposal,
    PlanRecord,
    PlanRefusal,
    PlanRefusalCode,
    PolicyImplementation,
    PreferenceDimension,
    PreferenceMode,
    PreferenceOrigin,
    PreferenceOriginReceipt,
    RequirementStatus,
    ResolutionConstraint,
    ResolutionConstraintSet,
    ResolutionDecision,
    ResolutionExplanationCode,
    ResolutionPolicy,
    ResolutionQuestion,
    ResolutionQuestionCode,
    ResolutionScope,
    ValidationWarning,
    WarningSource,
    WarningSourceKind,
)
from pydantic import TypeAdapter, ValidationError

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64
_SESSION = "f" * 64
_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_methods_first_types_are_the_canonical_public_exports() -> None:
    assert public_protocol.CapabilityKind is CapabilityKind
    assert public_protocol.TrustDimension is TrustDimension
    assert public_protocol.AvailabilityStatus is AvailabilityStatus
    assert public_protocol.PlanRefusalCode is PlanRefusalCode
    assert public_protocol.LegacyCapabilityKind is not CapabilityKind
    assert public_protocol.LegacyTrustDimension is not TrustDimension


def _port(direction: str, concept: str) -> dict[str, str]:
    return {
        "direction": direction,
        "concept": concept,
        "unit": "price" if concept == "price_series" else "decimal",
        "shape": "ordered_series",
        "cardinality": "one_or_more",
        "convention": (
            "ordered_positive_prices"
            if concept == "price_series"
            else "simple_periodic_return"
        ),
        "ordering": "preserve_source_order",
        "frequency": "inherited",
        "provenance_requirement": "not_required",
    }


@pytest.mark.parametrize(
    "measure",
    (
        "all_valid_calendar_month_labels",
        "interval_count",
        "is_consecutive_calendar_months",
    ),
)
def test_capability_constraints_support_closed_calendar_month_measures(
    measure: str,
) -> None:
    constraint = ConstraintSpec(
        id="calendar_month_contract",
        target=PortDirection.INPUT,
        severity="blocking",
        expression={
            "left": {"field": "months", "measure": measure},
            "op": "eq",
            "right": {"value": True},
        },
        message="Calendar month labels must satisfy the declared invariant.",
    )

    assert constraint.expression["left"]["measure"] == measure


def test_interval_count_measures_adjacent_observation_pairs() -> None:
    from defined_quant.constraint_evaluation import evaluate_constraint

    expression = {
        "left": {"field": "returns", "measure": "count"},
        "op": "eq",
        "right": {"field": "observations", "measure": "interval_count"},
    }

    assert evaluate_constraint(
        expression,
        {"returns": [0.1, 0.2], "observations": [100.0, 110.0, 132.0]},
    )
    assert not evaluate_constraint(
        expression,
        {"returns": [0.1], "observations": [100.0, 110.0, 132.0]},
    )


def _object_schema(
    field: str,
    *,
    direction: str,
    concept: str,
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [field],
        "properties": {
            field: {
                "type": "array",
                "minItems": 1,
                "items": {"type": "number"},
                "x-defined-quant-port": _port(direction, concept),
            }
        },
    }


def _registry_records() -> tuple[
    CapabilitySpec,
    MethodSpec,
    BackendSpec,
    AdapterSpec,
    ImplementationSpec,
]:
    capability = CapabilitySpec(
        id="returns.simple",
        version="1.0.0",
        title="Simple returns",
        kind=CapabilityKind.CALCULATION,
        summary="Calculate adjacent simple returns.",
        input_schema=_object_schema(
            "prices",
            direction="input",
            concept="price_series",
        ),
        output_schema=_object_schema(
            "returns",
            direction="output",
            concept="periodic_return_series",
        ),
    )
    recipe = Recipe(
        steps=(
            RecipeStep(
                step_id="calculate",
                capability=capability.ref,
                input_bindings=(
                    RecipeInputBinding(
                        target_field="prices",
                        source=RecipeValueSource(kind="method_input", field="prices"),
                    ),
                ),
            ),
        ),
        result_bindings=(
            RecipeOutputBinding(
                target_field="returns",
                source=RecipeValueSource(
                    kind="step_output",
                    step_id="calculate",
                    field="returns",
                ),
            ),
        ),
    )
    method = MethodSpec(
        id="dq.market_data.simple_return",
        version="1.0.0",
        title="Simple Return",
        lifecycle=MethodLifecycle.EXPERIMENTAL,
        summary="Calculate simple returns from caller-supplied prices.",
        category="market_data",
        discovery=DiscoveryMetadata(
            slug="simple_return",
            website_slug="simple-return",
            aliases=("arithmetic return",),
            intents=("calculate_returns",),
            input_concepts=("price_series",),
            output_concepts=("return_series",),
            use_when=("Prices are already ordered and validated.",),
            do_not_use_when=("The user requested log returns.",),
            unsupported_scope=("No data acquisition.",),
        ),
        input_schema=capability.input_schema,
        output_schema=capability.output_schema,
        formula="r_t = (P_t - P_(t-1)) / P_(t-1)",
        methodology=("Calculate one result per adjacent pair.",),
        interpretation="Values are decimal simple returns.",
        schema_bindings=(
            SchemaFieldBinding(
                method_field="prices",
                direction=PortDirection.INPUT,
                capability=capability.ref,
                capability_field="prices",
            ),
            SchemaFieldBinding(
                method_field="returns",
                direction=PortDirection.OUTPUT,
                capability=capability.ref,
                capability_field="returns",
            ),
        ),
        recipe=recipe,
    )
    backend = BackendSpec(
        id="dq_native",
        version="1.0.0",
        title="DQ native",
        kind=BackendKind.DQ_NATIVE,
        identity=BackendIdentity(name="Defined Quant"),
        locality=RuntimeLocality.LOCAL,
        transports=(TransportKind.IN_PROCESS,),
        requires_network=False,
        credential_requirement=CredentialRequirement.NONE,
        licensing=LicenseBoundary(kind=LicenseKind.OPEN_SOURCE, identifier="MIT"),
        entitlement=EntitlementRequirement.NONE,
        data_boundary=DataBoundary(
            egress=DataEgress.NONE,
            accepts_restricted_data=True,
        ),
    )
    artifact = ArtifactPin(
        distribution="defined-quant",
        version="1.0.0",
        artifact_hash=_A,
    )
    adapter_evidence = EvidenceRef(
        id="evidence.adapter.simple_return",
        version="1.0.0",
        evidence_hash=_E,
        subject=EvidenceSubject(
            kind=EvidenceSubjectKind.ADAPTER,
            id="dq_native.simple_return",
            version="1.0.0",
        ),
    )
    implementation_evidence = EvidenceRef(
        id="evidence.implementation.simple_return",
        version="1.0.0",
        evidence_hash=_E,
        subject=EvidenceSubject(
            kind=EvidenceSubjectKind.IMPLEMENTATION,
            id="dq_native.simple_return",
            version="1.0.0",
        ),
    )
    adapter = AdapterSpec(
        id="dq_native.simple_return",
        version="1.0.0",
        title="DQ simple return adapter",
        family="dq_native",
        backend=backend.ref,
        distribution="defined-quant",
        contract_version="1.0.0",
        dispatch_key="dq_native_simple_return",
        transports=(TransportKind.IN_PROCESS,),
        evidence_refs=(adapter_evidence,),
    )
    runtime_binding = BackendBinding(
        role=BackendRole.RUNTIME,
        backend=backend.ref,
        transport=TransportKind.IN_PROCESS,
        locality=RuntimeLocality.LOCAL,
    )
    implementation = ImplementationSpec(
        id="dq_native.simple_return",
        version="1.0.0",
        title="DQ-native simple return",
        capability=capability.ref,
        backend_bindings=(runtime_binding,),
        adapter=adapter.ref,
        artifact=artifact,
        support_level=SupportLevel.FULL,
        trust=(
            TrustAssertion(
                dimension=TrustDimension.CONFORMANCE_TESTED,
                evidence=implementation_evidence,
            ),
        ),
        evidence_refs=(implementation_evidence,),
    )
    return capability, method, backend, adapter, implementation


def _runtime() -> RuntimeIdentity:
    return RuntimeIdentity(name="test_runtime", version="1.0.0", artifact_hash=_B)


def _availability(
    implementation: ImplementationSpec,
) -> ImplementationAvailability:
    return ImplementationAvailability(
        implementation=implementation.ref,
        adapter=implementation.adapter,
        backend_bindings=implementation.backend_bindings,
        enabled=True,
        installation=InstallationStatus.INSTALLED,
        artifact=RequirementStatus.SATISFIED,
        dependencies=RequirementStatus.SATISFIED,
        credentials=RequirementStatus.NOT_REQUIRED,
        licence=RequirementStatus.NOT_REQUIRED,
        entitlement=RequirementStatus.NOT_REQUIRED,
        transport=RequirementStatus.SATISFIED,
        reachability=RequirementStatus.NOT_REQUIRED,
        status=AvailabilityStatus.AVAILABLE,
        reasons=(AvailabilityReason.READY,),
    )


def _compiled_records() -> tuple[
    CapabilitySpec,
    MethodSpec,
    ImplementationSpec,
    ImplementationAvailability,
    PlanProposal,
    CompiledPlan,
    PlanRecord,
]:
    capability, method, backend, _, implementation = _registry_records()
    availability = _availability(implementation)
    snapshot = AvailabilitySnapshot(
        snapshot_id="local",
        observed_at=_NOW,
        evaluator=_runtime(),
        implementations=(availability,),
    )
    role_policy = BackendRolePolicy(
        role=BackendRole.RUNTIME,
        allowed_backends=(backend.ref,),
        allowed_kinds=(BackendKind.DQ_NATIVE,),
        allowed_transports=(TransportKind.IN_PROCESS,),
        allowed_localities=(RuntimeLocality.LOCAL,),
        network_allowed=False,
        allowed_data_egress=(DataEgress.NONE,),
    )
    policy = ResolutionPolicy(
        id="local_default",
        version="1.0.0",
        capability_rules=(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=(
                    PolicyImplementation(implementation=implementation.ref, priority=0),
                ),
                backend_roles=(role_policy,),
                required_trust_dimensions=(TrustDimension.CONFORMANCE_TESTED,),
            ),
        ),
    )
    raw_constraint = ResolutionConstraint(
        constraint_id="require_native",
        scope=ResolutionScope(step_id="calculate"),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.REQUIRED,
        targets=("dq_native.simple_return",),
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=FallbackBehavior.FORBIDDEN,
    )
    proposal = PlanProposal(
        method_id=method.id,
        method_version=method.version,
        financial_inputs={"prices": [100.0, 105.0]},
        conventions={},
        resolution_constraints=ResolutionConstraintSet(constraints=(raw_constraint,)),
    )
    receipt = PreferenceOriginReceipt(
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        verified_origin=PreferenceOrigin.USER_EXPLICIT,
        verification=OriginVerification.USER_CONFIRMED,
        constraint_hash=raw_constraint.constraint_hash,
        session_binding_hash=_SESSION,
        issuer=_runtime(),
        issued_at=_NOW,
    )
    receipt_set = OriginReceiptSet(
        session_binding_hash=_SESSION,
        receipts=(receipt,),
    )
    assert receipt_set.receipts == (receipt,)
    effective = raw_constraint.model_copy(update={"origin_receipt": receipt})
    candidate = CandidateDecision(
        implementation=implementation.ref,
        adapter=implementation.adapter,
        backend_bindings=implementation.backend_bindings,
        priority=0,
        preference_rank=0,
        capability_match=True,
        user_constraints_satisfied=True,
        policy_allowed=True,
        trust_satisfied=True,
        satisfied_trust_dimensions=(TrustDimension.CONFORMANCE_TESTED,),
        applicability_satisfied=True,
        backend_kind_allowed=True,
        adapter_family_allowed=True,
        transport_allowed=True,
        locality_allowed=True,
        network_allowed=True,
        data_handling_allowed=True,
        availability=availability,
        selected=True,
    )
    decision = ResolutionDecision(
        step_id="calculate",
        capability=capability.ref,
        constraint_hashes=(raw_constraint.constraint_hash,),
        candidates=(candidate,),
        selected_implementation=implementation.ref,
        selected_adapter=implementation.adapter,
        selected_backend_bindings=implementation.backend_bindings,
        selected_transport=TransportKind.IN_PROCESS,
        selected_locality=RuntimeLocality.LOCAL,
        fallback_permitted=False,
        fallback_used=False,
        explanation_code=ResolutionExplanationCode.REQUIRED_EXACT,
        explanation="The exact required implementation is eligible.",
    )
    compiled_step = CompiledStep(
        step_id="calculate",
        capability=capability.ref,
        implementation=implementation.ref,
        adapter=implementation.adapter,
        backend_bindings=implementation.backend_bindings,
        transport=TransportKind.IN_PROCESS,
        locality=RuntimeLocality.LOCAL,
        input_bindings=method.recipe.steps[0].input_bindings,
        resolution=decision,
    )
    plan = CompiledPlan(
        method=method.ref,
        proposal_hash=proposal.proposal_hash,
        policy=policy.ref,
        availability_snapshot=snapshot.ref,
        original_user_constraints=proposal.resolution_constraints,
        effective_constraints=ResolutionConstraintSet(constraints=(effective,)),
        resolved_financial_inputs={"prices": [100.0, 105.0]},
        resolved_conventions={},
        applied_defaults=(),
        steps=(compiled_step,),
        result_bindings=method.recipe.result_bindings,
    )
    plan_record = PlanRecord(plan=plan, compiler=_runtime(), compiled_at=_NOW)
    return (
        capability,
        method,
        implementation,
        availability,
        proposal,
        plan,
        plan_record,
    )


def test_registry_identities_separate_method_capability_adapter_and_implementation() -> None:
    capability, method, backend, adapter, implementation = _registry_records()

    assert method.ref.contract_hash == method.contract_hash
    assert capability.ref.contract_hash == capability.contract_hash
    assert adapter.ref.backend == backend.ref
    assert implementation.runtime_backend == backend.ref
    assert implementation.capability == capability.ref
    assert len({method.contract_hash, capability.contract_hash, implementation.spec_hash}) == 3
    assert method.discovery.website_slug == "simple-return"
    assert method.lifecycle == MethodLifecycle.EXPERIMENTAL


def test_protocol_specs_reject_cross_subject_evidence_and_unlisted_trust() -> None:
    _, _, _, adapter, implementation = _registry_records()
    wrong_adapter_evidence = adapter.evidence_refs[0].model_copy(
        update={
            "subject": EvidenceSubject(
                kind=EvidenceSubjectKind.IMPLEMENTATION,
                id=implementation.id,
                version=implementation.version,
            )
        }
    )
    with pytest.raises(ValidationError, match="exact adapter"):
        adapter.model_copy(update={"evidence_refs": (wrong_adapter_evidence,)})

    wrong_implementation_evidence = implementation.evidence_refs[0].model_copy(
        update={
            "subject": EvidenceSubject(
                kind=EvidenceSubjectKind.ADAPTER,
                id=adapter.id,
                version=adapter.version,
            )
        }
    )
    with pytest.raises(ValidationError, match="exact implementation"):
        implementation.model_copy(
            update={"evidence_refs": (wrong_implementation_evidence,)}
        )

    uncatalogued_evidence = implementation.evidence_refs[0].model_copy(
        update={"id": "evidence.implementation.uncatalogued", "evidence_hash": _B}
    )
    with pytest.raises(ValidationError, match="must cite implementation evidence"):
        implementation.model_copy(
            update={
                "trust": (
                    TrustAssertion(
                        dimension=TrustDimension.CONFORMANCE_TESTED,
                        evidence=uncatalogued_evidence,
                    ),
                )
            }
        )


def test_raw_proposal_cannot_self_attest_origin_or_supply_automatic_or_control_material() -> None:
    _, method, _, _, _ = _registry_records()
    constraint = ResolutionConstraint(
        constraint_id="require_native",
        scope=ResolutionScope(all_steps=True),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.REQUIRED,
        targets=("dq_native.simple_return",),
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
    )
    receipt = PreferenceOriginReceipt(
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        verified_origin=PreferenceOrigin.USER_EXPLICIT,
        verification=OriginVerification.USER_CONFIRMED,
        constraint_hash=constraint.constraint_hash,
        session_binding_hash=_SESSION,
        issuer=_runtime(),
        issued_at=_NOW,
    )
    self_attested = constraint.model_copy(update={"origin_receipt": receipt})
    with pytest.raises(ValidationError, match="host-trusted receipt set"):
        PlanProposal(
            method_id=method.id,
            method_version=method.version,
            financial_inputs={"prices": [100.0, 101.0]},
            conventions={},
            resolution_constraints=ResolutionConstraintSet(
                constraints=(self_attested,),
            ),
        )

    automatic = ResolutionConstraint(
        constraint_id="auto",
        scope=ResolutionScope(all_steps=True),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.AUTOMATIC,
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=FallbackBehavior.ANY_POLICY_ELIGIBLE,
    )
    with pytest.raises(ValidationError, match="represented by omitted"):
        PlanProposal(
            method_id=method.id,
            method_version=method.version,
            financial_inputs={"prices": [100.0, 101.0]},
            conventions={},
            resolution_constraints=ResolutionConstraintSet(constraints=(automatic,)),
        )

    for forbidden in (
        "https://arbitrary.example/data",
        "SELECT price FROM secret_table",
        "import requests",
        "password=hunter2",
    ):
        with pytest.raises(ValidationError, match="cannot contain"):
            PlanProposal(
                method_id=method.id,
                method_version=method.version,
                financial_inputs={"prices": [100.0, 101.0], "label": forbidden},
                conventions={},
            )


def test_compilation_outcomes_are_typed_and_plan_reference_is_unsuffixed() -> None:
    _, _, _, _, proposal, plan, _ = _compiled_records()
    adapter = TypeAdapter(CompilationOutcome)

    parsed = adapter.validate_python(plan.model_dump(mode="json"))
    assert isinstance(parsed, CompiledPlan)
    assert plan.ref.reference == f"dqplan:{plan.plan_hash}"
    assert ":v" not in plan.ref.reference

    needs = NeedsInformation(
        proposal=proposal,
        questions=(
            ResolutionQuestion(
                question_id="confirm_fallback",
                code=ResolutionQuestionCode.FALLBACK_PERMISSION,
                field_path="resolution_constraints.require_native.fallback",
                description="May another eligible implementation be used?",
                allowed_values=(False, True),
            ),
        ),
    )
    assert isinstance(adapter.validate_python(needs.model_dump(mode="json")), NeedsInformation)
    refusal = PlanRefusal(
        proposal=proposal,
        code=PlanRefusalCode.REQUIRED_CHOICE_UNAVAILABLE,
        message="The required implementation is unavailable.",
    )
    assert isinstance(adapter.validate_python(refusal.model_dump(mode="json")), PlanRefusal)


def test_successful_run_binds_plan_adapter_step_and_run_records() -> None:
    capability, method, _, availability, _, plan, plan_record = _compiled_records()
    execution_request = PlanExecutionRequest(
        plan=plan.ref,
        plan_record_hash=plan_record.record_hash,
        requester=_runtime(),
        requested_at=_NOW,
    )
    adapter_request = AdapterExecutionRequest(
        plan=plan.ref,
        execution_request_hash=execution_request.request_hash,
        step_id="calculate",
        capability=capability.ref,
        implementation=plan.steps[0].implementation,
        adapter=plan.steps[0].adapter,
        backend_bindings=plan.steps[0].backend_bindings,
        transport=plan.steps[0].transport,
        locality=plan.steps[0].locality,
        execution_availability=availability,
        canonical_inputs={"prices": [100.0, 105.0]},
    )
    adapter_result = AdapterExecutionSuccess(
        request_hash=adapter_request.request_hash,
        adapter_runtime=_runtime(),
        canonical_output={"returns": [0.05]},
    )
    passed_capability = CanonicalValidation(
        status=ValidationStatus.PASSED,
        schema_hash=capability.contract_hash,
        validator=_runtime(),
    )
    capability_warning = ValidationWarning(
        source=WarningSource(
            kind=WarningSourceKind.CAPABILITY,
            subject_id=capability.id,
        ),
        code="runtime_scope_warning",
        message="Runtime canonical validation recorded a bounded warning.",
    )
    method_warning = ValidationWarning(
        source=WarningSource(
            kind=WarningSourceKind.METHOD,
            subject_id=method.id,
        ),
        code="method_output_scope_warning",
        field="returns",
        message="Method-output validation recorded a bounded warning.",
    )
    step = StepRecord(
        status=StepStatus.SUCCEEDED,
        plan=plan.ref,
        execution_request_hash=execution_request.request_hash,
        method=method.ref,
        compiled_step=plan.steps[0],
        canonical_inputs={"prices": [100.0, 105.0]},
        input_validation=passed_capability,
        execution_availability=availability,
        adapter_request=adapter_request,
        adapter_result=adapter_result,
        output_validation=passed_capability,
        canonical_output={"returns": [0.05]},
        warnings=(capability_warning,),
        executor=_runtime(),
        started_at=_NOW,
        finished_at=_NOW,
    )
    passed_method = CanonicalValidation(
        status=ValidationStatus.PASSED,
        schema_hash=method.contract_hash,
        validator=_runtime(),
    )
    run = RunRecord(
        status=RunStatus.SUCCEEDED,
        plan_record=plan_record,
        execution_request=execution_request,
        steps=(step,),
        method_input_validation=passed_method,
        method_output_validation=passed_method,
        canonical_method_output={"returns": [0.05]},
        method_warnings=(method_warning,),
        warnings=(capability_warning, method_warning),
        executor=_runtime(),
        started_at=_NOW,
        finished_at=_NOW,
    )

    assert step.ref.reference == f"dqstep:{step.step_hash}"
    assert run.ref.reference == f"dqrun:{run.run_hash}"
    assert plan.warnings == ()
    assert step.warnings == (capability_warning,)
    assert run.method_warnings == (method_warning,)
    assert run.warnings == (capability_warning, method_warning)
    assert RunRecord.model_validate(run.model_dump(mode="json")).run_hash == run.run_hash

    with pytest.raises(ValidationError, match="method-output warnings must be unique"):
        run.model_copy(
            update={"method_warnings": (method_warning, method_warning)}
        )

    with pytest.raises(ValidationError, match="de-duplicated"):
        run.model_copy(update={"warnings": ()})

    plan_with_duplicate_warning = plan.model_copy(
        update={"warnings": (method_warning,)}
    )
    plan_record_with_duplicate_warning = PlanRecord(
        plan=plan_with_duplicate_warning,
        compiler=plan_record.compiler,
        compiled_at=plan_record.compiled_at,
    )
    request_with_duplicate_warning = PlanExecutionRequest(
        plan=plan_with_duplicate_warning.ref,
        plan_record_hash=plan_record_with_duplicate_warning.record_hash,
        requester=execution_request.requester,
        requested_at=execution_request.requested_at,
    )
    adapter_request_with_duplicate_warning = adapter_request.model_copy(
        update={
            "plan": plan_with_duplicate_warning.ref,
            "execution_request_hash": request_with_duplicate_warning.request_hash,
        }
    )
    adapter_result_with_duplicate_warning = adapter_result.model_copy(
        update={"request_hash": adapter_request_with_duplicate_warning.request_hash}
    )
    step_with_duplicate_warning = step.model_copy(
        update={
            "plan": plan_with_duplicate_warning.ref,
            "execution_request_hash": request_with_duplicate_warning.request_hash,
            "compiled_step": plan_with_duplicate_warning.steps[0],
            "adapter_request": adapter_request_with_duplicate_warning,
            "adapter_result": adapter_result_with_duplicate_warning,
        }
    )
    run_with_duplicate_warning = run.model_copy(
        update={
            "plan_record": plan_record_with_duplicate_warning,
            "execution_request": request_with_duplicate_warning,
            "steps": (step_with_duplicate_warning,),
            "method_warnings": (method_warning,),
            "warnings": (capability_warning, method_warning),
        }
    )
    assert plan_with_duplicate_warning.warnings == (method_warning,)
    assert step_with_duplicate_warning.warnings == (capability_warning,)
    assert method_warning.source != capability_warning.source
    assert method_warning in run_with_duplicate_warning.method_warnings
    assert run_with_duplicate_warning.warnings == (capability_warning, method_warning)

    contradictory_warning = ValidationWarning(
        source=method_warning.source,
        code=method_warning.code,
        field=method_warning.field,
        message="The same warning identity cannot silently change its message.",
    )
    with pytest.raises(ValidationError, match="contradict one canonical warning identity"):
        run_with_duplicate_warning.model_copy(
            update={
                "method_warnings": (contradictory_warning,),
                "warnings": (capability_warning, contradictory_warning),
            }
        )

    input_not_validated = CanonicalValidation(
        status=ValidationStatus.NOT_PERFORMED,
        schema_hash=capability.contract_hash,
        validator=_runtime(),
    )
    with pytest.raises(ValidationError, match="requires passed canonical input validation"):
        step.model_copy(update={"input_validation": input_not_validated})

    failed_method_validation = CanonicalValidation(
        status=ValidationStatus.FAILED,
        schema_hash=method.contract_hash,
        validator=_runtime(),
        issues=(
            ValidationIssue(
                path="returns",
                code="schema_mismatch",
                message="The assembled method output did not match its canonical schema.",
            ),
        ),
    )
    failed_run = run.model_copy(
        update={
            "status": RunStatus.FAILED,
            "method_output_validation": failed_method_validation,
            "canonical_method_output": None,
            "failure": ExecutionFailure(
                code=ExecutionFailureCode.OUTPUT_VALIDATION_FAILED,
                message="Canonical method-output validation failed.",
                retryable=False,
            ),
        }
    )
    assert all(item.status == StepStatus.SUCCEEDED for item in failed_run.steps)
    assert failed_run.canonical_method_output is None
    assert failed_run.method_output_validation.status == ValidationStatus.FAILED

    with pytest.raises(ValidationError, match="failed canonical method-output validation"):
        failed_run.model_copy(
            update={
                "failure": ExecutionFailure(
                    code=ExecutionFailureCode.INTERNAL_FAILURE,
                    message="An unrelated failure cannot stand in for validation failure.",
                    retryable=False,
                )
            }
        )

    with pytest.raises(ValidationError, match="successful step"):
        step.model_copy(
            update={
                "adapter_result": None,
                "canonical_output": None,
            }
        )


def test_applied_default_requires_the_exact_resolved_value() -> None:
    _, _, _, _, _, plan, _ = _compiled_records()
    with pytest.raises(ValidationError, match="applied default"):
        plan.model_copy(
            update={
                "applied_defaults": (
                    AppliedDefault(field="prices", value=[99.0, 100.0]),
                )
            }
        )
