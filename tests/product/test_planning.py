from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from defined_quant.planning import compile_plan
from defined_quant.registry import ProtocolRegistry, load_registry
from defined_quant_protocol import (
    AvailabilitySnapshot,
    BackendKind,
    BackendRole,
    BackendRolePolicy,
    CapabilityPolicyRule,
    CompiledPlan,
    DataEgress,
    FallbackBehavior,
    ImplementationAvailability,
    ImplementationSpec,
    InstallationStatus,
    NeedsInformation,
    OriginReceiptSet,
    OriginVerification,
    PlanProposal,
    PlanRefusal,
    PolicyImplementation,
    PreferenceDimension,
    PreferenceMode,
    PreferenceOrigin,
    PreferenceOriginReceipt,
    RequirementStatus,
    ResolutionConstraint,
    ResolutionConstraintSet,
    ResolutionPolicy,
    ResolutionScope,
    RuntimeIdentity,
    RuntimeLocality,
    TransportKind,
)
from defined_quant_protocol import (
    ImplementationAvailabilityReason as AvailabilityReason,
)
from defined_quant_protocol import (
    ImplementationAvailabilityStatus as AvailabilityStatus,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 1, tzinfo=UTC)
HASH = "0" * 64
HOST = RuntimeIdentity(name="test_host", version="1.0.0", artifact_hash=HASH)


def _registry() -> ProtocolRegistry:
    return load_registry(root=ROOT / "registry").as_protocol_registry()


def _simple_capability(registry: ProtocolRegistry) -> Any:
    return next(item for item in registry.capabilities if item.id == "returns.simple")


def _simple_adapter(registry: ProtocolRegistry) -> Any:
    return next(item for item in registry.adapters if item.id == "dq_native.simple_return")


def _simple_implementation(registry: ProtocolRegistry) -> ImplementationSpec:
    return next(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )


def _simple_method(registry: ProtocolRegistry) -> Any:
    return next(item for item in registry.methods if item.id == "dq.market_data.simple_return")


def _policy(registry: ProtocolRegistry) -> ResolutionPolicy:
    capability = _simple_capability(registry)
    backend = registry.backends[0]
    implementation = _simple_implementation(registry)
    return ResolutionPolicy(
        id="test_policy",
        version="1.0.0",
        capability_rules=(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=(
                    PolicyImplementation(
                        implementation=implementation.ref,
                        priority=10,
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
                required_trust_dimensions=(),
            ),
        ),
    )


def _availability(
    registry: ProtocolRegistry,
    *,
    available: bool = True,
) -> AvailabilitySnapshot:
    implementation = _simple_implementation(registry)
    return AvailabilitySnapshot(
        snapshot_id="test_snapshot",
        observed_at=NOW,
        evaluator=RuntimeIdentity(name="test_host", version="1.0.0", artifact_hash=HASH),
        implementations=(
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
            ),
        ),
    )


def _proposal(
    constraints: ResolutionConstraintSet = ResolutionConstraintSet(),
) -> PlanProposal:
    return PlanProposal(
        method_id="dq.market_data.simple_return",
        method_version="1.0.0",
        financial_inputs={"prices": [100.0, 110.0, 99.0]},
        conventions={"price_kind": "adjusted"},
        resolution_constraints=constraints,
    )


def _required_backend(registry: ProtocolRegistry) -> ResolutionConstraint:
    capability = _simple_capability(registry)
    return ResolutionConstraint(
        constraint_id="required_runtime_backend",
        scope=ResolutionScope(capability=capability.ref),
        dimension=PreferenceDimension.BACKEND,
        backend_role=BackendRole.RUNTIME,
        mode=PreferenceMode.REQUIRED,
        targets=("dq_native",),
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=FallbackBehavior.FORBIDDEN,
    )


def _receipts(constraint: ResolutionConstraint) -> OriginReceiptSet:
    receipt = PreferenceOriginReceipt(
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        verified_origin=PreferenceOrigin.USER_EXPLICIT,
        verification=OriginVerification.USER_CONFIRMED,
        constraint_hash=constraint.constraint_hash,
        session_binding_hash=HASH,
        issuer=HOST,
        issued_at=NOW,
    )
    return OriginReceiptSet(session_binding_hash=HASH, receipts=(receipt,))


