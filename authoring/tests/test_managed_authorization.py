"""Contract tests for exact-subject atomic managed authorization."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import defined_quant.plan_validation as plan_validation
import pytest
from defined_quant import (
    create_authorization_binding,
    create_manual_approval,
    load_execution_policy,
    validate_plan,
    verify_authorization,
)
from defined_quant_protocol import (
    AnalysisPlan,
    ApprovalRecord,
    AuthorizationBinding,
    AuthorizationErrorCode,
    AuthorizationFailure,
    AuthorizationSuccess,
    CallerProvenance,
    ComponentRef,
    DatasetBinding,
    ExecutionPolicy,
    InterpretationMethod,
    OperationManifest,
    OperationRequest,
    PlanStep,
    ProvenanceStatus,
    QuestionVerificationStatus,
    ResolvedQuestion,
    RunnerIdentity,
    SourceKind,
    ValidationCheck,
    ValidationFailure,
    ValidationIssue,
    ValidationIssueCode,
    ValidationReceipt,
    managed_authorization_protocol_schema,
    operation_protocol_schema,
)
from pydantic import ValidationError

SIMPLE_RETURN = ComponentRef(
    id="dq.market_data.simple_return",
    version="0.3.3",
    subject_hash="63a2e74034b45f567fda32b263dc61441f16c9cfd5107f88b41505d16de39cab",
)
TIMESTAMPS = [
    "2026-07-24T16:00:00Z",
    "2026-07-27T16:00:00Z",
    "2026-07-28T16:00:00Z",
]
APPROVED_AT = datetime(2026, 8, 10, 10, 30, tzinfo=UTC)
RESOLVED_AT = datetime(2026, 8, 10, 10, 15, tzinfo=UTC)


def _dataset(
    *,
    price_kind: str | None = "adjusted",
    timestamps: list[str] | None = TIMESTAMPS,
) -> DatasetBinding:
    input_data: dict[str, Any] = {
        "prices": [100.0, 105.0, 102.9],
        "timestamps": timestamps,
        "declared_frequency": "daily",
    }
    if price_kind is not None:
        input_data["price_kind"] = price_kind
    return DatasetBinding(dataset_id="candidate_prices", input=input_data)


def _resolution(
    *,
    answer: str = "adjusted",
    status: QuestionVerificationStatus = QuestionVerificationStatus.CALLER_CONFIRMED,
    resolved_at: datetime = RESOLVED_AT,
) -> ResolvedQuestion:
    return ResolvedQuestion(
        question_id="price_kind",
        target_field="price_kind",
        answer=answer,
        supplied_by="requesting_analyst",
        verification_status=status,
        resolved_at=resolved_at,
    )


def _plan(
    *,
    policy: ExecutionPolicy | None = None,
    component: ComponentRef = SIMPLE_RETURN,
    dataset: DatasetBinding | None = None,
    resolutions: tuple[ResolvedQuestion, ...] | None = None,
    explicit_draft_opt_in: bool = True,
    lifecycle: str = "draft",
    revision: int = 1,
    parent_plan_hash: str | None = None,
    objective: str = "Calculate adjacent-period simple returns from supplied prices.",
) -> AnalysisPlan:
    bound_policy = policy or load_execution_policy("simple_return_csv_v1")
    return AnalysisPlan(
        plan_id="simple_return_analysis",
        revision=revision,
        parent_plan_hash=parent_plan_hash,
        objective=objective,
        resolved_questions=resolutions if resolutions is not None else (_resolution(),),
        step=PlanStep(
            step_id="calculate_returns",
            profile_id="simple_return_csv_v1",
            component=component,
            component_lifecycle=lifecycle,
            explicit_draft_opt_in=explicit_draft_opt_in,
            dataset=dataset or _dataset(),
        ),
        expected_outputs=("Decimal simple-return series aligned to period ends.",),
        stop_conditions=("Stop before calculation if authorization is stale.",),
        execution_policy_hash=bound_policy.policy_hash,
    )


def _authorized_chain() -> tuple[
    ExecutionPolicy,
    AnalysisPlan,
    ValidationReceipt,
    ApprovalRecord,
    AuthorizationBinding,
]:
    policy = load_execution_policy("simple_return_csv_v1")
    plan = _plan(policy=policy)
    outcome = validate_plan(plan, policy)
    assert isinstance(outcome, ValidationReceipt)
    approval = create_manual_approval(
        outcome,
        approved_by="portfolio_reviewer",
        approved_at=APPROVED_AT,
    )
    binding = create_authorization_binding(plan, outcome, approval)
    return policy, plan, outcome, approval, binding


def test_plan_validation_freshly_verifies_installed_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | Path | None]] = []
    original: Any = getattr(plan_validation, "verify_subject")

    def verified(component_id: str, *, root: str | Path | None = None) -> str:
        calls.append((component_id, root))
        return original(component_id, root=root)

    monkeypatch.setattr(plan_validation, "verify_subject", verified)
    policy = load_execution_policy("simple_return_csv_v1")

    outcome = validate_plan(_plan(policy=policy), policy)

    assert isinstance(outcome, ValidationReceipt)
    assert len(calls) == 1
    assert calls[0][0] == "dq.market_data.simple_return"
    assert isinstance(calls[0][1], Path)


def test_exact_subject_atomic_plan_receipt_and_manual_approval_authorize() -> None:
    policy, plan, receipt, approval, binding = _authorized_chain()
    repeated = validate_plan(plan, policy)
    outcome = verify_authorization(plan, receipt, approval, binding, policy)

    assert policy.policy_id == "simple_return_csv_v1"
    assert len(policy.components) == 1
    assert policy.components[0].component == SIMPLE_RETURN
    assert policy.components[0].allowed_lifecycle == "draft"
    assert policy.components[0].require_explicit_opt_in is True
    assert isinstance(repeated, ValidationReceipt)
    assert repeated == receipt
    assert repeated.receipt_hash == receipt.receipt_hash
    assert receipt.plan_hash == plan.plan_hash
    assert receipt.dataset_hash == plan.step.dataset.dataset_hash
    assert approval.authorization_kind == "manual"
    assert isinstance(outcome, AuthorizationSuccess)
    assert outcome.binding == binding


def test_missing_or_unconfirmed_required_answer_needs_information_without_receipt() -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    missing = validate_plan(
        _plan(policy=policy, dataset=_dataset(price_kind=None), resolutions=()),
        policy,
    )
    unconfirmed = validate_plan(
        _plan(
            policy=policy,
            resolutions=(
                _resolution(status=QuestionVerificationStatus.UNVERIFIED),
            ),
        ),
        policy,
    )

    for outcome in (missing, unconfirmed):
        assert isinstance(outcome, ValidationFailure)
        assert outcome.status == "needs_information"
        assert outcome.issues[0].code is ValidationIssueCode.PLAN_REQUIRED_QUESTION_UNRESOLVED
        assert outcome.questions[0].target_field == "price_kind"
        assert not isinstance(outcome, ValidationReceipt)


def test_conflicting_resolved_answer_is_blocked() -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    outcome = validate_plan(
        _plan(policy=policy, dataset=_dataset(price_kind="unadjusted")),
        policy,
    )

    assert isinstance(outcome, ValidationFailure)
    assert outcome.status == "blocked"
    assert outcome.issues[0].code is ValidationIssueCode.PLAN_RESOLVED_QUESTION_MISMATCH


def test_required_answer_comparison_distinguishes_booleans_from_numbers() -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    dataset = DatasetBinding(
        dataset_id="candidate_prices",
        input={
            "prices": [100.0, 105.0],
            "price_kind": 1,
            "timestamps": TIMESTAMPS[:2],
        },
    )
    boolean_resolution = ResolvedQuestion(
        question_id="price_kind",
        target_field="price_kind",
        answer=True,
        supplied_by="requesting_analyst",
        verification_status=QuestionVerificationStatus.CALLER_CONFIRMED,
        resolved_at=RESOLVED_AT,
    )
    outcome = validate_plan(
        _plan(policy=policy, dataset=dataset, resolutions=(boolean_resolution,)),
        policy,
    )

    assert isinstance(outcome, ValidationFailure)
    assert outcome.issues[0].code is ValidationIssueCode.PLAN_RESOLVED_QUESTION_MISMATCH


def test_invalid_input_and_blocking_component_constraints_never_issue_receipts() -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    invalid_kind = validate_plan(
        _plan(
            policy=policy,
            dataset=_dataset(price_kind="vendor_defined"),
            resolutions=(_resolution(answer="vendor_defined"),),
        ),
        policy,
    )
    insufficient_data = DatasetBinding(
        dataset_id="candidate_prices",
        input={
            "prices": [100.0],
            "price_kind": "adjusted",
            "timestamps": [TIMESTAMPS[0]],
        },
    )
    blocked_constraint = validate_plan(
        _plan(policy=policy, dataset=insufficient_data),
        policy,
    )

    assert isinstance(invalid_kind, ValidationFailure)
    assert invalid_kind.issues[0].code is ValidationIssueCode.INVALID_COMPONENT_INPUT
    assert isinstance(blocked_constraint, ValidationFailure)
    assert (
        blocked_constraint.issues[0].code
        is ValidationIssueCode.COMPONENT_CONSTRAINT_VIOLATION
    )


def test_large_invalid_input_fails_closed_with_a_bounded_typed_issue() -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    oversized_input: dict[str, Any] = {
        **_dataset().input,
        **{f"unexpected_{index}": index for index in range(100)},
    }
    plan = _plan(
        policy=policy,
        dataset=DatasetBinding(dataset_id="oversized_input", input=oversized_input),
    )

    outcome = validate_plan(plan, policy)

    assert isinstance(outcome, ValidationFailure)
    assert outcome.issues[0].code is ValidationIssueCode.INVALID_COMPONENT_INPUT
    assert len(outcome.issues[0].message) <= 500


def test_answer_revision_changes_plan_and_stales_old_authorization() -> None:
    policy, first, receipt, approval, binding = _authorized_chain()
    revised = _plan(
        policy=policy,
        dataset=_dataset(price_kind="unadjusted"),
        resolutions=(_resolution(answer="unadjusted"),),
        revision=2,
        parent_plan_hash=first.plan_hash,
    )
    revised_receipt = validate_plan(revised, policy)
    stale = verify_authorization(revised, receipt, approval, binding, policy)

    assert revised.plan_hash != first.plan_hash
    assert isinstance(revised_receipt, ValidationReceipt)
    assert revised_receipt.plan_hash == revised.plan_hash
    assert isinstance(stale, AuthorizationFailure)


def test_resolution_time_is_operational_but_data_timestamps_are_semantic() -> None:
    policy, plan, receipt, approval, binding = _authorized_chain()
    later_resolution = _resolution(
        resolved_at=datetime(2026, 8, 10, 11, 15, tzinfo=UTC)
    )
    same_semantics = plan.model_copy(
        update={"resolved_questions": (later_resolution,)}
    )
    changed_dataset = plan.step.dataset.model_copy(
        update={
            "input": {
                **plan.step.dataset.input,
                "timestamps": [
                    TIMESTAMPS[0],
                    TIMESTAMPS[1],
                    "2026-07-29T16:00:00Z",
                ],
            }
        }
    )
    timestamp_change = plan.model_copy(
        update={"step": plan.step.model_copy(update={"dataset": changed_dataset})}
    )
    same_authorization = verify_authorization(
        same_semantics,
        receipt,
        approval,
        binding,
        policy,
    )
    stale = verify_authorization(timestamp_change, receipt, approval, binding, policy)

    assert same_semantics.plan_hash == plan.plan_hash
    assert isinstance(same_authorization, AuthorizationSuccess)
    assert timestamp_change.plan_hash != plan.plan_hash
    assert changed_dataset.dataset_hash != plan.step.dataset.dataset_hash
    assert isinstance(stale, AuthorizationFailure)
    assert stale.code is AuthorizationErrorCode.DATASET_BINDING_MISMATCH


@pytest.mark.parametrize("timestamps", [None, []])
def test_managed_profile_requires_nonempty_timestamps(timestamps: list[str] | None) -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    outcome = validate_plan(_plan(policy=policy, dataset=_dataset(timestamps=timestamps)), policy)

    assert isinstance(outcome, ValidationFailure)
    assert outcome.status == "blocked"
    assert outcome.issues[0].code is ValidationIssueCode.MANAGED_TIMESTAMP_REQUIRED
    assert outcome.issues[0].field == "timestamps"


@pytest.mark.parametrize(
    "component",
    [
        ComponentRef(
            id="dq.market_data.unknown_return",
            version=SIMPLE_RETURN.version,
            subject_hash=SIMPLE_RETURN.subject_hash,
        ),
        SIMPLE_RETURN.model_copy(update={"version": "0.3.2"}),
        SIMPLE_RETURN.model_copy(update={"subject_hash": "b" * 64}),
    ],
)
def test_policy_refuses_every_nonexact_component_identity(component: ComponentRef) -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    outcome = validate_plan(_plan(policy=policy, component=component), policy)

    assert isinstance(outcome, ValidationFailure)
    assert outcome.status == "blocked"
    assert outcome.issues[0].code is ValidationIssueCode.PLAN_COMPONENT_IDENTITY_MISMATCH


def test_unknown_profile_and_unbound_policy_are_outside_managed_scope() -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    plan = _plan(policy=policy)
    unknown_profile = plan.model_copy(
        update={"step": plan.step.model_copy(update={"profile_id": "unknown_profile"})}
    )
    unbound_policy = plan.model_copy(update={"execution_policy_hash": "d" * 64})

    scope_failure = validate_plan(unknown_profile, policy)
    policy_failure = validate_plan(unbound_policy, policy)

    assert isinstance(scope_failure, ValidationFailure)
    assert scope_failure.issues[0].code is ValidationIssueCode.EXECUTION_SCOPE_DENIED
    assert isinstance(policy_failure, ValidationFailure)
    assert policy_failure.issues[0].code is ValidationIssueCode.PLAN_POLICY_MISMATCH


def test_caller_constructed_policy_cannot_replace_the_packaged_allowlist() -> None:
    packaged = load_execution_policy("simple_return_csv_v1")
    caller_policy = ExecutionPolicy.model_validate(
        {**packaged.model_dump(mode="json"), "version": "1.0.10"}
    )
    plan = _plan(policy=caller_policy)

    outcome = validate_plan(plan, caller_policy)

    assert isinstance(outcome, ValidationFailure)
    assert outcome.issues[0].code is ValidationIssueCode.EXECUTION_SCOPE_DENIED


@pytest.mark.parametrize(
    ("lifecycle", "opt_in"),
    [("published", True), ("draft", False)],
)
def test_draft_lifecycle_requires_truthful_identity_and_explicit_opt_in(
    lifecycle: str,
    opt_in: bool,
) -> None:
    policy = load_execution_policy("simple_return_csv_v1")
    outcome = validate_plan(
        _plan(policy=policy, lifecycle=lifecycle, explicit_draft_opt_in=opt_in),
        policy,
    )

    assert isinstance(outcome, ValidationFailure)
    assert outcome.issues[0].code is ValidationIssueCode.PLAN_COMPONENT_LIFECYCLE_DENIED


def test_plan_receipt_approval_policy_and_component_mutations_fail_independently() -> None:
    policy, plan, receipt, approval, binding = _authorized_chain()

    changed_plan = plan.model_copy(update={"objective": "A changed execution objective."})
    changed_receipt = receipt.model_copy(
        update={"validator": RunnerIdentity(name="other_validator", version="0.1.0")}
    )
    changed_approval = approval.model_copy(update={"approved_by": "different_reviewer"})
    changed_policy = policy.model_copy(update={"version": "1.0.10"})
    changed_component_plan = plan.model_copy(
        update={
            "step": plan.step.model_copy(
                update={
                    "component": SIMPLE_RETURN.model_copy(update={"subject_hash": "c" * 64})
                }
            )
        }
    )

    failures = (
        verify_authorization(changed_plan, receipt, approval, binding, policy),
        verify_authorization(plan, changed_receipt, approval, binding, policy),
        verify_authorization(plan, receipt, changed_approval, binding, policy),
        verify_authorization(plan, receipt, approval, binding, changed_policy),
        verify_authorization(changed_component_plan, receipt, approval, binding, policy),
    )
    assert [outcome.code for outcome in failures if isinstance(outcome, AuthorizationFailure)] == [
        AuthorizationErrorCode.PLAN_REVISION_STALE,
        AuthorizationErrorCode.VALIDATION_RECEIPT_MISMATCH,
        AuthorizationErrorCode.APPROVAL_BINDING_MISMATCH,
        AuthorizationErrorCode.POLICY_BINDING_MISMATCH,
        AuthorizationErrorCode.COMPONENT_BINDING_MISMATCH,
    ]


def test_fabricated_receipt_is_recomputed_and_rejected() -> None:
    policy, plan, receipt, _, _ = _authorized_chain()
    fabricated = receipt.model_copy(
        update={"validator": RunnerIdentity(name="fabricated_validator", version="9.9.9")}
    )
    approval = create_manual_approval(
        fabricated,
        approved_by="portfolio_reviewer",
        approved_at=APPROVED_AT,
    )
    binding = create_authorization_binding(plan, fabricated, approval)
    outcome = verify_authorization(plan, fabricated, approval, binding, policy)

    assert isinstance(outcome, AuthorizationFailure)
    assert outcome.code is AuthorizationErrorCode.VALIDATION_RECEIPT_MISMATCH


def test_authorization_binding_itself_is_reproduced_before_success() -> None:
    policy, plan, receipt, approval, binding = _authorized_chain()
    mutated = binding.model_copy(update={"schema_version": 2})

    outcome = verify_authorization(plan, receipt, approval, mutated, policy)

    assert isinstance(outcome, AuthorizationFailure)
    assert outcome.code is AuthorizationErrorCode.AUTHORIZATION_BINDING_MISMATCH


def test_nested_dataset_mutation_is_detected_by_mandatory_revalidation() -> None:
    policy, plan, receipt, approval, binding = _authorized_chain()
    captured_dataset_hash = plan.step.dataset.dataset_hash
    captured_plan_hash = plan.plan_hash
    timestamps = plan.step.dataset.input["timestamps"]
    assert isinstance(timestamps, list)
    timestamps[-1] = "2026-07-29T16:00:00Z"

    outcome = verify_authorization(plan, receipt, approval, binding, policy)

    assert plan.step.dataset.dataset_hash != captured_dataset_hash
    assert plan.plan_hash != captured_plan_hash
    assert isinstance(outcome, AuthorizationFailure)
    assert outcome.code is AuthorizationErrorCode.DATASET_BINDING_MISMATCH


def test_noncanonical_nested_tamper_returns_a_typed_refusal() -> None:
    policy, plan, receipt, approval, binding = _authorized_chain()
    prices = plan.step.dataset.input["prices"]
    assert isinstance(prices, list)
    prices[0] = float("nan")

    outcome = verify_authorization(plan, receipt, approval, binding, policy)

    assert isinstance(outcome, AuthorizationFailure)
    assert outcome.code is AuthorizationErrorCode.DATASET_BINDING_MISMATCH


def test_manual_approval_and_success_receipt_schemas_are_closed_and_unique() -> None:
    _, _, receipt, approval, _ = _authorized_chain()
    approval_properties = ApprovalRecord.model_json_schema()["properties"]

    assert set(approval_properties) == {
        "schema_version",
        "authorization_kind",
        "plan_hash",
        "validation_receipt_hash",
        "approved_by",
        "approved_at",
        "note",
    }
    assert "policy" not in approval_properties
    with pytest.raises(ValidationError):
        ApprovalRecord.model_validate({**approval.model_dump(mode="json"), "policy": None})
    with pytest.raises(ValidationError, match="check IDs must be unique"):
        ValidationReceipt(
            **{
                **receipt.model_dump(mode="python"),
                "checks": (ValidationCheck(check_id="same"),) * 2,
            }
        )
    with pytest.raises(ValidationError, match="question IDs must be unique"):
        ValidationFailure(
            status="needs_information",
            plan_hash=receipt.plan_hash,
            issues=(
                ValidationIssue(
                    code=ValidationIssueCode.PLAN_REQUIRED_QUESTION_UNRESOLVED,
                    message="question required",
                ),
            ),
            questions=(
                {
                    "question_id": "same",
                    "target_field": "first",
                    "prompt": "First?",
                    "materiality": "Changes the answer.",
                },
                {
                    "question_id": "same",
                    "target_field": "second",
                    "prompt": "Second?",
                    "materiality": "Changes the answer.",
                },
            ),
        )


def test_managed_protocol_descriptor_is_reproducible_and_authorization_only() -> None:
    descriptor = managed_authorization_protocol_schema()

    assert descriptor["protocol_version"] == "0.4.0"
    assert descriptor["execution_mode"] == "managed_authorization_only"
    assert descriptor["hash_framing"] == operation_protocol_schema()["hash_framing"]
    assert descriptor["hash_domains"] == {
        "policy": "managed.execution_policy.v1",
        "dataset": "managed.dataset_binding.v1",
        "plan": "managed.analysis_plan.v1",
        "validation_receipt": "managed.validation_receipt.v1",
        "manual_approval": "managed.manual_approval.v1",
        "authorization_binding": "managed.authorization_binding.v1",
    }
    assert "execution_result" not in descriptor["schemas"]


def test_analysis_plan_defaults_to_protocol_0_4_and_reads_older_managed_protocols() -> None:
    current = _plan()
    protocol_0_2 = current.model_dump(mode="json")
    protocol_0_2["protocol_version"] = "0.2.0"
    protocol_0_3 = current.model_dump(mode="json")
    protocol_0_3["protocol_version"] = "0.3.0"

    assert current.protocol_version == "0.4.0"
    assert AnalysisPlan.model_validate(protocol_0_2).protocol_version == "0.2.0"
    assert AnalysisPlan.model_validate(protocol_0_3).protocol_version == "0.3.0"

    protocol_0_2["protocol_version"] = "0.1.0"
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(protocol_0_2)


def test_direct_operation_request_remains_visibly_unmanaged_without_managed_fields() -> None:
    request = OperationRequest(
        component=SIMPLE_RETURN,
        input={"prices": [100.0, 105.0], "price_kind": "adjusted"},
        provenance=CallerProvenance(
            source_kind=SourceKind.USER_ATTACHMENT,
            interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
            verification_status=ProvenanceStatus.UNVERIFIED,
            label="caller-supplied prices",
        ),
    )
    manifest_schema = OperationManifest.model_json_schema()

    assert request.input["price_kind"] == "adjusted"
    assert operation_protocol_schema()["execution_mode"] == "unmanaged"
    assert manifest_schema["properties"]["execution_mode"]["const"] == "unmanaged"
    assert "approval" not in OperationRequest.model_json_schema()["properties"]


def test_validation_is_catalog_wide_and_component_identity_lives_only_in_policy_data() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "shared" / "plan_validation.py"
    ).read_text(encoding="utf-8")

    assert "dq.market_data.simple_return" not in source
    assert "simple_return_csv_v1" not in source
