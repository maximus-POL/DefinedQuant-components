"""Acceptance tests for the pure governed-plan compiler."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from defined_quant import subject_hash
from defined_quant.method_registry import GovernedRegistry, project_component_inspection
from defined_quant.plan_compiler import compile_plan
from defined_quant.service import DefinedQuantService
from defined_quant_protocol import (
    AvailabilityReason,
    AvailabilitySnapshotV1,
    AvailabilityStatus,
    CapabilityKind,
    CapabilityResolutionRuleV1,
    CapabilitySpecV1,
    CompiledPlanV1,
    ConstraintComparisonV1,
    ConstraintConditionV1,
    ConstraintMeasure,
    ConstraintOperandV1,
    ConstraintOperator,
    ConstraintSeverity,
    ConstraintSpecV1,
    ConventionSpecV1,
    DefaultSpecV1,
    ImplementationAvailabilityV1,
    ImplementationSpecV1,
    ImplementationTransport,
    MethodSpecV1,
    NamedPortV1,
    NeedsInformationV1,
    PlanProposalV1,
    PlanRefusalV1,
    PolicyImplementationV1,
    RecipeDagV1,
    RecipeInputBindingV1,
    RecipeStepV1,
    RecipeValueSourceV1,
    ResolutionPolicyV1,
    SemanticPort,
    TransportLocality,
    TransportMetadataV1,
    TrustDimension,
    TrustedAdapterV1,
    canonical_hash,
    canonical_json_bytes,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SIMPLE_RETURN_SUBJECT = "ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a"


def _port(*, direction: str, concept: str) -> SemanticPort:
    return SemanticPort.model_validate(
        {
            "direction": direction,
            "concept": concept,
            "unit": "decimal" if "return" in concept else "price",
            "shape": "ordered_series",
            "cardinality": "one_or_more",
            "convention": (
                "simple_periodic_return" if "return" in concept else "ordered_positive_prices"
            ),
            "ordering": "preserve_source_order",
            "frequency": "inherited",
            "provenance_requirement": "not_required",
        }
    )


_PRICE_INPUT_PORT = _port(direction="input", concept="price_series")
_RETURN_OUTPUT_PORT = _port(direction="output", concept="periodic_return_series")

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["adjusted", "unadjusted"]},
        "prices": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 2,
            "x-defined-quant-port": _PRICE_INPUT_PORT.model_dump(mode="json"),
        },
        "window": {"type": "integer", "minimum": 1, "default": 5},
    },
    "required": ["kind", "prices"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "returns": {
            "type": "array",
            "items": {"type": "number"},
            "x-defined-quant-port": _RETURN_OUTPUT_PORT.model_dump(mode="json"),
        },
    },
    "required": ["returns"],
    "additionalProperties": False,
}
INPUT_PORTS = (NamedPortV1(field="prices", port=_PRICE_INPUT_PORT),)
OUTPUT_PORTS = (
    NamedPortV1(
        field="returns",
        port=_RETURN_OUTPUT_PORT,
    ),
)
TRUST = (
    TrustDimension.DETERMINISTIC,
    TrustDimension.SCHEMA_CHECKED,
    TrustDimension.TRUSTED_ADAPTER,
)


def _capability(
    capability_id: str = "statistics.simple_return",
    *,
    version: str = "1.0.0",
) -> CapabilitySpecV1:
    return CapabilitySpecV1(
        id=capability_id,
        version=version,
        title="Synthetic return capability",
        kind=CapabilityKind.CALCULATION,
        summary="Transforms an ordered price series into periodic returns.",
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        input_ports=INPUT_PORTS,
        output_ports=OUTPUT_PORTS,
    )


def _method(capability: CapabilitySpecV1, *, default_window: int = 5) -> MethodSpecV1:
    return MethodSpecV1(
        id="dq.market_data.synthetic_return",
        version="1.0.0",
        title="Synthetic Return",
        summary="A deterministic synthetic method used only for compiler tests.",
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        input_ports=INPUT_PORTS,
        output_ports=OUTPUT_PORTS,
        methodology=("r_t = P_t / P_t-1 - 1",),
        interpretation="Interpret results as synthetic decimal periodic returns.",
        conventions=(
            ConventionSpecV1(
                id="kind",
                field="kind",
                question="Are the prices adjusted or unadjusted?",
                materiality="The choice changes the economic interpretation.",
                required_from_user=True,
                allowed_values=("adjusted", "unadjusted"),
            ),
        ),
        defaults=(
            DefaultSpecV1(
                field="window",
                value=default_window,
                rationale="The synthetic fixture explicitly authors this default.",
            ),
        ),
        capabilities=(capability.ref,),
        recipe=RecipeDagV1(
            steps=(
                RecipeStepV1(
                    step_id="calculate",
                    capability_id=capability.id,
                    input_bindings=tuple(
                        RecipeInputBindingV1(
                            target_field=field,
                            source=RecipeValueSourceV1(
                                source="method_input",
                                field=field,
                            ),
                        )
                        for field in ("kind", "prices", "window")
                    ),
                    output_fields=("returns",),
                ),
            ),
            result_steps=("calculate",),
        ),
    )


def _implementation(
    capability: CapabilitySpecV1,
    implementation_id: str,
    *,
    version: str = "1.0.0",
) -> ImplementationSpecV1:
    return ImplementationSpecV1(
        id=implementation_id,
        version=version,
        title=f"Synthetic {implementation_id}",
        capability=capability.ref,
        input_ports=capability.input_ports,
        output_ports=capability.output_ports,
        adapter=TrustedAdapterV1(
            id="synthetic_adapter",
            version="1.0.0",
            distribution="defined-quant",
        ),
        transport=TransportMetadataV1(
            kind=ImplementationTransport.PYTHON_PACKAGE,
            locality=TransportLocality.LOCAL,
            system_id="synthetic_runtime",
            requires_network=False,
            requires_credentials=False,
        ),
        trust_dimensions=TRUST,
    )


def _policy(
    method: MethodSpecV1,
    capability: CapabilitySpecV1,
    ranked: tuple[tuple[ImplementationSpecV1, int], ...],
    *,
    version: str = "1.0.0",
) -> ResolutionPolicyV1:
    return ResolutionPolicyV1(
        id="professional_default",
        version=version,
        method=method.ref,
        capability_rules=(
            CapabilityResolutionRuleV1(
                capability=capability.ref,
                implementations=tuple(
                    PolicyImplementationV1(
                        implementation=implementation.ref,
                        priority=priority,
                    )
                    for implementation, priority in sorted(
                        ranked,
                        key=lambda item: (
                            item[1],
                            item[0].id.encode("utf-8"),
                            item[0].version,
                            item[0].implementation_hash,
                        ),
                    )
                ),
                required_trust_dimensions=TRUST,
                allowed_transports=(ImplementationTransport.PYTHON_PACKAGE,),
            ),
        ),
    )


def _availability(
    implementations: tuple[ImplementationSpecV1, ...],
    *,
    unavailable: frozenset[str] = frozenset(),
) -> AvailabilitySnapshotV1:
    return AvailabilitySnapshotV1(
        implementations=tuple(
            ImplementationAvailabilityV1(
                implementation=item.ref,
                status=(
                    AvailabilityStatus.UNAVAILABLE
                    if item.id in unavailable
                    else AvailabilityStatus.AVAILABLE
                ),
                reason=(
                    AvailabilityReason.DISABLED
                    if item.id in unavailable
                    else AvailabilityReason.READY
                ),
            )
            for item in sorted(implementations, key=lambda value: value.id)
        )
    )


def _context() -> dict[str, Any]:
    capability = _capability()
    method = _method(capability)
    alpha = _implementation(capability, "impl.alpha")
    beta = _implementation(capability, "impl.beta")
    implementations = (alpha, beta)
    return {
        "proposal": PlanProposalV1(
            method_id=method.id,
            method_version=method.version,
            financial_inputs={"prices": [100.0, 101.0]},
            conventions={"kind": "adjusted"},
            agent_rationale="The user requested periodic returns.",
        ),
        "method": method,
        "capabilities": (capability,),
        "implementations": implementations,
        "policy": _policy(method, capability, ((alpha, 10), (beta, 20))),
        "availability": _availability(implementations),
    }


def _compile(context: dict[str, Any]) -> CompiledPlanV1:
    outcome = compile_plan(**context)
    assert isinstance(outcome, CompiledPlanV1)
    return outcome


def test_identical_inputs_produce_identical_canonical_plan_bytes_and_hash() -> None:
    context = _context()
    first = _compile(context)
    second = _compile(context)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first.plan_hash == second.plan_hash


def test_hash_bound_specs_and_plans_are_recursively_immutable() -> None:
    context = _context()
    method = context["method"]
    plan = _compile(context)
    method_hash = method.spec_hash
    plan_hash = plan.plan_hash

    with pytest.raises(TypeError, match="immutable"):
        method.input_schema["properties"]["prices"]["minItems"] = 1
    with pytest.raises(TypeError, match="immutable"):
        plan.resolved_financial_inputs["prices"][0] = 0.0

    assert method.spec_hash == method_hash
    assert plan.plan_hash == plan_hash


def test_revalidating_copy_preserves_unset_literal_fields_in_nested_constraints() -> None:
    operand = ConstraintOperandV1(field="prices", measure=ConstraintMeasure.COUNT)
    assert operand.model_copy() == operand

    service = DefinedQuantService()
    try:
        registry = project_component_inspection(
            service.inspect_component("dq.market_data.simple_return", view="governance")
        )
    finally:
        service.close()
    method = registry.methods[0]
    copied = method.model_copy()
    assert copied == method
    assert copied.spec_hash == method.spec_hash


def test_governed_plan_hash_vectors_are_fixed() -> None:
    fixture = json.loads(
        (ROOT / "docs/local_mcp/governed_plan_hash_vectors.v1.json").read_text(encoding="utf-8")
    )
    context = _context()
    plan = _compile(context)
    capability = context["capabilities"][0]
    vectors = {item["id"]: item for item in fixture["vectors"]}
    expected_values = {
        "method_spec": context["method"].model_dump(mode="json"),
        "capability_spec": capability.model_dump(mode="json"),
        "implementation_impl_alpha": context["implementations"][0].model_dump(mode="json"),
        "implementation_impl_beta": context["implementations"][1].model_dump(mode="json"),
        "resolution_policy": context["policy"].model_dump(mode="json"),
        "compiled_plan_semantics": plan.semantic_projection(),
    }
    assert set(vectors) == set(expected_values)
    for vector_id, value in expected_values.items():
        vector = vectors[vector_id]
        assert vector["value"] == value
        assert canonical_hash(value, domain=vector["domain"]) == vector["expected_sha256"]
    assert fixture["compilation_inputs"] == {
        "proposal": context["proposal"].model_dump(mode="json"),
        "availability": context["availability"].model_dump(mode="json"),
    }
    assert fixture["compiled_plan_value"] == plan.model_dump(mode="json")
    assert (
        hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
        == fixture["expected_compiled_plan_canonical_bytes_sha256"]
    )


def test_registry_insertion_order_and_unrelated_additions_do_not_change_plan() -> None:
    context = _context()
    baseline = _compile(context)

    reversed_context = {**context, "implementations": tuple(reversed(context["implementations"]))}
    assert _compile(reversed_context).plan_hash == baseline.plan_hash

    unrelated_capability = _capability("statistics.unrelated")
    unrelated = _implementation(unrelated_capability, "impl.unrelated")
    expanded = {
        **context,
        "capabilities": (*context["capabilities"], unrelated_capability),
        "implementations": (*context["implementations"], unrelated),
    }
    assert _compile(expanded).plan_hash == baseline.plan_hash

    altered_capability = context["capabilities"][0].model_copy(
        update={"summary": "A stale capability identity that must not be considered."}
    )
    stale = _implementation(altered_capability, "impl.stale")
    stale_context = {
        **context,
        "implementations": (*context["implementations"], stale),
        "availability": _availability((*context["implementations"], stale)),
    }
    assert _compile(stale_context).plan_hash == baseline.plan_hash


def test_selected_implementation_policy_input_and_convention_change_plan_hash() -> None:
    context = _context()
    baseline = _compile(context)
    alpha, beta = context["implementations"]

    changed_availability = {
        **context,
        "availability": _availability((alpha, beta), unavailable=frozenset({alpha.id})),
    }
    selected_beta = _compile(changed_availability)
    assert selected_beta.plan_hash != baseline.plan_hash
    assert selected_beta.resolution_receipts[0].selected_implementation == beta.ref

    changed_policy = {
        **context,
        "policy": _policy(context["method"], context["capabilities"][0], ((alpha, 11), (beta, 20))),
    }
    policy_plan = _compile(changed_policy)
    assert policy_plan.policy.policy_hash != baseline.policy.policy_hash
    assert policy_plan.plan_hash != baseline.plan_hash

    changed_input = {
        **context,
        "proposal": context["proposal"].model_copy(
            update={"financial_inputs": {"prices": [100.0, 102.0]}}
        ),
    }
    assert _compile(changed_input).plan_hash != baseline.plan_hash

    changed_convention = {
        **context,
        "proposal": context["proposal"].model_copy(update={"conventions": {"kind": "unadjusted"}}),
    }
    assert _compile(changed_convention).plan_hash != baseline.plan_hash


def test_equal_policy_priority_uses_stable_implementation_id_tie_break() -> None:
    context = _context()
    alpha, beta = context["implementations"]
    tied = {
        **context,
        "implementations": (beta, alpha),
        "policy": _policy(
            context["method"],
            context["capabilities"][0],
            ((beta, 10), (alpha, 10)),
        ),
    }
    plan = _compile(tied)
    assert plan.resolution_receipts[0].selected_implementation.id == "impl.alpha"


def test_policy_forbidden_candidate_is_receipted_but_never_selected() -> None:
    context = _context()
    alpha, _beta = context["implementations"]
    restricted = {
        **context,
        "policy": _policy(
            context["method"],
            context["capabilities"][0],
            ((alpha, 10),),
        ),
    }
    plan = _compile(restricted)
    candidates = plan.resolution_receipts[0].relevant_candidates
    assert plan.resolution_receipts[0].selected_implementation == alpha.ref
    assert candidates[1].policy_allowed is False
    assert "forbidden_by_policy" in {reason.value for reason in candidates[1].rejection_reasons}


def test_no_available_policy_eligible_implementation_is_refused() -> None:
    context = _context()
    alpha, beta = context["implementations"]
    outcome = compile_plan(
        **{
            **context,
            "availability": _availability(
                (alpha, beta),
                unavailable=frozenset({alpha.id, beta.id}),
            ),
        }
    )
    assert isinstance(outcome, PlanRefusalV1)
    assert outcome.code.value == "no_approved_implementation"


def test_agent_rationale_is_preserved_but_excluded_from_plan_hash() -> None:
    context = _context()
    first = _compile(context)
    second = _compile(
        {
            **context,
            "proposal": context["proposal"].model_copy(
                update={"agent_rationale": "A different untrusted explanation."}
            ),
        }
    )
    assert first.agent_rationale != second.agent_rationale
    assert first.plan_hash == second.plan_hash


def test_authored_defaults_are_materialized_and_hashed() -> None:
    context = _context()
    defaulted = _compile(context)
    assert defaulted.resolved_financial_inputs["window"] == 5
    assert [item.field for item in defaulted.applied_defaults] == ["window"]

    explicit = _compile(
        {
            **context,
            "proposal": context["proposal"].model_copy(
                update={"financial_inputs": {"prices": [100.0, 101.0], "window": 7}}
            ),
        }
    )
    assert explicit.applied_defaults == ()
    assert explicit.plan_hash != defaulted.plan_hash


def test_missing_convention_needs_information_and_extra_input_is_refused() -> None:
    context = _context()
    missing = compile_plan(
        **{
            **context,
            "proposal": context["proposal"].model_copy(update={"conventions": {}}),
        }
    )
    assert isinstance(missing, NeedsInformationV1)
    assert missing.questions[0].field_path == "conventions.kind"
    assert missing.questions[0].reason_code == "required_convention_missing"
    assert missing.questions[0].allowed_values == ("adjusted", "unadjusted")

    extra = compile_plan(
        **{
            **context,
            "proposal": context["proposal"].model_copy(
                update={
                    "financial_inputs": {
                        "prices": [100.0, 101.0],
                        "unexpected": True,
                    }
                }
            ),
        }
    )
    assert isinstance(extra, PlanRefusalV1)
    assert extra.code.value == "invalid_financial_input"
    assert extra.fields == ("unexpected",)

    missing_and_extra = compile_plan(
        **{
            **context,
            "proposal": context["proposal"].model_copy(
                update={
                    "financial_inputs": {
                        "prices": [100.0, 101.0],
                        "unexpected": True,
                    },
                    "conventions": {},
                }
            ),
        }
    )
    assert isinstance(missing_and_extra, PlanRefusalV1)
    assert missing_and_extra.code.value == "invalid_financial_input"
    assert missing_and_extra.fields == ("unexpected",)


@pytest.mark.parametrize(
    "forbidden",
    [
        "python",
        "sql",
        "url",
        "import_path",
        "mcp_tool",
        "provider",
        "implementation",
        "credentials",
        "implementation_id",
        "provider_name",
        "sql_query",
        "source_url",
        "python_callable",
        "mcp_server",
    ],
)
def test_agent_cannot_supply_code_access_configuration_or_implementation(
    forbidden: str,
) -> None:
    with pytest.raises(ValidationError):
        PlanProposalV1(
            method_id="dq.market_data.synthetic_return",
            method_version="1.0.0",
            financial_inputs={"prices": [100.0, 101.0], forbidden: "not allowed"},
            conventions={"kind": "adjusted"},
        )


def test_agent_cannot_add_top_level_implementation_override() -> None:
    with pytest.raises(ValidationError):
        PlanProposalV1.model_validate(
            {
                "method_id": "dq.market_data.synthetic_return",
                "method_version": "1.0.0",
                "financial_inputs": {"prices": [100.0, 101.0]},
                "conventions": {"kind": "adjusted"},
                "implementation_id": "impl.beta",
            }
        )


def test_method_and_capability_schemas_cannot_expose_control_plane_fields() -> None:
    context = _context()
    method_data = context["method"].model_dump(mode="python")
    method_data["input_schema"]["properties"]["provider_id"] = {"type": "string"}
    with pytest.raises(ValidationError, match="control-plane"):
        MethodSpecV1.model_validate(method_data)

    capability_data = context["capabilities"][0].model_dump(mode="python")
    capability_data["input_schema"]["properties"]["sql_query"] = {"type": "string"}
    with pytest.raises(ValidationError, match="control-plane"):
        CapabilitySpecV1.model_validate(capability_data)


def test_method_defaults_are_complete_and_match_schema_defaults() -> None:
    context = _context()
    without_default = context["method"].model_dump(mode="python")
    without_default["defaults"] = []
    with pytest.raises(ValidationError, match="optional method input"):
        MethodSpecV1.model_validate(without_default)

    conflicting_default = context["method"].model_dump(mode="python")
    conflicting_default["defaults"][0]["value"] = 6
    with pytest.raises(ValidationError, match="schema defaults"):
        MethodSpecV1.model_validate(conflicting_default)

    unresolved_convention = context["method"].model_dump(mode="python")
    unresolved_convention["conventions"][0]["required_from_user"] = False
    with pytest.raises(ValidationError, match="non-user-required conventions"):
        MethodSpecV1.model_validate(unresolved_convention)


def test_method_rejects_invalid_constraint_fields_even_in_short_circuited_branches() -> None:
    context = _context()
    branches = (
        ConstraintConditionV1(
            comparison=ConstraintComparisonV1(
                left=ConstraintOperandV1(field="prices", measure=ConstraintMeasure.COUNT),
                operator=ConstraintOperator.LT,
                right=ConstraintOperandV1(value=2),
            )
        ),
        ConstraintConditionV1(
            comparison=ConstraintComparisonV1(
                left=ConstraintOperandV1(field="zzzz"),
                operator=ConstraintOperator.EQ,
                right=ConstraintOperandV1(value=1),
            )
        ),
    )
    hidden_invalid_field = ConstraintSpecV1(
        id="hidden_invalid_field",
        severity=ConstraintSeverity.BLOCKING,
        when=ConstraintConditionV1(
            all_of=tuple(
                sorted(
                    branches,
                    key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
                )
            )
        ),
        message="A hidden malformed branch must invalidate the complete method specification.",
    )
    with pytest.raises(ValidationError, match="method input fields"):
        context["method"].model_copy(update={"constraints": (hidden_invalid_field,)})

    invalid_measure_branches = (
        branches[0],
        ConstraintConditionV1(
            comparison=ConstraintComparisonV1(
                left=ConstraintOperandV1(
                    field="window",
                    measure=ConstraintMeasure.STDEV,
                ),
                operator=ConstraintOperator.GT,
                right=ConstraintOperandV1(value=0),
            )
        ),
    )
    hidden_invalid_measure = ConstraintSpecV1(
        id="hidden_invalid_measure",
        severity=ConstraintSeverity.BLOCKING,
        when=ConstraintConditionV1(
            all_of=tuple(
                sorted(
                    invalid_measure_branches,
                    key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
                )
            )
        ),
        message="A scalar cannot be evaluated with a sequence-only measure.",
    )
    with pytest.raises(ValidationError, match="measure is incompatible"):
        context["method"].model_copy(update={"constraints": (hidden_invalid_measure,)})

    invalid_operator_branches = (
        branches[0],
        ConstraintConditionV1(
            comparison=ConstraintComparisonV1(
                left=ConstraintOperandV1(field="window"),
                operator=ConstraintOperator.LT,
                right=ConstraintOperandV1(value="not-a-number"),
            )
        ),
    )
    hidden_invalid_operator = ConstraintSpecV1(
        id="hidden_invalid_operator",
        severity=ConstraintSeverity.BLOCKING,
        when=ConstraintConditionV1(
            all_of=tuple(
                sorted(
                    invalid_operator_branches,
                    key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
                )
            )
        ),
        message="Every branch must use operators defined for its operand result types.",
    )
    with pytest.raises(ValidationError, match="operator is incompatible"):
        context["method"].model_copy(update={"constraints": (hidden_invalid_operator,)})


def test_method_rejects_duplicate_convention_question_ids() -> None:
    context = _context()
    duplicate = ConventionSpecV1(
        id="kind",
        field="prices",
        question="A duplicate question ID must not be accepted.",
        materiality="Duplicate IDs would make clarification outcomes ambiguous.",
        required_from_user=True,
    )
    conventions = tuple(
        sorted(
            (*context["method"].conventions, duplicate),
            key=lambda item: (item.field, item.id),
        )
    )
    with pytest.raises(ValidationError, match="convention IDs"):
        context["method"].model_copy(update={"conventions": conventions})


def test_schema_const_and_enum_comparisons_are_json_type_aware() -> None:
    context = _context()
    capability_data = context["capabilities"][0].model_dump(mode="python")
    capability_data["input_schema"]["properties"]["prices"]["items"] = {"enum": [1]}
    capability_data["input_schema"]["properties"]["window"] = {
        "const": 1,
        "default": 1,
    }
    capability = CapabilitySpecV1.model_validate(capability_data)

    method_data = context["method"].model_dump(mode="python")
    method_data["input_schema"] = capability.input_schema
    method_data["capabilities"] = [capability.ref]
    method_data["defaults"][0]["value"] = 1
    method = MethodSpecV1.model_validate(method_data)
    implementation = _implementation(capability, "impl.type_aware")
    policy = _policy(method, capability, ((implementation, 10),))
    proposal = context["proposal"].model_copy(
        update={"financial_inputs": {"prices": [True, True], "window": True}}
    )
    outcome = compile_plan(
        proposal,
        method,
        (capability,),
        (implementation,),
        policy,
        _availability((implementation,)),
    )
    assert isinstance(outcome, PlanRefusalV1)
    assert outcome.code.value == "invalid_financial_input"
    assert outcome.fields == ("prices", "window")


def test_convention_allowed_values_are_enforced_independently_of_schema() -> None:
    context = _context()
    method_data = context["method"].model_dump(mode="python")
    method_data["input_schema"]["properties"]["kind"]["enum"] = [
        "adjusted",
        "raw",
        "unadjusted",
    ]
    broadened = MethodSpecV1.model_validate(method_data)
    policy = _policy(
        broadened,
        context["capabilities"][0],
        tuple((item, index * 10 + 10) for index, item in enumerate(context["implementations"])),
    )
    outcome = compile_plan(
        context["proposal"].model_copy(update={"conventions": {"kind": "raw"}}),
        broadened,
        context["capabilities"],
        context["implementations"],
        policy,
        context["availability"],
    )
    assert isinstance(outcome, PlanRefusalV1)
    assert outcome.code.value == "convention_conflict"
    assert outcome.fields == ("kind",)


def test_compiled_binding_has_one_selection_and_explicitly_forbids_runtime_fallback() -> None:
    plan = _compile(_context())
    receipt = plan.resolution_receipts[0]
    assert sum(item.selected for item in receipt.relevant_candidates) == 1
    assert receipt.runtime_fallback_allowed is False
    assert receipt.selected_implementation == receipt.relevant_candidates[0].implementation


def test_hash_bound_candidate_cannot_contradict_eligibility_facts() -> None:
    candidate = _compile(_context()).resolution_receipts[0].relevant_candidates[0]
    with pytest.raises(ValidationError, match="rejection reasons"):
        candidate.model_copy(
            update={
                "policy_allowed": False,
                "trust_requirements_satisfied": False,
                "transport_allowed": False,
            }
        )


def test_production_simple_return_vertical_slice_and_subject_hash_compatibility() -> None:
    assert subject_hash("dq.market_data.simple_return") == SIMPLE_RETURN_SUBJECT
    service = DefinedQuantService()
    try:
        plan = service.compile_plan(
            PlanProposalV1(
                method_id="dq.market_data.simple_return",
                method_version="0.3.4",
                financial_inputs={"prices": [100.0, 110.0]},
                conventions={"price_kind": "adjusted"},
            )
        )
    finally:
        service.close()
    assert isinstance(plan, CompiledPlanV1)
    assert plan.claims == ("PLAN VALIDATION PASSED", "ELIGIBLE UNDER POLICY")
    selected = plan.resolution_receipts[0].selected_implementation
    assert selected.id == "dq_native.market_data.simple_return"
    assert plan.resolved_financial_inputs["timestamps"] is None


@pytest.mark.parametrize(
    "timestamps",
    [
        ["garbage", "still-garbage"],
        ["2025-02-30T00:00:00Z", "2025-03-01T00:00:00Z"],
        ["2025-01-01T00:00:00", "2025-03-01T00:00:00Z"],
    ],
)
def test_production_datetime_inputs_require_valid_aware_rfc3339_values(
    timestamps: list[str],
) -> None:
    service = DefinedQuantService()
    try:
        outcome = service.compile_plan(
            PlanProposalV1(
                method_id="dq.market_data.simple_return",
                method_version="0.3.4",
                financial_inputs={"prices": [100.0, 110.0], "timestamps": timestamps},
                conventions={"price_kind": "adjusted"},
            )
        )
    finally:
        service.close()
    assert isinstance(outcome, PlanRefusalV1)
    assert outcome.code.value == "invalid_financial_input"
    assert outcome.fields == ("timestamps",)


def test_timestamp_ordering_is_evaluated_by_instant_not_lexical_text() -> None:
    service = DefinedQuantService()
    try:
        outcome = service.compile_plan(
            PlanProposalV1(
                method_id="dq.market_data.simple_return",
                method_version="0.3.4",
                financial_inputs={
                    "prices": [100.0, 110.0],
                    "timestamps": [
                        "2024-01-01T00:00:00-01:00",
                        "2024-01-01T00:30:00+01:00",
                    ],
                },
                conventions={"price_kind": "adjusted"},
            )
        )
    finally:
        service.close()
    assert isinstance(outcome, PlanRefusalV1)
    assert outcome.code.value == "constraint_violation"


def test_scalar_all_finite_constraints_use_the_canonical_evaluator() -> None:
    service = DefinedQuantService()
    try:
        volatility = service.compile_plan(
            PlanProposalV1(
                method_id="dq.volatility.historical_volatility",
                method_version="0.1.2",
                financial_inputs={"returns": [0.01, -0.02, 0.015]},
                conventions={"annualization_factor": 252.0, "return_kind": "log"},
            )
        )
        rebased = service.compile_plan(
            PlanProposalV1(
                method_id="dq.market_data.rebased_price_index",
                method_version="0.1.2",
                financial_inputs={"prices": [100.0, 101.0]},
                conventions={
                    "base_index": 0,
                    "base_value": 100.0,
                    "price_kind": "adjusted",
                },
            )
        )
    finally:
        service.close()
    assert isinstance(volatility, CompiledPlanV1)
    assert isinstance(rebased, CompiledPlanV1)


def test_service_unknown_method_and_version_return_typed_refusals() -> None:
    service = DefinedQuantService()
    try:
        unknown = service.compile_plan(
            PlanProposalV1(
                method_id="dq.market_data.not_installed",
                method_version="1.0.0",
                financial_inputs={},
                conventions={},
            )
        )
        wrong_version = service.compile_plan(
            PlanProposalV1(
                method_id="dq.market_data.simple_return",
                method_version="9.9.9",
                financial_inputs={},
                conventions={},
            )
        )
    finally:
        service.close()
    assert isinstance(unknown, PlanRefusalV1)
    assert unknown.code.value == "method_not_found"
    assert isinstance(wrong_version, PlanRefusalV1)
    assert wrong_version.code.value == "method_identity_mismatch"


def test_injected_registry_requires_an_explicit_policy() -> None:
    context = _context()
    registry = GovernedRegistry(
        methods=(context["method"],),
        capabilities=context["capabilities"],
        implementations=context["implementations"],
    )
    service = DefinedQuantService(
        governed_registry=registry,
        governed_availability=context["availability"],
    )
    try:
        outcome = service.compile_plan(context["proposal"])
    finally:
        service.close()
    assert isinstance(outcome, PlanRefusalV1)
    assert outcome.code.value == "no_approved_implementation"


def test_every_current_component_has_one_legacy_governance_projection() -> None:
    service = DefinedQuantService()
    try:
        component_ids = tuple(record.component_id for record in service.contract_index.records)
        projected = tuple(
            project_component_inspection(service.inspect_component(component_id, view="governance"))
            for component_id in component_ids
        )
    finally:
        service.close()
    assert len(projected) == 7
    assert all(len(item.methods) == 1 for item in projected)
    assert all(len(item.capabilities) == 1 for item in projected)
    assert all(len(item.implementations) == 1 for item in projected)
    assert all(item.implementations[0].id.startswith("dq_native.") for item in projected)


def test_registry_rejects_unregistered_capability() -> None:
    context = _context()
    with pytest.raises(ValueError, match="unregistered capability"):
        GovernedRegistry(
            methods=(context["method"],),
            capabilities=(),
            implementations=context["implementations"],
        )


def test_duplicate_implementation_records_are_refused() -> None:
    context = _context()
    alpha, beta = context["implementations"]
    outcome = compile_plan(
        **{
            **context,
            "implementations": (alpha, alpha, beta),
        }
    )
    assert isinstance(outcome, PlanRefusalV1)
    assert outcome.code.value == "invalid_governance_record"


def test_recipe_requires_complete_bindings_and_exact_field_schemas() -> None:
    context = _context()
    method = context["method"]
    incomplete_recipe = RecipeDagV1(
        steps=(
            RecipeStepV1(
                step_id="calculate",
                capability_id=context["capabilities"][0].id,
                input_bindings=tuple(
                    binding
                    for binding in method.recipe.steps[0].input_bindings
                    if binding.target_field != "window"
                ),
                output_fields=("returns",),
            ),
        ),
        result_steps=("calculate",),
    )
    incomplete_method = method.model_copy(update={"recipe": incomplete_recipe})
    incomplete_policy = _policy(
        incomplete_method,
        context["capabilities"][0],
        tuple((item, index * 10 + 10) for index, item in enumerate(context["implementations"])),
    )
    incomplete = compile_plan(
        context["proposal"],
        incomplete_method,
        context["capabilities"],
        context["implementations"],
        incomplete_policy,
        context["availability"],
    )
    assert isinstance(incomplete, PlanRefusalV1)
    assert incomplete.code.value == "incompatible_implementation"

    method_data = method.model_dump(mode="python")
    method_data["input_schema"]["properties"]["prices"]["items"] = {"type": "string"}
    mismatched_method = MethodSpecV1.model_validate(method_data)
    mismatched_policy = _policy(
        mismatched_method,
        context["capabilities"][0],
        tuple((item, index * 10 + 10) for index, item in enumerate(context["implementations"])),
    )
    mismatch = compile_plan(
        context["proposal"].model_copy(
            update={"financial_inputs": {"prices": ["100", "101"]}}
        ),
        mismatched_method,
        context["capabilities"],
        context["implementations"],
        mismatched_policy,
        context["availability"],
    )
    assert isinstance(mismatch, PlanRefusalV1)
    assert mismatch.code.value == "incompatible_implementation"

    with pytest.raises(ValidationError, match="port metadata"):
        context["capabilities"][0].model_copy(update={"input_ports": ()})


def test_synthetic_multi_step_recipe_resolves_each_capability_once() -> None:
    transform = _capability("statistics.return_transform")
    return_input = (
        NamedPortV1(
            field="returns",
            port=_port(direction="input", concept="periodic_return_series"),
        ),
    )
    validation_input_schema = {
        "type": "object",
        "properties": {
            "returns": {
                "type": "array",
                "items": {"type": "number"},
                "x-defined-quant-port": return_input[0].port.model_dump(mode="json"),
            }
        },
        "required": ["returns"],
        "additionalProperties": False,
    }
    validate = CapabilitySpecV1(
        id="statistics.return_validation",
        version="1.0.0",
        title="Synthetic return validation",
        kind=CapabilityKind.VERIFICATION,
        summary="Checks an already computed synthetic return series.",
        input_schema=validation_input_schema,
        output_schema=OUTPUT_SCHEMA,
        input_ports=return_input,
        output_ports=OUTPUT_PORTS,
    )
    method = MethodSpecV1(
        id="dq.market_data.synthetic_return",
        version="1.0.0",
        title="Synthetic multi-step return",
        summary="A two-step backend-neutral compiler fixture.",
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        input_ports=INPUT_PORTS,
        output_ports=OUTPUT_PORTS,
        methodology=("Transform prices, then validate the typed return series.",),
        interpretation="Interpret the final series as synthetic decimal returns.",
        conventions=(
            ConventionSpecV1(
                id="kind",
                field="kind",
                question="Are the prices adjusted or unadjusted?",
                materiality="The choice changes the economic interpretation.",
                required_from_user=True,
                allowed_values=("adjusted", "unadjusted"),
            ),
        ),
        defaults=(
            DefaultSpecV1(
                field="window",
                value=5,
                rationale="The fixture explicitly authors this input default.",
            ),
        ),
        capabilities=tuple(sorted((transform.ref, validate.ref), key=lambda item: item.id)),
        recipe=RecipeDagV1(
            steps=(
                RecipeStepV1(
                    step_id="calculate",
                    capability_id=transform.id,
                    input_bindings=tuple(
                        RecipeInputBindingV1(
                            target_field=field,
                            source=RecipeValueSourceV1(
                                source="method_input",
                                field=field,
                            ),
                        )
                        for field in ("kind", "prices", "window")
                    ),
                    output_fields=("returns",),
                ),
                RecipeStepV1(
                    step_id="validate",
                    capability_id=validate.id,
                    depends_on=("calculate",),
                    input_bindings=(
                        RecipeInputBindingV1(
                            target_field="returns",
                            source=RecipeValueSourceV1(
                                source="step_output",
                                step_id="calculate",
                                field="returns",
                            ),
                        ),
                    ),
                    output_fields=("returns",),
                ),
            ),
            result_steps=("validate",),
        ),
    )
    transform_impl = _implementation(transform, "impl.transform")
    validate_impl = ImplementationSpecV1(
        **{
            **_implementation(validate, "impl.validate").model_dump(
                mode="python", exclude={"input_ports"}
            ),
            "input_ports": return_input,
        }
    )
    rules = (
        _policy(method, transform, ((transform_impl, 10),)).capability_rules[0],
        _policy(method, validate, ((validate_impl, 10),)).capability_rules[0],
    )
    policy = ResolutionPolicyV1(
        id="professional_default",
        version="1.0.0",
        method=method.ref,
        capability_rules=tuple(sorted(rules, key=lambda item: item.capability.id)),
    )
    outcome = compile_plan(
        PlanProposalV1(
            method_id=method.id,
            method_version=method.version,
            financial_inputs={"prices": [100.0, 102.0]},
            conventions={"kind": "adjusted"},
        ),
        method,
        (transform, validate),
        (validate_impl, transform_impl),
        policy,
        _availability((validate_impl, transform_impl)),
    )
    assert isinstance(outcome, CompiledPlanV1)
    assert tuple(item.step_id for item in outcome.resolution_receipts) == (
        "calculate",
        "validate",
    )
    assert tuple(item.selected_implementation.id for item in outcome.resolution_receipts) == (
        "impl.transform",
        "impl.validate",
    )


def test_compiler_module_has_no_ambient_io_imports() -> None:
    source = (ROOT / "shared/plan_compiler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert imports.isdisjoint(
        {
            "importlib",
            "os",
            "pathlib",
            "random",
            "socket",
            "subprocess",
            "time",
            "uuid",
        }
    )
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source


def test_core_runtime_dependency_boundary_remains_pydantic_only() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dependencies = ["pydantic>=2.7,<3"]' in pyproject
