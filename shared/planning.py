"""Deterministic methods-first plan compilation and implementation resolution.

The compiler is deliberately pure: it reads already validated registry metadata, an explicit
service policy, one non-secret availability snapshot, and host-verified preference-origin
receipts.  It never imports adapter code, probes a provider, reads credentials, or executes a
recipe.  Runtime execution therefore receives one exact immutable implementation binding per
capability step and has no authority to resolve or fall back.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from defined_quant.constraint_evaluation import (
    ConstraintEvaluationError,
    evaluate_constraint,
)
from defined_quant.policy_evaluation import evaluate_policy_admission
from defined_quant.registry import ProtocolRegistry
from defined_quant.schema_validation import schema_matches, validate_instance
from defined_quant_protocol import (
    AdapterSpec,
    AppliedDefault,
    AvailabilitySnapshot,
    BackendBinding,
    BackendRole,
    BackendSpec,
    CandidateDecision,
    CompilationOutcome,
    CompiledPlan,
    CompiledStep,
    FallbackBehavior,
    ImplementationAvailability,
    ImplementationSpec,
    InstallationStatus,
    MethodSpec,
    NeedsInformation,
    OriginReceiptSet,
    PlanProposal,
    PlanRefusal,
    PreferenceDimension,
    PreferenceMode,
    PreferenceOrigin,
    RecipeStep,
    RegistryConstraintSeverity,
    RegistryTrustDimension,
    RequirementStatus,
    ResolutionConstraint,
    ResolutionConstraintSet,
    ResolutionDecision,
    ResolutionExplanationCode,
    ResolutionPolicy,
    ResolutionQuestion,
    ResolutionQuestionCode,
    ResolutionScope,
    RuntimeIdentity,
    RuntimeLocality,
    StepResolutionAttempt,
    ValidationWarning,
    WarningSource,
    WarningSourceKind,
)
from defined_quant_protocol import (
    CompilationRefusalCode as PlanRefusalCode,
)
from defined_quant_protocol import (
    ImplementationAvailabilityReason as AvailabilityReason,
)
from defined_quant_protocol import (
    ImplementationAvailabilityStatus as AvailabilityStatus,
)
from defined_quant_protocol import (
    ResolutionCandidateRejectionCode as CandidateRejectionCode,
)
from defined_quant_protocol.registry import derive_availability_requirements


@dataclass(frozen=True, slots=True)
class _RegistryIndexes:
    implementations: Mapping[str, ImplementationSpec]
    adapters: Mapping[str, AdapterSpec]
    backends: Mapping[str, BackendSpec]


@dataclass(frozen=True, slots=True)
class _CandidateContext:
    implementation: ImplementationSpec
    availability: ImplementationAvailability
    priority: int | None
    capability_match: bool
    policy_allowed: bool
    trust_satisfied: bool
    backend_kind_allowed: bool
    adapter_family_allowed: bool
    transport_allowed: bool
    locality_allowed: bool
    network_allowed: bool
    data_handling_allowed: bool
    applicability_satisfied: bool
    satisfied_trust_dimensions: tuple[RegistryTrustDimension, ...]
    missing_trust_dimensions: tuple[RegistryTrustDimension, ...]


def _refusal(
    proposal: PlanProposal,
    code: PlanRefusalCode,
    message: str,
    *,
    fields: Iterable[str] = (),
    attempts: Sequence[StepResolutionAttempt] = (),
) -> PlanRefusal:
    return PlanRefusal(
        proposal=proposal,
        code=code,
        message=message,
        fields=tuple(sorted(set(fields), key=lambda item: item.encode("utf-8"))),
        resolution_attempts=tuple(
            sorted(attempts, key=lambda item: item.step_id.encode("utf-8"))
        ),
    )


def _indexes(registry: ProtocolRegistry) -> _RegistryIndexes:
    return _RegistryIndexes(
        implementations={item.ref.spec_hash: item for item in registry.implementations},
        adapters={item.ref.spec_hash: item for item in registry.adapters},
        backends={item.ref.spec_hash: item for item in registry.backends},
    )


def relevant_policy(
    policy: ResolutionPolicy,
    capability_hashes: Iterable[str],
) -> ResolutionPolicy:
    """Return the exact policy slice that can affect one recipe.

    Binding this slice, rather than the host's unrelated rules, keeps a compiled plan stable when
    policy entries for unrelated capabilities are added.
    """

    selected = frozenset(capability_hashes)
    rules = tuple(
        item
        for item in policy.capability_rules
        if item.capability.contract_hash in selected
    )
    if not rules:
        raise ValueError("active policy has no rule for any recipe capability")
    return ResolutionPolicy(id=policy.id, version=policy.version, capability_rules=rules)


def relevant_availability(
    snapshot: AvailabilitySnapshot,
    implementation_hashes: Iterable[str],
) -> AvailabilitySnapshot:
    """Return the availability slice containing only implementations considered by a recipe."""

    selected = frozenset(implementation_hashes)
    return AvailabilitySnapshot(
        snapshot_id=snapshot.snapshot_id,
        observed_at=snapshot.observed_at,
        evaluator=snapshot.evaluator,
        implementations=tuple(
            item
            for item in snapshot.implementations
            if item.implementation.spec_hash in selected
        ),
    )


def _availability_slice(
    snapshot: AvailabilitySnapshot,
    implementations: Sequence[ImplementationSpec],
    backends: Sequence[BackendSpec],
) -> AvailabilitySnapshot:
    """Bind every considered implementation, making missing observations explicitly unknown."""

    values: list[ImplementationAvailability] = []
    for implementation in implementations:
        values.append(implementation_availability(snapshot, implementation, backends))
    return AvailabilitySnapshot(
        snapshot_id=snapshot.snapshot_id,
        observed_at=snapshot.observed_at,
        evaluator=snapshot.evaluator,
        implementations=tuple(
            sorted(
                tuple(values),
                key=lambda item: (
                    item.implementation.id,
                    item.implementation.version,
                    item.implementation.spec_hash,
                ),
            )
        ),
    )


def _unknown_availability(
    implementation: ImplementationSpec,
    backends: Sequence[BackendSpec],
) -> ImplementationAvailability:
    requirements = derive_availability_requirements(implementation, backends)

    def state(required: bool) -> RequirementStatus:
        return RequirementStatus.UNKNOWN if required else RequirementStatus.NOT_REQUIRED

    return ImplementationAvailability(
        implementation=implementation.ref,
        adapter=implementation.adapter,
        backend_bindings=implementation.backend_bindings,
        enabled=True,
        installation=InstallationStatus.UNKNOWN,
        artifact=RequirementStatus.UNKNOWN,
        dependencies=state(requirements.dependencies_required),
        credentials=state(requirements.credentials_required),
        licence=state(requirements.licence_required),
        entitlement=state(requirements.entitlement_required),
        transport=RequirementStatus.UNKNOWN,
        reachability=state(requirements.reachability_required),
        status=AvailabilityStatus.UNKNOWN,
        reasons=(AvailabilityReason.STATUS_UNKNOWN,),
    )


def _availability_by_implementation(
    snapshot: AvailabilitySnapshot,
) -> Mapping[str, ImplementationAvailability]:
    return {item.implementation.spec_hash: item for item in snapshot.implementations}


def implementation_availability(
    snapshot: AvailabilitySnapshot,
    implementation: ImplementationSpec,
    backends: Sequence[BackendSpec],
) -> ImplementationAvailability:
    """Return the exact observation, or an explicit unknown when none was supplied."""

    candidate = _availability_by_implementation(snapshot).get(implementation.ref.spec_hash)
    if candidate is None:
        return _unknown_availability(implementation, backends)
    if (
        candidate.implementation != implementation.ref
        or candidate.adapter != implementation.adapter
        or candidate.backend_bindings != implementation.backend_bindings
    ):
        raise ValueError(
            "availability observation contradicts the exact registered implementation"
        )
    return candidate


def _method(
    proposal: PlanProposal,
    registry: ProtocolRegistry,
) -> MethodSpec | PlanRefusal:
    matching_id = tuple(item for item in registry.methods if item.id == proposal.method_id)
    if not matching_id:
        return _refusal(
            proposal,
            PlanRefusalCode.METHOD_NOT_FOUND,
            "The requested method is not registered.",
        )
    exact = tuple(item for item in matching_id if item.version == proposal.method_version)
    if len(exact) != 1:
        return _refusal(
            proposal,
            PlanRefusalCode.METHOD_IDENTITY_MISMATCH,
            "The requested method version is not the registered canonical version.",
        )
    return exact[0]


def _input_questions(
    proposal: PlanProposal,
    method: MethodSpec,
) -> tuple[ResolutionQuestion, ...]:
    convention_by_field = {item.field: item for item in method.conventions}
    raw_properties = method.input_schema.get("properties")
    raw_required = method.input_schema.get("required")
    if not isinstance(raw_properties, Mapping) or not isinstance(raw_required, Sequence):
        raise ValueError("validated method input schema is not an object schema")
    properties = tuple(
        field for field in raw_properties if isinstance(field, str)
    )
    required = tuple(field for field in raw_required if isinstance(field, str))
    supplied = set(proposal.financial_inputs) | set(proposal.conventions)
    questions: list[ResolutionQuestion] = []
    for field in required:
        if field in supplied:
            continue
        convention = convention_by_field.get(field)
        if convention is None:
            questions.append(
                ResolutionQuestion(
                    question_id=f"missing_{field}",
                    code=ResolutionQuestionCode.REQUIRED_INPUT,
                    field_path=field,
                    description=f"Supply the required method input {field!r}.",
                )
            )
        else:
            questions.append(
                ResolutionQuestion(
                    question_id=f"missing_{field}",
                    code=ResolutionQuestionCode.REQUIRED_CONVENTION,
                    field_path=field,
                    description=convention.question,
                    allowed_values=convention.allowed_values,
                )
            )
    # MethodSpec guarantees that every optional field has an authored default. This guard keeps
    # corrupted or hand-constructed protocol objects from turning absence into an implicit choice.
    defaults = {item.field for item in method.defaults}
    for field in properties:
        if field not in supplied and field not in required and field not in defaults:
            questions.append(
                ResolutionQuestion(
                    question_id=f"missing_{field}",
                    code=ResolutionQuestionCode.REQUIRED_INPUT,
                    field_path=field,
                    description=f"Supply {field!r}; no authored default is available.",
                )
            )
    return tuple(
        sorted(questions, key=lambda item: (item.field_path, item.question_id))
    )


def _invalid_schema_fields(
    values: Mapping[str, Any],
    properties: Mapping[str, Any],
    root_schema: Mapping[str, Any],
) -> tuple[str, ...]:
    invalid: list[str] = []
    for field, value in values.items():
        field_schema = properties.get(field)
        if not isinstance(field_schema, Mapping) or not schema_matches(
            value,
            field_schema,
            root_schema,
        ):
            invalid.append(field)
    return tuple(sorted(invalid, key=lambda item: item.encode("utf-8")))


def _resolved_inputs(
    proposal: PlanProposal,
    method: MethodSpec,
) -> tuple[dict[str, Any], dict[str, Any], tuple[AppliedDefault, ...]] | PlanRefusal:
    convention_fields = {item.field for item in method.conventions}
    raw_properties = method.input_schema.get("properties")
    if not isinstance(raw_properties, Mapping):
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_REGISTRY,
            "The selected method does not have a valid object input schema.",
        )
    properties = {field for field in raw_properties if isinstance(field, str)}
    misplaced_conventions = set(proposal.financial_inputs) & convention_fields
    unknown_conventions = set(proposal.conventions) - convention_fields
    unknown_inputs = set(proposal.financial_inputs) - (properties - convention_fields)
    if misplaced_conventions or unknown_conventions:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_CONVENTION,
            "Conventions must use only their declared convention fields.",
            fields=misplaced_conventions | unknown_conventions,
        )
    if unknown_inputs:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_INPUT,
            "Financial inputs contain fields outside the selected method contract.",
            fields=unknown_inputs,
        )

    financial_inputs = dict(proposal.financial_inputs)
    conventions = dict(proposal.conventions)
    applied: list[AppliedDefault] = []
    for default in method.defaults:
        target = conventions if default.field in convention_fields else financial_inputs
        if default.field not in target:
            target[default.field] = default.value
            applied.append(AppliedDefault(field=default.field, value=default.value))
    try:
        validate_instance(
            financial_inputs | conventions,
            method.input_schema,
            name=f"method {method.id} input",
        )
    except ValueError as exc:
        invalid_conventions = _invalid_schema_fields(
            conventions,
            raw_properties,
            method.input_schema,
        )
        invalid_inputs = _invalid_schema_fields(
            financial_inputs,
            raw_properties,
            method.input_schema,
        )
        if invalid_conventions:
            return _refusal(
                proposal,
                PlanRefusalCode.INVALID_CONVENTION,
                str(exc),
                fields=invalid_conventions,
            )
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_INPUT,
            str(exc),
            fields=invalid_inputs,
        )
    return (
        financial_inputs,
        conventions,
        tuple(sorted(applied, key=lambda item: item.field.encode("utf-8"))),
    )


def _method_constraints(
    proposal: PlanProposal,
    method: MethodSpec,
    values: Mapping[str, Any],
) -> tuple[ValidationWarning, ...] | PlanRefusal:
    warnings: list[ValidationWarning] = []
    blocking: list[str] = []
    try:
        for constraint in method.constraints:
            if constraint.target.value != "input":
                continue
            if not evaluate_constraint(
                constraint.expression,
                values,
                subject_id=method.id,
            ):
                continue
            if constraint.severity == RegistryConstraintSeverity.BLOCKING:
                blocking.append(constraint.message)
            else:
                warnings.append(
                    ValidationWarning(
                        source=WarningSource(
                            kind=WarningSourceKind.METHOD,
                            subject_id=method.id,
                        ),
                        code=constraint.id,
                        message=constraint.message,
                    )
                )
    except ConstraintEvaluationError as exc:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_REGISTRY,
            f"Method constraint evaluation failed: {exc}",
        )
    if blocking:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_INPUT,
            "; ".join(blocking),
        )
    return tuple(
        sorted(
            warnings,
            key=lambda item: (item.code, item.field or "", item.message),
        )
    )


def _scope_applies(
    constraint: ResolutionConstraint,
    *,
    step_id: str,
    capability: Any,
) -> bool:
    scope = constraint.scope
    return bool(
        scope.all_steps
        or scope.step_id == step_id
        or scope.capability == capability
    )


def _validate_scopes(
    constraints: Sequence[ResolutionConstraint],
    method: MethodSpec,
) -> tuple[str, ...]:
    step_ids = {item.step_id for item in method.recipe.steps}
    capabilities = {item.capability for item in method.recipe.steps}
    invalid: list[str] = []
    for constraint in constraints:
        scope = constraint.scope
        if scope.step_id is not None and scope.step_id not in step_ids:
            invalid.append(constraint.constraint_id)
        if scope.capability is not None and scope.capability not in capabilities:
            invalid.append(constraint.constraint_id)
    return tuple(sorted(set(invalid), key=lambda item: item.encode("utf-8")))


def _with_verified_origins(
    proposal: PlanProposal,
    host_constraints: ResolutionConstraintSet,
    receipts: OriginReceiptSet | None,
    *,
    expected_session_binding: str | None,
    trusted_issuers: Sequence[RuntimeIdentity],
) -> ResolutionConstraintSet | NeedsInformation:
    receipt_boundary_valid = bool(
        receipts is not None
        and expected_session_binding is not None
        and receipts.session_binding_hash == expected_session_binding
        and trusted_issuers
        and all(item.issuer in trusted_issuers for item in receipts.receipts)
    )
    received = (
        {item.constraint_hash: item for item in receipts.receipts}
        if receipts is not None and receipt_boundary_valid
        else {}
    )
    verified: list[ResolutionConstraint] = []
    questions: list[ResolutionQuestion] = []
    for constraint in (
        *proposal.resolution_constraints.constraints,
        *host_constraints.constraints,
    ):
        if constraint.asserted_origin in {
            PreferenceOrigin.USER_EXPLICIT,
            PreferenceOrigin.USER_PROFILE,
        }:
            receipt = received.get(constraint.constraint_hash)
            if receipt is None or receipt.asserted_origin != constraint.asserted_origin:
                questions.append(
                    ResolutionQuestion(
                        question_id=f"confirm_{constraint.constraint_id}",
                        code=ResolutionQuestionCode.ORIGIN_CONFIRMATION,
                        field_path=f"resolution_constraints.{constraint.constraint_id}",
                        description=(
                            "Confirm this implementation preference through the trusted host "
                            "boundary before it can constrain resolution."
                        ),
                    )
                )
                continue
            verified.append(constraint.model_copy(update={"origin_receipt": receipt}))
        else:
            verified.append(constraint)
    if questions:
        return NeedsInformation(
            proposal=proposal,
            questions=tuple(
                sorted(questions, key=lambda item: (item.field_path, item.question_id))
            ),
        )
    try:
        return ResolutionConstraintSet(
            constraints=tuple(
                sorted(verified, key=lambda item: item.constraint_id.encode("utf-8"))
            )
        )
    except ValueError:
        # Duplicate IDs across caller and host constraints are not silently shadowed.
        return NeedsInformation(
            proposal=proposal,
            questions=(
                ResolutionQuestion(
                    question_id="resolution_constraint_conflict",
                    code=ResolutionQuestionCode.IMPLEMENTATION_SELECTION,
                    field_path="resolution_constraints",
                    description="Resolution constraints contain conflicting identities.",
                ),
            ),
        )


def _automatic_constraint(step_id: str) -> ResolutionConstraint:
    return ResolutionConstraint(
        constraint_id=f"automatic_{step_id}",
        scope=ResolutionScope(step_id=step_id),
        dimension=PreferenceDimension.IMPLEMENTATION,
        mode=PreferenceMode.AUTOMATIC,
        asserted_origin=PreferenceOrigin.AUTOMATIC_RESOLUTION,
        fallback=FallbackBehavior.ANY_POLICY_ELIGIBLE,
    )


def _binding_for_role(
    implementation: ImplementationSpec,
    role: BackendRole,
) -> BackendBinding | None:
    return next((item for item in implementation.backend_bindings if item.role == role), None)


def _target_matches(
    constraint: ResolutionConstraint,
    target: str,
    implementation: ImplementationSpec,
    indexes: _RegistryIndexes,
) -> bool:
    if constraint.dimension == PreferenceDimension.IMPLEMENTATION:
        identifier, separator, version = target.partition("@")
        return implementation.id == identifier and (
            not separator or implementation.version == version
        )
    if constraint.dimension == PreferenceDimension.ADAPTER_FAMILY:
        adapter = indexes.adapters[implementation.adapter.spec_hash]
        return adapter.family == target
    if constraint.dimension in {
        PreferenceDimension.BACKEND,
        PreferenceDimension.BACKEND_KIND,
        PreferenceDimension.TRANSPORT,
    }:
        assert constraint.backend_role is not None
        binding = _binding_for_role(implementation, constraint.backend_role)
        if binding is None:
            return False
        if constraint.dimension == PreferenceDimension.BACKEND:
            return binding.backend.id == target
        if constraint.dimension == PreferenceDimension.TRANSPORT:
            return binding.transport.value == target
        backend = indexes.backends[binding.backend.spec_hash]
        return backend.kind.value == target
    if constraint.dimension == PreferenceDimension.LOCALITY:
        if target == "remote_allowed":
            return True
        return all(
            item.locality == RuntimeLocality.LOCAL
            for item in implementation.backend_bindings
        )
    if constraint.dimension == PreferenceDimension.NETWORK:
        if target == "allowed":
            return True
        return all(
            not indexes.backends[item.backend.spec_hash].requires_network
            for item in implementation.backend_bindings
        )
    return False


def _constraint_satisfied(
    constraint: ResolutionConstraint,
    implementation: ImplementationSpec,
    indexes: _RegistryIndexes,
) -> bool:
    matches = tuple(
        _target_matches(constraint, target, implementation, indexes)
        for target in constraint.targets
    )
    if constraint.mode == PreferenceMode.AUTOMATIC:
        return True
    if constraint.mode == PreferenceMode.FORBIDDEN:
        return not any(matches)
    if constraint.mode in {PreferenceMode.REQUIRED, PreferenceMode.ALLOWED_SET}:
        return any(matches)
    if constraint.fallback == FallbackBehavior.FORBIDDEN:
        return bool(matches and matches[0])
    if constraint.fallback == FallbackBehavior.WITHIN_PREFERENCES:
        return any(matches)
    return True


def _preference_rank(
    constraints: Sequence[ResolutionConstraint],
    implementation: ImplementationSpec,
    indexes: _RegistryIndexes,
) -> int | None:
    preferred = tuple(item for item in constraints if item.mode == PreferenceMode.PREFERRED)
    if not preferred:
        return None
    rank = 0
    for constraint in preferred:
        matched = next(
            (
                index
                for index, target in enumerate(constraint.targets)
                if _target_matches(constraint, target, implementation, indexes)
            ),
            len(constraint.targets),
        )
        rank = rank * (len(constraint.targets) + 1) + matched
    return rank


def _policy_context(
    implementation: ImplementationSpec,
    policy: ResolutionPolicy,
    indexes: _RegistryIndexes,
    values: Mapping[str, Any],
    *,
    availability: ImplementationAvailability,
) -> _CandidateContext:
    policy_facts = evaluate_policy_admission(
        implementation,
        policy,
        backends=indexes.backends,
    )

    applicability_satisfied = True
    for restriction in implementation.restrictions:
        try:
            if evaluate_constraint(
                restriction.expression,
                values,
                subject_id=implementation.id,
            ):
                applicability_satisfied = False
        except ConstraintEvaluationError:
            applicability_satisfied = False

    return _CandidateContext(
        implementation=implementation,
        availability=availability,
        priority=policy_facts.priority,
        capability_match=True,
        policy_allowed=policy_facts.policy_allowed,
        trust_satisfied=policy_facts.trust_satisfied,
        backend_kind_allowed=policy_facts.backend_kind_allowed,
        adapter_family_allowed=True,
        transport_allowed=policy_facts.transport_allowed,
        locality_allowed=policy_facts.locality_allowed,
        network_allowed=policy_facts.network_allowed,
        data_handling_allowed=policy_facts.data_handling_allowed,
        applicability_satisfied=applicability_satisfied,
        satisfied_trust_dimensions=policy_facts.satisfied_trust_dimensions,
        missing_trust_dimensions=policy_facts.missing_trust_dimensions,
    )


def _candidate_decision(
    context: _CandidateContext,
    constraints: Sequence[ResolutionConstraint],
    indexes: _RegistryIndexes,
    *,
    selected: bool,
) -> CandidateDecision:
    implementation = context.implementation
    user_satisfied = all(
        _constraint_satisfied(item, implementation, indexes) for item in constraints
    )
    reasons: list[CandidateRejectionCode] = []
    facts = (
        (context.capability_match, CandidateRejectionCode.CAPABILITY_MISMATCH),
        (user_satisfied, CandidateRejectionCode.USER_CONSTRAINT),
        (context.policy_allowed, CandidateRejectionCode.POLICY),
        (context.trust_satisfied, CandidateRejectionCode.TRUST),
        (context.applicability_satisfied, CandidateRejectionCode.APPLICABILITY),
        (context.backend_kind_allowed, CandidateRejectionCode.BACKEND_KIND),
        (context.adapter_family_allowed, CandidateRejectionCode.ADAPTER_FAMILY),
        (context.transport_allowed, CandidateRejectionCode.TRANSPORT),
        (context.locality_allowed, CandidateRejectionCode.LOCALITY),
        (context.network_allowed, CandidateRejectionCode.NETWORK),
        (context.data_handling_allowed, CandidateRejectionCode.DATA_HANDLING),
    )
    reasons.extend(reason for passed, reason in facts if not passed)
    if context.availability.status != AvailabilityStatus.AVAILABLE:
        reasons.append(CandidateRejectionCode.UNAVAILABLE)
    return CandidateDecision(
        implementation=implementation.ref,
        adapter=implementation.adapter,
        backend_bindings=implementation.backend_bindings,
        priority=context.priority,
        preference_rank=_preference_rank(constraints, implementation, indexes),
        capability_match=context.capability_match,
        user_constraints_satisfied=user_satisfied,
        policy_allowed=context.policy_allowed,
        trust_satisfied=context.trust_satisfied,
        satisfied_trust_dimensions=context.satisfied_trust_dimensions,
        missing_trust_dimensions=context.missing_trust_dimensions,
        applicability_satisfied=context.applicability_satisfied,
        backend_kind_allowed=context.backend_kind_allowed,
        adapter_family_allowed=context.adapter_family_allowed,
        transport_allowed=context.transport_allowed,
        locality_allowed=context.locality_allowed,
        network_allowed=context.network_allowed,
        data_handling_allowed=context.data_handling_allowed,
        availability=context.availability,
        rejection_reasons=tuple(sorted(reasons, key=lambda item: item.value)),
        selected=selected,
    )


def _unselected_targets(
    constraints: Sequence[ResolutionConstraint],
    selected: ImplementationSpec,
    indexes: _RegistryIndexes,
) -> tuple[str, ...]:
    values: set[str] = set()
    for constraint in constraints:
        if constraint.mode != PreferenceMode.PREFERRED:
            continue
        matched_index = next(
            (
                index
                for index, target in enumerate(constraint.targets)
                if _target_matches(constraint, target, selected, indexes)
            ),
            len(constraint.targets),
        )
        values.update(constraint.targets[:matched_index])
    return tuple(sorted(values, key=lambda item: item.encode("utf-8")))


def _explanation(
    constraints: Sequence[ResolutionConstraint],
    *,
    fallback_used: bool,
) -> tuple[ResolutionExplanationCode, str]:
    modes = {item.mode for item in constraints}
    if PreferenceMode.REQUIRED in modes:
        return (
            ResolutionExplanationCode.REQUIRED_EXACT,
            "Selected the exact eligible implementation required by the verified constraint.",
        )
    if PreferenceMode.PREFERRED in modes:
        if fallback_used:
            return (
                ResolutionExplanationCode.PREFERRED_FALLBACK,
                "The preferred choice was not eligible; an explicitly permitted fallback "
                "was selected.",
            )
        return (
            ResolutionExplanationCode.PREFERRED_SELECTED,
            "Selected the highest-ranked eligible implementation in the verified preferences.",
        )
    if PreferenceMode.ALLOWED_SET in modes:
        return (
            ResolutionExplanationCode.ALLOWED_SET_SELECTED,
            "Selected the highest-priority eligible implementation within the allowed set.",
        )
    return (
        ResolutionExplanationCode.AUTOMATIC_POLICY_PRIORITY,
        "No user implementation preference applied; service policy priority selected the "
        "implementation.",
    )


def _resolve_step(
    proposal: PlanProposal,
    step: RecipeStep,
    constraints: Sequence[ResolutionConstraint],
    registry: ProtocolRegistry,
    indexes: _RegistryIndexes,
    policy: ResolutionPolicy,
    availability: AvailabilitySnapshot,
    values: Mapping[str, Any],
) -> CompiledStep | PlanRefusal | NeedsInformation:
    implementations = tuple(
        item for item in registry.implementations if item.capability == step.capability
    )
    if not implementations:
        return _refusal(
            proposal,
            PlanRefusalCode.NO_ELIGIBLE_IMPLEMENTATION,
            f"No implementation is registered for capability {step.capability.id}.",
        )
    for constraint in constraints:
        if constraint.mode != PreferenceMode.REQUIRED:
            continue
        if constraint.dimension == PreferenceDimension.IMPLEMENTATION:
            target = constraint.targets[0]
            _, separator, _ = target.partition("@")
            exact_matches = tuple(
                implementation
                for implementation in implementations
                if _target_matches(constraint, target, implementation, indexes)
            )
            if not separator and len(exact_matches) > 1:
                return NeedsInformation(
                    proposal=proposal,
                    questions=(
                        ResolutionQuestion(
                            question_id=f"exact_{constraint.constraint_id}",
                            code=ResolutionQuestionCode.IMPLEMENTATION_SELECTION,
                            field_path=(
                                f"resolution_constraints.{constraint.constraint_id}.targets"
                            ),
                            description=(
                                "The required implementation ID has multiple registered exact "
                                "versions. Confirm one ID@version target."
                            ),
                            allowed_values=tuple(
                                f"{item.id}@{item.version}"
                                for item in sorted(
                                    exact_matches,
                                    key=lambda item: (
                                        item.id,
                                        item.version,
                                        item.ref.spec_hash,
                                    ),
                                )
                            ),
                        ),
                    ),
                )
        if not any(
            _target_matches(
                constraint,
                constraint.targets[0],
                implementation,
                indexes,
            )
            for implementation in implementations
        ):
            return _refusal(
                proposal,
                PlanRefusalCode.REQUIRED_CHOICE_NOT_REGISTERED,
                (
                    "The required implementation or backend is not registered for "
                    f"recipe step {step.step_id}."
                ),
                fields=(constraint.constraint_id,),
            )
    observed = _availability_by_implementation(availability)
    contexts: list[_CandidateContext] = []
    for implementation in implementations:
        candidate_availability = observed.get(implementation.ref.spec_hash)
        if candidate_availability is None:
            candidate_availability = _unknown_availability(
                implementation,
                tuple(indexes.backends.values()),
            )
        contexts.append(
            _policy_context(
                implementation,
                policy,
                indexes,
                values,
                availability=candidate_availability,
            )
        )
    preliminary = tuple(
        _candidate_decision(item, constraints, indexes, selected=False)
        for item in contexts
    )
    eligible = tuple(item for item in preliminary if not item.rejection_reasons)
    attempt = StepResolutionAttempt(
        step_id=step.step_id,
        capability=step.capability,
        constraint_hashes=tuple(
            sorted(item.constraint_hash for item in constraints)
        ),
        candidates=tuple(
            sorted(
                preliminary,
                key=lambda item: (
                    item.implementation.id,
                    item.implementation.version,
                    item.implementation.spec_hash,
                ),
            )
        ),
        explanation="No candidate satisfied all user, policy, trust, and availability checks.",
    )
    if not eligible:
        preferred = tuple(item for item in constraints if item.mode == PreferenceMode.PREFERRED)
        if preferred and any(
            item.fallback == FallbackBehavior.FORBIDDEN for item in preferred
        ):
            return NeedsInformation(
                proposal=proposal,
                questions=(
                    ResolutionQuestion(
                        question_id=f"fallback_{step.step_id}",
                        code=ResolutionQuestionCode.FALLBACK_PERMISSION,
                        field_path=f"recipe.{step.step_id}",
                        description=(
                            "The preferred implementation is not eligible. Confirm an allowed "
                            "fallback or change the preference."
                        ),
                    ),
                ),
                resolution_attempts=(attempt,),
            )
        code = (
            PlanRefusalCode.REQUIRED_CHOICE_UNAVAILABLE
            if any(item.mode == PreferenceMode.REQUIRED for item in constraints)
            else PlanRefusalCode.NO_ELIGIBLE_IMPLEMENTATION
        )
        return _refusal(
            proposal,
            code,
            f"No eligible implementation can realize recipe step {step.step_id}.",
            attempts=(attempt,),
        )

    chosen = min(
        eligible,
        key=lambda item: (
            item.preference_rank if item.preference_rank is not None else 1_000_000_000,
            item.priority if item.priority is not None else 1_000_000_000,
            item.implementation.id,
            item.implementation.version,
            item.implementation.spec_hash,
        ),
    )
    chosen_spec = indexes.implementations[chosen.implementation.spec_hash]
    candidates = tuple(
        _candidate_decision(
            context,
            constraints,
            indexes,
            selected=context.implementation.ref == chosen.implementation,
        )
        for context in contexts
    )
    candidates = tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.implementation.id,
                item.implementation.version,
                item.implementation.spec_hash,
            ),
        )
    )
    unselected = _unselected_targets(constraints, chosen_spec, indexes)
    fallback_used = bool(unselected)
    fallback_permitted = any(
        item.mode == PreferenceMode.PREFERRED
        and item.fallback != FallbackBehavior.FORBIDDEN
        for item in constraints
    )
    explanation_code, explanation = _explanation(
        constraints,
        fallback_used=fallback_used,
    )
    runtime = _binding_for_role(chosen_spec, BackendRole.RUNTIME)
    assert runtime is not None
    decision = ResolutionDecision(
        step_id=step.step_id,
        capability=step.capability,
        constraint_hashes=tuple(
            sorted(item.constraint_hash for item in constraints)
        ),
        candidates=candidates,
        selected_implementation=chosen_spec.ref,
        selected_adapter=chosen_spec.adapter,
        selected_backend_bindings=chosen_spec.backend_bindings,
        selected_transport=runtime.transport,
        selected_locality=runtime.locality,
        fallback_permitted=fallback_permitted,
        fallback_used=fallback_used,
        requested_targets_not_selected=unselected,
        explanation_code=explanation_code,
        explanation=explanation,
    )
    return CompiledStep(
        step_id=step.step_id,
        capability=step.capability,
        implementation=chosen_spec.ref,
        adapter=chosen_spec.adapter,
        backend_bindings=chosen_spec.backend_bindings,
        transport=runtime.transport,
        locality=runtime.locality,
        depends_on=step.depends_on,
        input_bindings=step.input_bindings,
        resolution=decision,
    )


def compile_plan(
    proposal: PlanProposal,
    *,
    registry: ProtocolRegistry,
    policy: ResolutionPolicy,
    availability: AvailabilitySnapshot,
    origin_receipts: OriginReceiptSet | None = None,
    expected_origin_session_binding: str | None = None,
    trusted_origin_issuers: Sequence[RuntimeIdentity] = (),
    host_constraints: ResolutionConstraintSet = ResolutionConstraintSet(),
) -> CompilationOutcome:
    """Compile a backend-neutral method recipe to exact immutable implementations.

    Caller-supplied resolution constraints may assert only ``user_explicit``. They take effect
    only when the trusted host passes a matching origin receipt beside the proposal. Installed
    adapters are candidates only when the explicit policy admits their exact implementation and
    the supplied availability snapshot reports every required check as usable.
    """

    selected_method = _method(proposal, registry)
    if isinstance(selected_method, PlanRefusal):
        return selected_method
    questions = _input_questions(proposal, selected_method)
    if questions:
        return NeedsInformation(proposal=proposal, questions=questions)
    resolved = _resolved_inputs(proposal, selected_method)
    if isinstance(resolved, PlanRefusal):
        return resolved
    financial_inputs, conventions, applied_defaults = resolved
    values = financial_inputs | conventions
    warnings = _method_constraints(proposal, selected_method, values)
    if isinstance(warnings, PlanRefusal):
        return warnings

    verified = _with_verified_origins(
        proposal,
        host_constraints,
        origin_receipts,
        expected_session_binding=expected_origin_session_binding,
        trusted_issuers=trusted_origin_issuers,
    )
    if isinstance(verified, NeedsInformation):
        return verified
    invalid_scopes = _validate_scopes(verified.constraints, selected_method)
    if invalid_scopes:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_RESOLUTION_CONSTRAINT,
            "Resolution constraints reference recipe steps or capabilities outside the method.",
            fields=invalid_scopes,
        )

    automatic = tuple(
        _automatic_constraint(step.step_id) for step in selected_method.recipe.steps
    )
    try:
        effective = ResolutionConstraintSet(
            constraints=tuple(
                sorted(
                    (*verified.constraints, *automatic),
                    key=lambda item: item.constraint_id.encode("utf-8"),
                )
            )
        )
    except ValueError as exc:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_RESOLUTION_CONSTRAINT,
            f"Resolution constraints are not canonical: {exc}",
        )

    capability_hashes = {
        item.capability.contract_hash for item in selected_method.recipe.steps
    }
    candidate_implementations = tuple(
        item
        for item in registry.implementations
        if item.capability.contract_hash in capability_hashes
    )
    try:
        policy_slice = relevant_policy(policy, capability_hashes)
    except ValueError as exc:
        return _refusal(
            proposal,
            PlanRefusalCode.POLICY_REFUSED,
            str(exc),
        )
    try:
        availability_slice = _availability_slice(
            availability,
            candidate_implementations,
            registry.backends,
        )
    except ValueError as exc:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_REGISTRY,
            f"Availability snapshot is inconsistent: {exc}",
        )
    indexes = _indexes(registry)
    compiled_steps: list[CompiledStep] = []
    for step in selected_method.recipe.steps:
        applicable = tuple(
            item
            for item in effective.constraints
            if _scope_applies(item, step_id=step.step_id, capability=step.capability)
        )
        outcome = _resolve_step(
            proposal,
            step,
            applicable,
            registry,
            indexes,
            policy_slice,
            availability_slice,
            values,
        )
        if isinstance(outcome, PlanRefusal | NeedsInformation):
            return outcome
        compiled_steps.append(outcome)

    return CompiledPlan(
        method=selected_method.ref,
        proposal_hash=proposal.proposal_hash,
        policy=policy_slice.ref,
        availability_snapshot=availability_slice.ref,
        original_user_constraints=proposal.resolution_constraints,
        effective_constraints=effective,
        resolved_financial_inputs=financial_inputs,
        resolved_conventions=conventions,
        applied_defaults=applied_defaults,
        warnings=warnings,
        steps=tuple(compiled_steps),
        result_bindings=selected_method.recipe.result_bindings,
    )


__all__ = [
    "compile_plan",
    "implementation_availability",
    "relevant_availability",
    "relevant_policy",
]