def _registry_with_alternative() -> ProtocolRegistry:
    registry = _registry()
    backend = registry.backends[0].model_copy(
        update={"id": "fixture_backend", "title": "Fixture local backend"}
    )
    adapter = _simple_adapter(registry).model_copy(
        update={
            "id": "fixture.simple_return",
            "title": "Fixture simple-return adapter",
            "backend": backend.ref,
            "dispatch_key": "fixture_simple_return",
            "evidence_refs": (),
        }
    )
    original = _simple_implementation(registry)
    implementation = original.model_copy(
        update={
            "id": "fixture.simple_return",
            "title": "Fixture simple-return implementation",
            "backend_bindings": (
                original.backend_bindings[0].model_copy(update={"backend": backend.ref}),
            ),
            "adapter": adapter.ref,
            "trust": (),
            "evidence_refs": (),
        }
    )
    return ProtocolRegistry(
        methods=registry.methods,
        capabilities=registry.capabilities,
        backends=tuple(sorted((*registry.backends, backend), key=lambda item: item.id)),
        adapters=tuple(sorted((*registry.adapters, adapter), key=lambda item: item.id)),
        implementations=tuple(
            sorted((*registry.implementations, implementation), key=lambda item: item.id)
        ),
    )


def _registry_with_second_implementation_version() -> ProtocolRegistry:
    registry = _registry()
    original = _simple_implementation(registry)
    second = original.model_copy(
        update={
            "version": "2.0.0",
            "evidence_refs": (),
            "trust": (),
        }
    )
    return ProtocolRegistry(
        methods=registry.methods,
        capabilities=registry.capabilities,
        backends=registry.backends,
        adapters=registry.adapters,
        implementations=tuple(
            sorted(
                (*registry.implementations, second),
                key=lambda item: (item.id, item.version, item.ref.spec_hash),
            )
        ),
    )


def _policy_for_all(registry: ProtocolRegistry) -> ResolutionPolicy:
    capability = _simple_capability(registry)
    backends = tuple(sorted((item.ref for item in registry.backends), key=lambda item: item.id))
    implementations = tuple(
        sorted(
            (
                PolicyImplementation(
                    implementation=item.ref,
                    priority=(10 if item.id == "dq_native.simple_return" else 20),
                )
                for item in registry.implementations
                if item.capability == capability.ref
            ),
            key=lambda item: (
                item.implementation.id,
                item.implementation.version,
                item.implementation.spec_hash,
            ),
        )
    )
    return ResolutionPolicy(
        id="test_policy",
        version="1.0.0",
        capability_rules=(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=implementations,
                backend_roles=(
                    BackendRolePolicy(
                        role=BackendRole.RUNTIME,
                        allowed_backends=backends,
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


def _availability_for_all(
    registry: ProtocolRegistry,
    *,
    unavailable: frozenset[str] = frozenset(),
) -> AvailabilitySnapshot:
    def observation(implementation: ImplementationSpec) -> ImplementationAvailability:
        available = implementation.id not in unavailable
        return ImplementationAvailability(
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

    return AvailabilitySnapshot(
        snapshot_id="test_snapshot",
        observed_at=NOW,
        evaluator=RuntimeIdentity(name="test_host", version="1.0.0", artifact_hash=HASH),
        implementations=tuple(observation(item) for item in registry.implementations),
    )


def test_automatic_resolution_compiles_exact_simple_return_implementation() -> None:
    registry = _registry()
    outcome = compile_plan(
        _proposal(),
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry),
    )

    assert isinstance(outcome, CompiledPlan)
    assert outcome.method == _simple_method(registry).ref
    assert outcome.resolved_financial_inputs == {
        "declared_frequency": None,
        "prices": [100.0, 110.0, 99.0],
        "timestamps": None,
    }
    assert outcome.resolved_conventions == {"price_kind": "adjusted"}
    assert [item.field for item in outcome.applied_defaults] == [
        "declared_frequency",
        "timestamps",
    ]
    assert [item.code for item in outcome.warnings] == ["ordering_unverified"]
    assert outcome.steps[0].implementation == _simple_implementation(registry).ref
    assert outcome.steps[0].resolution.explanation_code.value == "automatic_policy_priority"
    assert outcome.runtime_fallback_allowed is False


def test_user_constraint_requires_a_host_verified_origin_receipt() -> None:
    registry = _registry()
    constraint = _required_backend(registry)
    proposal = _proposal(ResolutionConstraintSet(constraints=(constraint,)))

    unverified = compile_plan(
        proposal,
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry),
    )
    verified = compile_plan(
        proposal,
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry),
        origin_receipts=_receipts(constraint),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )
    replayed_into_another_session = compile_plan(
        proposal,
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry),
        origin_receipts=_receipts(constraint),
        expected_origin_session_binding="9" * 64,
        trusted_origin_issuers=(HOST,),
    )
    untrusted_issuer = compile_plan(
        proposal,
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry),
        origin_receipts=_receipts(constraint),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(
            RuntimeIdentity(name="other_host", version="1.0.0", artifact_hash=HASH),
        ),
    )

    assert isinstance(unverified, NeedsInformation)
    assert unverified.questions[0].code.value == "origin_confirmation"
    assert isinstance(verified, CompiledPlan)
    assert isinstance(replayed_into_another_session, NeedsInformation)
    assert isinstance(untrusted_issuer, NeedsInformation)
    effective = {
        item.constraint_id: item for item in verified.effective_constraints.constraints
    }
    assert effective[constraint.constraint_id].origin_verified is True
    assert verified.original_user_constraints == proposal.resolution_constraints


