"""Catalog-aware validation for atomic managed authorization.

This module may inspect component contracts and input models, but it never calls a component.
The resulting authorization therefore permits a later run without claiming that a run occurred.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from importlib import resources
from typing import Any

from defined_quant.catalog import component_models, component_record, subject_hash
from defined_quant.types import (
    AmbiguousInput,
    ComponentContractError,
    ComponentLoadError,
    ComponentNotFound,
    ContractEvaluationError,
    DomainError,
)
from defined_quant.validation import preflight
from defined_quant_protocol import (
    AnalysisPlan,
    ApprovalRecord,
    AuthorizationBinding,
    AuthorizationErrorCode,
    AuthorizationFailure,
    AuthorizationOutcome,
    AuthorizationSuccess,
    ClarificationQuestion,
    ComponentLifecycle,
    ExecutionPolicy,
    ManagedComponentRule,
    QuestionVerificationStatus,
    RunnerIdentity,
    ValidationCheck,
    ValidationFailure,
    ValidationIssue,
    ValidationIssueCode,
    ValidationReceipt,
    canonical_json_bytes,
)
from pydantic import ValidationError

_SAFE_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_ISSUE_MESSAGE_LENGTH = 500
_VALIDATOR = RunnerIdentity(name="managed_plan_validator", version="0.1.0")
_PASSED_CHECKS = (
    ValidationCheck(check_id="policy_binding"),
    ValidationCheck(check_id="component_identity"),
    ValidationCheck(check_id="lifecycle_opt_in"),
    ValidationCheck(check_id="required_questions"),
    ValidationCheck(check_id="managed_input_requirements"),
    ValidationCheck(check_id="component_input_schema"),
    ValidationCheck(check_id="component_constraints"),
)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_execution_policy(policy_id: str) -> ExecutionPolicy:
    """Load one packaged policy by safe stable ID and validate its closed schema."""

    if _SAFE_PROFILE_ID.fullmatch(policy_id) is None:
        raise ValueError("execution policy ID must be a safe stable ID")
    resource = resources.files("defined_quant.managed_profiles").joinpath(f"{policy_id}.json")
    try:
        text = resource.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"unknown execution policy: {policy_id}") from exc
    raw = json.loads(text, object_pairs_hook=_unique_json_object)
    policy = ExecutionPolicy.model_validate(raw)
    if policy.policy_id != policy_id:
        raise ValueError("packaged execution policy ID does not match its filename")
    return policy


def _issue(
    code: ValidationIssueCode,
    message: str,
    *,
    field: str | None = None,
) -> ValidationIssue:
    normalized = " ".join(message.split()) or "managed validation failed"
    if len(normalized) > _MAX_ISSUE_MESSAGE_LENGTH:
        normalized = normalized[: _MAX_ISSUE_MESSAGE_LENGTH - 3] + "..."
    return ValidationIssue(code=code, message=normalized, field=field)


def _blocked(
    plan: AnalysisPlan,
    code: ValidationIssueCode,
    message: str,
    *,
    field: str | None = None,
) -> ValidationFailure:
    return ValidationFailure(
        status="blocked",
        plan_hash=plan.plan_hash,
        issues=(_issue(code, message, field=field),),
    )


def _contract_question(raw: Mapping[str, Any]) -> ClarificationQuestion:
    question_id = raw.get("id")
    target_field = raw.get("resolves_to")
    prompt = raw.get("ask")
    materiality = raw.get("why")
    if not isinstance(question_id, str) or not question_id:
        raise ComponentContractError("required question has invalid ID")
    if not isinstance(target_field, str) or not target_field:
        raise ComponentContractError("required question has invalid target")
    if not isinstance(prompt, str) or not prompt:
        raise ComponentContractError("required question has invalid id, target, or prompt")
    if not isinstance(materiality, str) or not materiality:
        raise ComponentContractError("required question has invalid materiality explanation")
    return ClarificationQuestion(
        question_id=question_id,
        target_field=target_field,
        prompt=prompt,
        materiality=materiality,
    )


def _validate_required_questions(
    plan: AnalysisPlan,
    guidance: Mapping[str, Any],
) -> ValidationFailure | None:
    raw_questions = guidance.get("required_questions", [])
    if not isinstance(raw_questions, list):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component required_questions must be a list",
        )

    try:
        questions = tuple(
            _contract_question(raw)
            for raw in raw_questions
            if isinstance(raw, Mapping)
        )
    except (ComponentContractError, ValidationError):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component required-question metadata is invalid",
        )
    if len(questions) != len(raw_questions):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component required_questions contains a non-object entry",
        )

    required_pairs = {(item.question_id, item.target_field) for item in questions}
    supplied_pairs = {
        (item.question_id, item.target_field) for item in plan.resolved_questions
    }
    if not supplied_pairs.issubset(required_pairs):
        return _blocked(
            plan,
            ValidationIssueCode.PLAN_RESOLVED_QUESTION_MISMATCH,
            "plan contains a resolved question not declared by the component contract",
        )

    missing_issues: list[ValidationIssue] = []
    missing_questions: list[ClarificationQuestion] = []
    resolutions = {
        (item.question_id, item.target_field): item for item in plan.resolved_questions
    }
    for question in questions:
        resolution = resolutions.get((question.question_id, question.target_field))
        if (
            resolution is None
            or resolution.verification_status
            != QuestionVerificationStatus.CALLER_CONFIRMED
        ):
            missing_issues.append(
                _issue(
                    ValidationIssueCode.PLAN_REQUIRED_QUESTION_UNRESOLVED,
                    f"required question {question.question_id!r} needs caller confirmation",
                    field=question.target_field,
                )
            )
            missing_questions.append(question)
            continue
        if question.target_field not in plan.step.dataset.input:
            return _blocked(
                plan,
                ValidationIssueCode.PLAN_RESOLVED_QUESTION_MISMATCH,
                "resolved question target is absent from the bound dataset input",
                field=question.target_field,
            )
        if canonical_json_bytes(
            plan.step.dataset.input[question.target_field]
        ) != canonical_json_bytes(resolution.answer):
            return _blocked(
                plan,
                ValidationIssueCode.PLAN_RESOLVED_QUESTION_MISMATCH,
                "resolved question answer does not match the bound dataset input",
                field=question.target_field,
            )

    if missing_questions:
        return ValidationFailure(
            status="needs_information",
            plan_hash=plan.plan_hash,
            issues=tuple(missing_issues),
            questions=tuple(missing_questions),
        )
    return None


def _ambiguity_failure(
    plan: AnalysisPlan,
    error: AmbiguousInput,
) -> ValidationFailure:
    raw_questions = error.details.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component reported ambiguity without valid clarification questions",
        )
    try:
        questions = tuple(
            _contract_question(
                {
                    "id": raw.get("id"),
                    "resolves_to": raw.get("field"),
                    "ask": raw.get("ask"),
                    "why": raw.get("why"),
                }
            )
            for raw in raw_questions
            if isinstance(raw, Mapping)
        )
    except (ComponentContractError, ValidationError):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component ambiguity metadata is invalid",
        )
    if len(questions) != len(raw_questions):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component ambiguity contains a non-object clarification question",
        )
    return ValidationFailure(
        status="needs_information",
        plan_hash=plan.plan_hash,
        issues=tuple(
            _issue(
                ValidationIssueCode.PLAN_REQUIRED_QUESTION_UNRESOLVED,
                f"required question {question.question_id!r} needs caller confirmation",
                field=question.target_field,
            )
            for question in questions
        ),
        questions=questions,
    )


def _is_empty(value: Any) -> bool:
    return isinstance(value, (str, list, tuple, dict)) and len(value) == 0


def _validate_managed_requirements(
    plan: AnalysisPlan,
    rule: ManagedComponentRule,
) -> ValidationFailure | None:
    for requirement in rule.requirements:
        value = plan.step.dataset.input.get(requirement.field)
        missing = requirement.field not in plan.step.dataset.input or value is None
        if not missing and requirement.require_non_empty:
            missing = _is_empty(value)
        if missing:
            return _blocked(
                plan,
                ValidationIssueCode(requirement.failure_code.value),
                f"managed profile requires non-empty input field {requirement.field!r}",
                field=requirement.field,
            )
    return None


def _matching_rule(
    plan: AnalysisPlan,
    policy: ExecutionPolicy,
) -> ManagedComponentRule | ValidationFailure:
    profile_rules = tuple(
        rule for rule in policy.components if rule.profile_id == plan.step.profile_id
    )
    if not profile_rules:
        return _blocked(
            plan,
            ValidationIssueCode.EXECUTION_SCOPE_DENIED,
            "plan profile is not allowed by the execution policy",
            field="profile_id",
        )
    for rule in profile_rules:
        if rule.component == plan.step.component:
            return rule
    return _blocked(
        plan,
        ValidationIssueCode.PLAN_COMPONENT_IDENTITY_MISMATCH,
        "plan component identity is not the exact subject allowed by its profile",
        field="component",
    )


def validate_plan(
    plan: AnalysisPlan,
    policy: ExecutionPolicy,
) -> ValidationReceipt | ValidationFailure:
    """Validate one atomic plan without executing its component."""

    if plan.execution_policy_hash != policy.policy_hash:
        return _blocked(
            plan,
            ValidationIssueCode.PLAN_POLICY_MISMATCH,
            "plan does not bind the supplied execution policy",
            field="execution_policy_hash",
        )

    try:
        packaged_policy = load_execution_policy(policy.policy_id)
    except (ValueError, ValidationError):
        return _blocked(
            plan,
            ValidationIssueCode.EXECUTION_SCOPE_DENIED,
            "execution policy is not an installed packaged allowlist",
            field="execution_policy_hash",
        )
    if packaged_policy != policy:
        return _blocked(
            plan,
            ValidationIssueCode.EXECUTION_SCOPE_DENIED,
            "execution policy differs from the packaged allowlist",
            field="execution_policy_hash",
        )

    rule_or_failure = _matching_rule(plan, policy)
    if isinstance(rule_or_failure, ValidationFailure):
        return rule_or_failure
    rule = rule_or_failure

    try:
        record = component_record(plan.step.component.id)
        installed_ref_matches = (
            record.version == plan.step.component.version
            and subject_hash(record) == plan.step.component.subject_hash
        )
    except (ComponentContractError, ComponentLoadError, ComponentNotFound):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "installed component identity or contract could not be validated",
            field="component",
        )
    if not installed_ref_matches:
        return _blocked(
            plan,
            ValidationIssueCode.PLAN_COMPONENT_IDENTITY_MISMATCH,
            "installed component does not match the plan's exact version and subject",
            field="component",
        )

    raw_lifecycle = record.metadata.get("lifecycle")
    if not isinstance(raw_lifecycle, str):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "installed component has an invalid lifecycle",
            field="component_lifecycle",
        )
    try:
        installed_lifecycle = ComponentLifecycle(raw_lifecycle)
    except ValueError:
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "installed component has an invalid lifecycle",
            field="component_lifecycle",
        )
    if (
        plan.step.component_lifecycle != installed_lifecycle
        or installed_lifecycle != rule.allowed_lifecycle
        or (rule.require_explicit_opt_in and not plan.step.explicit_draft_opt_in)
    ):
        return _blocked(
            plan,
            ValidationIssueCode.PLAN_COMPONENT_LIFECYCLE_DENIED,
            "component lifecycle or required explicit opt-in is not authorized",
            field="component_lifecycle",
        )

    guidance = record.metadata.get("guidance")
    if not isinstance(guidance, Mapping):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component guidance must be an object",
        )
    question_failure = _validate_required_questions(plan, guidance)
    if question_failure is not None:
        return question_failure

    requirement_failure = _validate_managed_requirements(plan, rule)
    if requirement_failure is not None:
        return requirement_failure

    try:
        input_model, _ = component_models(record)
        validated_input = input_model.model_validate(plan.step.dataset.input)
    except ValidationError as exc:
        return _blocked(
            plan,
            ValidationIssueCode.INVALID_COMPONENT_INPUT,
            (
                "component input does not match its closed Pydantic schema "
                f"({exc.error_count()} validation error(s))"
            ),
            field="input",
        )
    except (ComponentContractError, ComponentLoadError, ComponentNotFound):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "installed component input model could not be validated",
            field="component",
        )

    try:
        preflight(
            plan.step.component.id,
            **validated_input.model_dump(mode="python"),
        )
    except AmbiguousInput as exc:
        return _ambiguity_failure(plan, exc)
    except ContractEvaluationError:
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component constraint contract could not be evaluated",
        )
    except DomainError as exc:
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONSTRAINT_VIOLATION,
            exc.message,
        )
    except (ComponentContractError, ComponentLoadError, ComponentNotFound):
        return _blocked(
            plan,
            ValidationIssueCode.COMPONENT_CONTRACT_ERROR,
            "component constraints could not be validated",
        )

    return ValidationReceipt(
        plan_hash=plan.plan_hash,
        policy_hash=policy.policy_hash,
        component=plan.step.component,
        dataset_hash=plan.step.dataset.dataset_hash,
        validator=_VALIDATOR,
        checks=_PASSED_CHECKS,
    )


def create_manual_approval(
    receipt: ValidationReceipt,
    *,
    approved_by: str,
    approved_at: datetime,
    note: str | None = None,
) -> ApprovalRecord:
    """Create a human approval bound to one successful validation receipt."""

    return ApprovalRecord(
        plan_hash=receipt.plan_hash,
        validation_receipt_hash=receipt.receipt_hash,
        approved_by=approved_by,
        approved_at=approved_at,
        note=note,
    )


def create_authorization_binding(
    plan: AnalysisPlan,
    receipt: ValidationReceipt,
    approval: ApprovalRecord,
) -> AuthorizationBinding:
    """Capture authorization roots after checking their immediate references."""

    if receipt.plan_hash != plan.plan_hash:
        raise ValueError("validation receipt does not bind the supplied plan")
    if approval.plan_hash != plan.plan_hash:
        raise ValueError("manual approval does not bind the supplied plan")
    if approval.validation_receipt_hash != receipt.receipt_hash:
        raise ValueError("manual approval does not bind the supplied validation receipt")
    return AuthorizationBinding(
        plan_hash=plan.plan_hash,
        validation_receipt_hash=receipt.receipt_hash,
        approval_hash=approval.approval_hash,
        component=plan.step.component,
        dataset_hash=plan.step.dataset.dataset_hash,
    )


def _authorization_failure(
    code: AuthorizationErrorCode,
    message: str,
) -> AuthorizationFailure:
    return AuthorizationFailure(code=code, message=message)


def verify_authorization(
    plan: AnalysisPlan,
    receipt: ValidationReceipt,
    approval: ApprovalRecord,
    binding: AuthorizationBinding,
    policy: ExecutionPolicy,
) -> AuthorizationOutcome:
    """Revalidate every authorization root before any later calculation may run."""

    if binding.component != plan.step.component or receipt.component != plan.step.component:
        return _authorization_failure(
            AuthorizationErrorCode.COMPONENT_BINDING_MISMATCH,
            "component identity changed after authorization",
        )
    try:
        current_dataset_hash = plan.step.dataset.dataset_hash
    except (TypeError, ValueError):
        return _authorization_failure(
            AuthorizationErrorCode.DATASET_BINDING_MISMATCH,
            "dataset content is no longer canonically hashable",
        )
    if binding.dataset_hash != current_dataset_hash or receipt.dataset_hash != current_dataset_hash:
        return _authorization_failure(
            AuthorizationErrorCode.DATASET_BINDING_MISMATCH,
            "dataset content or timestamps changed after authorization",
        )
    try:
        current_policy_hash = policy.policy_hash
    except (TypeError, ValueError):
        return _authorization_failure(
            AuthorizationErrorCode.POLICY_BINDING_MISMATCH,
            "execution policy is no longer canonically hashable",
        )
    if (
        plan.execution_policy_hash != current_policy_hash
        or receipt.policy_hash != current_policy_hash
    ):
        return _authorization_failure(
            AuthorizationErrorCode.POLICY_BINDING_MISMATCH,
            "execution policy changed after plan validation",
        )
    try:
        current_plan_hash = plan.plan_hash
    except (TypeError, ValueError):
        return _authorization_failure(
            AuthorizationErrorCode.PLAN_REVISION_STALE,
            "plan content is no longer canonically hashable",
        )
    if binding.plan_hash != current_plan_hash or receipt.plan_hash != current_plan_hash:
        return _authorization_failure(
            AuthorizationErrorCode.PLAN_REVISION_STALE,
            "plan revision changed after validation",
        )
    try:
        current_receipt_hash = receipt.receipt_hash
    except (TypeError, ValueError):
        return _authorization_failure(
            AuthorizationErrorCode.VALIDATION_RECEIPT_MISMATCH,
            "validation receipt is no longer canonically hashable",
        )
    if binding.validation_receipt_hash != current_receipt_hash:
        return _authorization_failure(
            AuthorizationErrorCode.VALIDATION_RECEIPT_MISMATCH,
            "validation receipt changed after authorization roots were captured",
        )

    current_receipt = validate_plan(plan, policy)
    if not isinstance(current_receipt, ValidationReceipt) or current_receipt != receipt:
        return _authorization_failure(
            AuthorizationErrorCode.VALIDATION_RECEIPT_MISMATCH,
            "validation receipt cannot be reproduced from the current plan and policy",
        )
    if (
        approval.plan_hash != current_plan_hash
        or approval.validation_receipt_hash != current_receipt_hash
    ):
        return _authorization_failure(
            AuthorizationErrorCode.APPROVAL_BINDING_MISMATCH,
            "manual approval does not bind the current plan and validation receipt",
        )
    try:
        current_approval_hash = approval.approval_hash
    except (TypeError, ValueError):
        return _authorization_failure(
            AuthorizationErrorCode.APPROVAL_BINDING_MISMATCH,
            "manual approval is no longer canonically hashable",
        )
    if binding.approval_hash != current_approval_hash:
        return _authorization_failure(
            AuthorizationErrorCode.APPROVAL_BINDING_MISMATCH,
            "manual approval changed after authorization roots were captured",
        )
    expected_binding = create_authorization_binding(plan, receipt, approval)
    if binding != expected_binding:
        return _authorization_failure(
            AuthorizationErrorCode.AUTHORIZATION_BINDING_MISMATCH,
            "authorization binding differs from the reproduced authorization roots",
        )
    return AuthorizationSuccess(binding=binding)


__all__ = [
    "create_authorization_binding",
    "create_manual_approval",
    "load_execution_policy",
    "validate_plan",
    "verify_authorization",
]
