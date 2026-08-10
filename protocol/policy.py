"""Closed, data-driven execution policy for managed plan validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .canonical import canonical_hash
from .operation import ComponentRef

_SAFE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
EXECUTION_POLICY_HASH_DOMAIN = "managed.execution_policy.v1"

SafeId: TypeAlias = Annotated[str, Field(pattern=_SAFE_ID_PATTERN)]


class ComponentLifecycle(StrEnum):
    """Lifecycle states authored by component contracts."""

    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class RequirementFailureCode(StrEnum):
    """Closed failure codes a declarative managed-input requirement may emit."""

    MANAGED_REQUIRED_INPUT_MISSING = "managed_required_input_missing"
    MANAGED_TIMESTAMP_REQUIRED = "managed_timestamp_required"


class InputRequirement(BaseModel):
    """One field-presence rule enforced by a managed profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: SafeId
    failure_code: RequirementFailureCode
    require_non_null: Literal[True] = True
    require_non_empty: bool = False


class ManagedComponentRule(BaseModel):
    """Exact component subject and outer-layer requirements allowed by one profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: SafeId
    component: ComponentRef
    allowed_lifecycle: ComponentLifecycle
    require_explicit_opt_in: bool
    requirements: tuple[InputRequirement, ...] = ()

    @model_validator(mode="after")
    def validate_requirements(self) -> ManagedComponentRule:
        fields = [requirement.field for requirement in self.requirements]
        if len(set(fields)) != len(fields):
            raise ValueError("managed component requirements must use unique fields")
        return self


class ExecutionPolicy(BaseModel):
    """Immutable allowlist consumed by catalog-wide managed validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policy_id: SafeId
    version: str = Field(pattern=_SEMVER_PATTERN)
    components: tuple[ManagedComponentRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_rules(self) -> ExecutionPolicy:
        profiles = [rule.profile_id for rule in self.components]
        if len(set(profiles)) != len(profiles):
            raise ValueError("execution policy profile IDs must be unique")
        return self

    @property
    def policy_hash(self) -> str:
        """Return the semantic digest of the complete policy allowlist."""

        return canonical_hash(
            self.model_dump(mode="json"),
            domain=EXECUTION_POLICY_HASH_DOMAIN,
        )


def execution_policy_hash(policy: ExecutionPolicy) -> str:
    """Compatibility function for explicit policy hashing."""

    return policy.policy_hash


__all__ = [
    "ComponentLifecycle",
    "EXECUTION_POLICY_HASH_DOMAIN",
    "ExecutionPolicy",
    "InputRequirement",
    "ManagedComponentRule",
    "RequirementFailureCode",
    "SafeId",
    "execution_policy_hash",
]