def test_required_unavailable_backend_refuses_without_substitution() -> None:
    registry = _registry()
    constraint = _required_backend(registry)
    proposal = _proposal(ResolutionConstraintSet(constraints=(constraint,)))

    outcome = compile_plan(
        proposal,
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry, available=False),
        origin_receipts=_receipts(constraint),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )

    assert isinstance(outcome, PlanRefusal)
    assert outcome.code.value == "required_choice_unavailable"
    assert outcome.resolution_attempts[0].candidates[0].selected is False
    assert outcome.resolution_attempts[0].candidates[0].rejection_reasons == (
        "unavailable",
    )


def test_required_unregistered_backend_is_refused_as_unregistered() -> None:
    registry = _registry()
    constraint = _required_backend(registry).model_copy(
        update={"targets": ("not_registered",)}
    )
    proposal = _proposal(ResolutionConstraintSet(constraints=(constraint,)))

    outcome = compile_plan(
        proposal,
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry),
        origin_receipts=_receipts(constraint),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )

    assert isinstance(outcome, PlanRefusal)
    assert outcome.code.value == "required_choice_not_registered"


def test_required_bare_implementation_id_asks_for_an_exact_version() -> None:
    registry = _registry_with_second_implementation_version()
    constraint = ResolutionConstraint(
        constraint_id="required_calculation",
        scope=ResolutionScope(capability=_simple_capability(registry).ref),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.REQUIRED,
        targets=("dq_native.simple_return",),
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=FallbackBehavior.FORBIDDEN,
    )

    outcome = compile_plan(
        _proposal(ResolutionConstraintSet(constraints=(constraint,))),
        registry=registry,
        policy=_policy_for_all(registry),
        availability=_availability_for_all(registry),
        origin_receipts=_receipts(constraint),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )

    assert isinstance(outcome, NeedsInformation)
    assert outcome.questions[0].code.value == "implementation_selection"
    assert outcome.questions[0].allowed_values == (
        "dq_native.simple_return@1.0.0",
        "dq_native.simple_return@2.0.0",
    )


def test_preferred_implementation_falls_back_only_when_explicitly_permitted() -> None:
    registry = _registry_with_alternative()
    capability = _simple_capability(registry)
    preference = ResolutionConstraint(
        constraint_id="preferred_calculation",
        scope=ResolutionScope(capability=capability.ref),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.PREFERRED,
        targets=("dq_native.simple_return", "fixture.simple_return"),
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=FallbackBehavior.WITHIN_PREFERENCES,
    )
    proposal = _proposal(ResolutionConstraintSet(constraints=(preference,)))

    outcome = compile_plan(
        proposal,
        registry=registry,
        policy=_policy_for_all(registry),
        availability=_availability_for_all(
            registry,
            unavailable=frozenset({"dq_native.simple_return"}),
        ),
        origin_receipts=_receipts(preference),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )

    assert isinstance(outcome, CompiledPlan)
    assert outcome.steps[0].implementation.id == "fixture.simple_return"
    assert outcome.steps[0].resolution.fallback_permitted is True
    assert outcome.steps[0].resolution.fallback_used is True
    assert outcome.steps[0].resolution.requested_targets_not_selected == (
        "dq_native.simple_return",
    )
    assert outcome.steps[0].resolution.explanation_code.value == "preferred_fallback"


@pytest.mark.parametrize(
    ("fallback", "targets"),
    (
        (
            FallbackBehavior.WITHIN_PREFERENCES,
            ("dq_native.simple_return@1.0.0", "fixture.simple_return@1.0.0"),
        ),
        (
            FallbackBehavior.ANY_POLICY_ELIGIBLE,
            ("dq_native.simple_return@1.0.0",),
        ),
    ),
)
def test_versioned_preference_fallback_records_the_unselected_exact_target(
    fallback: FallbackBehavior,
    targets: tuple[str, ...],
) -> None:
    registry = _registry_with_alternative()
    capability = _simple_capability(registry)
    preference = ResolutionConstraint(
        constraint_id="preferred_calculation",
        scope=ResolutionScope(capability=capability.ref),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.PREFERRED,
        targets=targets,
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=fallback,
    )

    outcome = compile_plan(
        _proposal(ResolutionConstraintSet(constraints=(preference,))),
        registry=registry,
        policy=_policy_for_all(registry),
        availability=_availability_for_all(
            registry,
            unavailable=frozenset({"dq_native.simple_return"}),
        ),
        origin_receipts=_receipts(preference),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )

    assert isinstance(outcome, CompiledPlan)
    assert outcome.steps[0].implementation.id == "fixture.simple_return"
    assert outcome.steps[0].resolution.fallback_permitted is True
    assert outcome.steps[0].resolution.fallback_used is True
    assert outcome.steps[0].resolution.requested_targets_not_selected == (
        "dq_native.simple_return@1.0.0",
    )
    assert outcome.steps[0].resolution.explanation_code.value == "preferred_fallback"


