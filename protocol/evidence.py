"""Closed methods-first evidence and capability-conformance records.

Evidence is data, not executable control.  These models deliberately enumerate every accepted
field while leaving only capability-typed fixture payloads dynamic.  The registry loader validates
those payloads against the exact referenced capability.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ._immutable_json import freeze_json
from .registry import (
    EvidenceSubject,
    EvidenceSubjectKind,
    MethodId,
    MethodLifecycle,
    RegistryId,
    SafeId,
    SemVer,
    Sha256,
)

EvidenceText: TypeAlias = Annotated[str, Field(min_length=1, max_length=4000)]
RelativeEvidencePath: TypeAlias = Annotated[str, Field(min_length=1, max_length=500)]
DistributionName: TypeAlias = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$"),
]

_SECRET_KEYS = frozenset(
    {
        "access_key",
        "api_key",
        "auth_header",
        "authorization",
        "bearer_token",
        "client_secret",
        "connection_string",
        "credential",
        "credentials",
        "database_password",
        "password",
        "passphrase",
        "private_key",
        "secret",
        "token",
    }
)
_SECRET_KEY_TOKENS = frozenset(
    {"credential", "credentials", "passphrase", "password", "secret", "token"}
)


def _validate_no_secret_fields(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            normalized = key.casefold().replace("-", "_")
            tokens = frozenset(item for item in normalized.split("_") if item)
            if normalized in _SECRET_KEYS or tokens & _SECRET_KEY_TOKENS:
                raise ValueError(f"{path} contains prohibited secret-bearing field {key!r}")
            _validate_no_secret_fields(nested, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _validate_no_secret_fields(nested, path=f"{path}[{index}]")


def _validate_relative_path(value: str, *, name: str) -> str:
    if "://" in value or "\\" in value or any(character in value for character in "\r\n\0"):
        raise ValueError(f"{name} must be a repository-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{name} must be a contained repository-relative POSIX path")
    return value


class _ClosedEvidenceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_secret_fields(cls, value: Any) -> Any:
        _validate_no_secret_fields(value)
        return value

    @model_validator(mode="after")
    def _freeze_nested_json(self) -> Self:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            frozen = freeze_json(value)
            if frozen is not value:
                object.__setattr__(self, field_name, frozen)
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        del deep
        values = self.model_dump(mode="python", exclude_unset=True)
        if update is not None:
            values.update(update)
        return type(self).model_validate(values)


class EvidenceStatus(_ClosedEvidenceModel):
    label: Literal["Experimental Technical Preview"]


class MethodEvidenceStatus(EvidenceStatus):
    lifecycle: MethodLifecycle


class CapabilityIdentity(_ClosedEvidenceModel):
    id: RegistryId
    version: SemVer


class CapabilityCaseReference(_ClosedEvidenceModel):
    capability: CapabilityIdentity
    case: SafeId


class AuthorDerivation(_ClosedEvidenceModel):
    id: SafeId
    kind: Literal["author_derivation"]
    description: EvidenceText
    reference: CapabilityCaseReference | None = None
    references: tuple[CapabilityCaseReference, ...] = ()

    @model_validator(mode="after")
    def _validate_references(self) -> AuthorDerivation:
        if (self.reference is None) == (not self.references):
            raise ValueError("author derivation requires exactly one reference form")
        if len(set(self.references)) != len(self.references):
            raise ValueError("author derivation references must be unique")
        return self


class AuthoredScopeReview(_ClosedEvidenceModel):
    id: SafeId
    kind: Literal["authored_scope_review"]
    description: EvidenceText


MethodEvidenceItem: TypeAlias = Annotated[
    AuthorDerivation | AuthoredScopeReview,
    Field(discriminator="kind"),
]


class MethodEvidenceReview(_ClosedEvidenceModel):
    engineering: Literal["author_reviewed"]
    domain: Literal["none"]
    independent_reproduction: Literal["none"]


class MethodEvidence(_ClosedEvidenceModel):
    schema_version: Literal[1] = 1
    id: RegistryId
    version: SemVer
    subject: EvidenceSubject
    status: MethodEvidenceStatus
    evidence: tuple[MethodEvidenceItem, ...] = Field(min_length=1)
    review: MethodEvidenceReview
    non_claims: tuple[EvidenceText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_record(self) -> MethodEvidence:
        if self.subject.kind != EvidenceSubjectKind.METHOD:
            raise ValueError("method evidence subject kind must be method")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("method evidence item IDs must be unique")
        if len(set(self.non_claims)) != len(self.non_claims):
            raise ValueError("method non-claims must be unique")
        return self


class ExecutableTestDefinition(_ClosedEvidenceModel):
    id: SafeId
    kind: Literal["executable_test_definition"]
    test: Annotated[str, Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _validate_test(self) -> ExecutableTestDefinition:
        path, separator, test_name = self.test.partition("::")
        _validate_relative_path(path, name="adapter evidence test")
        if not separator or not test_name or any(character in test_name for character in "\r\n\0"):
            raise ValueError("adapter evidence test must name one repository test node")
        return self


class AdapterEvidenceReview(_ClosedEvidenceModel):
    engineering: Literal["author_reviewed"]
    security: Literal["local_mapping_boundary_reviewed"]
    provider_authentication: Literal["not_applicable"]


class AdapterEvidence(_ClosedEvidenceModel):
    schema_version: Literal[1] = 1
    id: RegistryId
    version: SemVer
    subject: EvidenceSubject
    status: EvidenceStatus
    evidence: tuple[ExecutableTestDefinition, ...] = Field(min_length=1)
    review: AdapterEvidenceReview
    non_claims: tuple[EvidenceText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_record(self) -> AdapterEvidence:
        if self.subject.kind != EvidenceSubjectKind.ADAPTER:
            raise ValueError("adapter evidence subject kind must be adapter")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("adapter evidence item IDs must be unique")
        if len(set(self.non_claims)) != len(self.non_claims):
            raise ValueError("adapter non-claims must be unique")
        return self


class ArtifactEvidenceHash(_ClosedEvidenceModel):
    algorithm: Literal["sha256"]
    canonicalization: Literal["defined_quant_canonical_json_v1"]
    domain: Literal["registry.artifact.installed_distribution_manifest"]
    value: Sha256


class InstalledDistributionEvidence(_ClosedEvidenceModel):
    kind: Literal["installed_distribution_manifest"]
    distribution: DistributionName
    version: SemVer
    manifest: RelativeEvidencePath
    hash: ArtifactEvidenceHash

    @model_validator(mode="after")
    def _validate_manifest(self) -> InstalledDistributionEvidence:
        _validate_relative_path(self.manifest, name="artifact evidence manifest")
        return self


class ImplementationConformance(_ClosedEvidenceModel):
    capability: CapabilityIdentity
    suite: RelativeEvidencePath
    tests: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def _validate_paths(self) -> ImplementationConformance:
        _validate_relative_path(self.suite, name="implementation conformance suite")
        for test in self.tests:
            path, separator, test_name = test.partition("::")
            _validate_relative_path(path, name="implementation conformance test")
            if not separator or not test_name:
                raise ValueError("implementation conformance tests must name test nodes")
        if len(set(self.tests)) != len(self.tests):
            raise ValueError("implementation conformance tests must be unique")
        return self


class SourceAndTestReview(_ClosedEvidenceModel):
    id: SafeId
    kind: Literal["source_and_test_review"]
    description: EvidenceText
    operation_order: SafeId | None = None


class LegacyComponentEvidence(_ClosedEvidenceModel):
    id: MethodId
    version: SemVer
    subject_hash: Sha256
    meaning: EvidenceText


class ImplementationMigration(_ClosedEvidenceModel):
    legacy_component: LegacyComponentEvidence


class ImplementationEvidenceReview(_ClosedEvidenceModel):
    engineering: Literal["author_reviewed"]
    capability_conformance: Literal["author_asserted"]
    domain: Literal["none"]
    independent_reproduction: Literal["none"]


class ImplementationEvidence(_ClosedEvidenceModel):
    schema_version: Literal[1] = 1
    id: RegistryId
    version: SemVer
    subject: EvidenceSubject
    status: EvidenceStatus
    artifact: InstalledDistributionEvidence
    conformance: ImplementationConformance
    evidence: tuple[SourceAndTestReview, ...] = Field(min_length=1)
    migration: ImplementationMigration
    review: ImplementationEvidenceReview
    non_claims: tuple[EvidenceText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_record(self) -> ImplementationEvidence:
        if self.subject.kind != EvidenceSubjectKind.IMPLEMENTATION:
            raise ValueError("implementation evidence subject kind must be implementation")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("implementation evidence item IDs must be unique")
        if len(set(self.non_claims)) != len(self.non_claims):
            raise ValueError("implementation non-claims must be unique")
        return self


class NumericModel(_ClosedEvidenceModel):
    input: SafeId
    output: SafeId


class FixtureEncoding(_ClosedEvidenceModel):
    binary64: EvidenceText


class NumericalTolerance(_ClosedEvidenceModel):
    relative: float
    absolute: float

    @model_validator(mode="after")
    def _validate_tolerance(self) -> NumericalTolerance:
        if (
            not math.isfinite(self.relative)
            or not math.isfinite(self.absolute)
            or self.relative < 0.0
            or self.absolute < 0.0
        ):
            raise ValueError("conformance tolerances must be finite and non-negative")
        return self


class ConformanceSuccess(_ClosedEvidenceModel):
    outcome: Literal["success"]
    output: dict[str, JsonValue]
    tolerance: NumericalTolerance


class ConformanceFailure(_ClosedEvidenceModel):
    outcome: Literal["failure"]
    code: SafeId


ConformanceExpectation: TypeAlias = Annotated[
    ConformanceSuccess | ConformanceFailure,
    Field(discriminator="outcome"),
]


class ConformanceCase(_ClosedEvidenceModel):
    id: SafeId
    kind: Literal["known_answer", "boundary", "numerical_boundary", "invalid_input"]
    description: EvidenceText | None = None
    input: dict[str, JsonValue]
    expected: ConformanceExpectation


class ConformanceInvariant(_ClosedEvidenceModel):
    id: SafeId
    statement: EvidenceText


class CapabilityConformance(_ClosedEvidenceModel):
    schema_version: Literal[1] = 1
    capability: CapabilityIdentity
    numeric_model: NumericModel | None = None
    fixture_encoding: FixtureEncoding | None = None
    cases: tuple[ConformanceCase, ...] = Field(min_length=1)
    invariants: tuple[ConformanceInvariant, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_record(self) -> CapabilityConformance:
        if len({item.id for item in self.cases}) != len(self.cases):
            raise ValueError("conformance case IDs must be unique")
        if len({item.id for item in self.invariants}) != len(self.invariants):
            raise ValueError("conformance invariant IDs must be unique")
        return self


EvidenceSpec: TypeAlias = MethodEvidence | AdapterEvidence | ImplementationEvidence


__all__ = [
    "AdapterEvidence",
    "CapabilityConformance",
    "CapabilityIdentity",
    "EvidenceSpec",
    "ImplementationEvidence",
    "MethodEvidence",
]
