"""Atomic managed analysis-plan records and semantic bindings."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from .canonical import canonical_hash, canonical_json_bytes
from .operation import ComponentRef
from .policy import ComponentLifecycle, SafeId

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
DATASET_BINDING_HASH_DOMAIN = "managed.dataset_binding.v1"
ANALYSIS_PLAN_HASH_DOMAIN = "managed.analysis_plan.v1"

PlanText: TypeAlias = Annotated[str, Field(min_length=1, max_length=500)]


class QuestionVerificationStatus(StrEnum):
    """How an answer-changing resolved question was established."""

    UNVERIFIED = "unverified"
    CALLER_CONFIRMED = "caller_confirmed"


class ResolvedQuestion(BaseModel):
    """One execution-relevant answer embedded in a plan revision.

    ``resolved_at`` is operational audit metadata. It is deliberately excluded from
    ``AnalysisPlan.plan_hash`` so the same canonical answer has the same semantic identity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    question_id: SafeId
    target_field: SafeId
    answer: JsonValue
    supplied_by: str = Field(min_length=1, max_length=160)
    verification_status: QuestionVerificationStatus
    resolved_at: AwareDatetime

    @model_validator(mode="after")
    def validate_answer(self) -> ResolvedQuestion:
        canonical_json_bytes(self.answer)
        return self

    def semantic_projection(self) -> dict[str, Any]:
        """Return answer meaning without operational resolution time."""

        return {
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "target_field": self.target_field,
            "answer": self.answer,
            "supplied_by": self.supplied_by,
            "verification_status": self.verification_status.value,
        }


class DatasetBinding(BaseModel):
    """Caller-bound candidate component input, without a source-verification claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    dataset_id: SafeId
    verification_status: Literal["unverified"] = "unverified"
    input: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_canonical_input(self) -> DatasetBinding:
        canonical_json_bytes(self.input)
        return self

    @property
    def dataset_hash(self) -> str:
        """Bind the complete candidate input, including semantic data timestamps."""

        return canonical_hash(
            self.model_dump(mode="json"),
            domain=DATASET_BINDING_HASH_DOMAIN,
        )


class PlanStep(BaseModel):
    """The single component step supported by the atomic C3B profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    step_id: SafeId
    profile_id: SafeId
    component: ComponentRef
    component_lifecycle: ComponentLifecycle
    explicit_draft_opt_in: bool
    dataset: DatasetBinding


class AnalysisPlan(BaseModel):
    """One immutable atomic plan revision proposed for managed authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    protocol_version: Literal["0.2.0"] = "0.2.0"
    plan_id: SafeId
    revision: int = Field(ge=1)
    parent_plan_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    objective: PlanText
    resolved_questions: tuple[ResolvedQuestion, ...]
    step: PlanStep
    expected_outputs: tuple[PlanText, ...] = Field(min_length=1, max_length=50)
    stop_conditions: tuple[PlanText, ...] = Field(min_length=1, max_length=50)
    execution_policy_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_revision_and_questions(self) -> AnalysisPlan:
        if self.revision == 1 and self.parent_plan_hash is not None:
            raise ValueError("first plan revision cannot have a parent_plan_hash")
        if self.revision > 1 and self.parent_plan_hash is None:
            raise ValueError("later plan revisions require parent_plan_hash")
        question_ids = [question.question_id for question in self.resolved_questions]
        target_fields = [question.target_field for question in self.resolved_questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("resolved question IDs must be unique")
        if len(set(target_fields)) != len(target_fields):
            raise ValueError("resolved question target fields must be unique")
        return self

    def semantic_projection(self) -> dict[str, Any]:
        """Return exactly the execution meaning authorized by validation and approval."""

        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "plan_id": self.plan_id,
            "revision": self.revision,
            "parent_plan_hash": self.parent_plan_hash,
            "objective": self.objective,
            "resolved_questions": [
                question.semantic_projection() for question in self.resolved_questions
            ],
            "step": self.step.model_dump(mode="json"),
            "expected_outputs": list(self.expected_outputs),
            "stop_conditions": list(self.stop_conditions),
            "execution_policy_hash": self.execution_policy_hash,
        }

    @property
    def plan_hash(self) -> str:
        """Return the domain-separated semantic plan revision digest."""

        return canonical_hash(self.semantic_projection(), domain=ANALYSIS_PLAN_HASH_DOMAIN)


def analysis_plan_hash(plan: AnalysisPlan) -> str:
    """Compatibility function for explicit plan hashing."""

    return plan.plan_hash


def dataset_binding_hash(binding: DatasetBinding) -> str:
    """Compatibility function for explicit dataset-binding hashing."""

    return binding.dataset_hash


__all__ = [
    "AnalysisPlan",
    "ANALYSIS_PLAN_HASH_DOMAIN",
    "DATASET_BINDING_HASH_DOMAIN",
    "DatasetBinding",
    "PlanStep",
    "QuestionVerificationStatus",
    "ResolvedQuestion",
    "analysis_plan_hash",
    "dataset_binding_hash",
]