def test_preferred_implementation_without_fallback_stops_for_confirmation() -> None:
    registry = _registry_with_alternative()
    preference = ResolutionConstraint(
        constraint_id="preferred_calculation",
        scope=ResolutionScope(capability=_simple_capability(registry).ref),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.PREFERRED,
        targets=("dq_native.simple_return",),
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=FallbackBehavior.FORBIDDEN,
    )
    proposal = _proposal(ResolutionConstraintSet(constraints=(preference,)))

    outcome = compile_plan(
        proposal,
        registry=registry,
        policy=_policy_for_all(registry),
        availability=_availability_for_all(
            registry,
            unavailable=frozenset({"dq_native.simple_return"}),
        ),
        origin_receipts=_receipts(preference),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )

    assert isinstance(outcome, NeedsInformation)
    assert outcome.questions[0].code.value == "fallback_permission"
    assert all(
        not candidate.selected
        for candidate in outcome.resolution_attempts[0].candidates
    )


def test_forbidden_implementation_is_removed_before_policy_priority() -> None:
    registry = _registry_with_alternative()
    forbidden = ResolutionConstraint(
        constraint_id="forbid_native_calculation",
        scope=ResolutionScope(capability=_simple_capability(registry).ref),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.FORBIDDEN,
        targets=("dq_native.simple_return",),
        asserted_origin=PreferenceOrigin.USER_EXPLICIT,
        fallback=FallbackBehavior.FORBIDDEN,
    )
    proposal = _proposal(ResolutionConstraintSet(constraints=(forbidden,)))

    outcome = compile_plan(
        proposal,
        registry=registry,
        policy=_policy_for_all(registry),
        availability=_availability_for_all(registry),
        origin_receipts=_receipts(forbidden),
        expected_origin_session_binding=HASH,
        trusted_origin_issuers=(HOST,),
    )

    assert isinstance(outcome, CompiledPlan)
    assert outcome.steps[0].implementation.id == "fixture.simple_return"
    candidates = {
        item.implementation.id: item for item in outcome.steps[0].resolution.candidates
    }
    assert candidates["dq_native.simple_return"].rejection_reasons == (
        "user_constraint",
    )
    assert candidates["fixture.simple_return"].selected is True


def test_missing_financial_convention_requests_information() -> None:
    registry = _registry()
    proposal = PlanProposal(
        method_id="dq.market_data.simple_return",
        method_version="1.0.0",
        financial_inputs={"prices": [100.0, 101.0]},
        conventions={},
    )

    outcome = compile_plan(
        proposal,
        registry=registry,
        policy=_policy(registry),
        availability=_availability(registry),
    )

    assert isinstance(outcome, NeedsInformation)
    assert outcome.questions[0].field_path == "price_kind"
    assert outcome.questions[0].code.value == "required_convention"


def test_unrelated_policy_rule_does_not_change_compiled_plan_identity() -> None:
    registry = _registry()
    base_policy = _policy(registry)
    unrelated_capability = _simple_capability(registry).model_copy(
        update={"id": "statistics.fixture"}
    )
    unrelated_rule = base_policy.capability_rules[0].model_copy(
        update={"capability": unrelated_capability.ref}
    )
    expanded_policy = ResolutionPolicy(
        id=base_policy.id,
        version=base_policy.version,
        capability_rules=tuple(
            sorted(
                (*base_policy.capability_rules, unrelated_rule),
                key=lambda item: (
                    item.capability.id,
                    item.capability.version,
                    item.capability.contract_hash,
                ),
            )
        ),
    )

    first = compile_plan(
        _proposal(),
        registry=registry,
        policy=base_policy,
        availability=_availability(registry),
    )
    second = compile_plan(
        _proposal(),
        registry=registry,
        policy=expanded_policy,
        availability=_availability(registry),
    )

    assert isinstance(first, CompiledPlan)
    assert isinstance(second, CompiledPlan)
    assert first.plan_hash == second.plan_hash
    assert first.policy == second.policy
