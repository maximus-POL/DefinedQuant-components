"""Closed proposal, policy, availability, and compilation records.

The compiler consumes these records but does not execute adapters.  User preferences
are bounded identifiers and enums; executable code, access configuration, URLs, SQL,
and secrets cannot enter the proposal surface.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from ._immutable_json import freeze_json
from .canonical import canonical_hash, canonical_json_bytes
from .registry import (
    AdapterRef,
    BackendBinding,
    BackendKind,
    BackendRef,
    BackendRole,
    CapabilityRef,
    DataEgress,
    ImplementationRef,
    JsonObject,
    MethodRef,
    RecipeInputBinding,
    RecipeOutputBinding,
    RegistryId,
    RegistryText,
    RuntimeIdentity,
    RuntimeLocality,
    SafeId,
    SemVer,
    Sha256,
    TransportKind,
    TrustDimension,
)

RESOLUTION_CONSTRAINT_HASH_DOMAIN = "resolution.constraint"
ORIGIN_RECEIPT_HASH_DOMAIN = "resolution.origin_receipt"
RESOLUTION_POLICY_HASH_DOMAIN = "resolution.policy"
AVAILABILITY_SNAPSHOT_HASH_DOMAIN = "resolution.availability_snapshot"
PLAN_PROPOSAL_HASH_DOMAIN = "resolution.plan_proposal"
RESOLUTION_DECISION_HASH_DOMAIN = "resolution.decision"
COMPILED_PLAN_HASH_DOMAIN = "resolution.compiled_plan"
PLAN_RECORD_HASH_DOMAIN = "records.plan"

_REGISTRY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,7}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_FORBIDDEN_PROPOSAL_TOKENS = frozenset(
    {
        "api",
        "adapter",
        "backend",
        "code",
        "connection",
        "credential",
        "credentials",
        "endpoint",
        "import",
        "implementation",
        "fallback",
        "locality",
        "mcp",
        "network",
        "password",
        "python",
        "provider",
        "secret",
        "sql",
        "token",
        "transport",
        "url",
    }
)
_FORBIDDEN_STRING_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"(?:^|\s)(?:https?|ftp|file|jdbc|postgres(?:ql)?|mysql|mssql|mongodb|redis)://",
        r"^\s*(?:import\s+\S+|from\s+\S+\s+import\s+\S+|def\s+\w+\s*\(|class\s+\w+|lambda\b|(?:exec|eval)\s*\()",
        r"^\s*(?:select\b.+\bfrom\b|insert\s+into\b|update\b.+\bset\b|delete\s+from\b|drop\s+(?:table|database)\b|create\s+(?:table|database)\b|alter\s+table\b|merge\s+into\b)",
        r"(?:^|[;\s])(?:password|passwd|pwd|api[_-]?key|access[_-]?token|secret|connection[_-]?string)\s*[:=]",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"(?:^|\s)Bearer\s+[A-Za-z0-9._~+/-]+=*(?:\s|$)",
        r"\bAKIA[0-9A-Z]{16}\b",
    )
)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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


def _canonical_order(values: Sequence[Any], *, key: Any, name: str) -> None:
    if tuple(values) != tuple(sorted(values, key=key)):
        raise ValueError(f"{name} must use canonical order")


def _unique(values: Sequence[Any], *, key: Any, name: str) -> None:
    keys = tuple(key(value) for value in values)
    if len(set(keys)) != len(keys):
        raise ValueError(f"{name} must be unique")


def _semantic_dump(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _validate_proposal_json(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = key.casefold().replace("-", "_")
            tokens = frozenset(item for item in normalized.split("_") if item)
            if tokens & _FORBIDDEN_PROPOSAL_TOKENS:
                raise ValueError(
                    "proposal financial fields cannot carry executable or access control"
                )
            _validate_proposal_json(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_proposal_json(nested)
    elif isinstance(value, str) and any(
        pattern.search(value) is not None for pattern in _FORBIDDEN_STRING_PATTERNS
    ):
        raise ValueError("proposal cannot contain executable, connection, or secret material")


class PreferenceMode(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    FORBIDDEN = "forbidden"
    ALLOWED_SET = "allowed_set"
    AUTOMATIC = "automatic"


class PreferenceDimension(StrEnum):
    IMPLEMENTATION = "implementation"
    BACKEND = "backend"
    ADAPTER_FAMILY = "adapter_family"
    BACKEND_KIND = "backend_kind"
    TRANSPORT = "transport"
    LOCALITY = "locality"
    NETWORK = "network"


class FallbackBehavior(StrEnum):
    FORBIDDEN = "forbidden"
    WITHIN_PREFERENCES = "within_preferences"
    ANY_POLICY_ELIGIBLE = "any_policy_eligible"


class PreferenceOrigin(StrEnum):
    USER_EXPLICIT = "user_explicit"
    USER_PROFILE = "user_profile"
    HOST_POLICY = "host_policy"
    AUTOMATIC_RESOLUTION = "automatic_resolution"


class OriginVerification(StrEnum):
    USER_CONFIRMED = "user_confirmed"
    HOST_ATTESTED = "host_attested"


class ResolutionScope(_ClosedModel):
    all_steps: Literal[True] | None = None
    step_id: SafeId | None = None
    capability: CapabilityRef | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> ResolutionScope:
        if sum(item is not None for item in (self.all_steps, self.step_id, self.capability)) != 1:
            raise ValueError("resolution scope requires exactly one target")
        return self


class PreferenceOriginReceipt(_ClosedModel):
    schema_version: Literal[1] = 1
    asserted_origin: Literal[
        PreferenceOrigin.USER_EXPLICIT,
        PreferenceOrigin.USER_PROFILE,
    ]
    verified_origin: Literal[
        PreferenceOrigin.USER_EXPLICIT,
        PreferenceOrigin.USER_PROFILE,
    ]
    verification: OriginVerification
    constraint_hash: Sha256
    session_binding_hash: Sha256
    issuer: RuntimeIdentity
    issued_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_origin(self) -> PreferenceOriginReceipt:
        if self.asserted_origin != self.verified_origin:
            raise ValueError("verified origin cannot silently rewrite the asserted origin")
        if (
            self.verified_origin == PreferenceOrigin.USER_PROFILE
            and self.verification != OriginVerification.HOST_ATTESTED
        ):
            raise ValueError("user-profile origin requires a host attestation")
        return self

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=ORIGIN_RECEIPT_HASH_DOMAIN)


class OriginReceiptSet(_ClosedModel):
    """Host-recognized receipts supplied beside, never inside, an agent proposal."""

    session_binding_hash: Sha256
    receipts: tuple[PreferenceOriginReceipt, ...] = ()

    @model_validator(mode="after")
    def _validate_receipts(self) -> OriginReceiptSet:
        if any(
            item.session_binding_hash != self.session_binding_hash for item in self.receipts
        ):
            raise ValueError("origin receipts must bind the trusted host session")
        _unique(
            self.receipts,
            key=lambda item: item.constraint_hash,
            name="origin receipt constraints",
        )
        _canonical_order(
            self.receipts,
            key=lambda item: item.constraint_hash,
            name="origin receipts",
        )
        return self


class ResolutionConstraint(_ClosedModel):
    """One bounded preference over registry IDs or a closed operational vocabulary.

    An implementation target is either a stable registry ID or ``ID@semantic-version``; it is
    never a caller-supplied artifact coordinate. A required bare ID that matches more than one
    registered version needs confirmation. The compiler always records one exact
    ImplementationRef in the compiled decision.
    """

    constraint_id: SafeId
    scope: ResolutionScope
    dimension: PreferenceDimension
    backend_role: BackendRole | None = None
    mode: PreferenceMode
    targets: tuple[Annotated[str, Field(min_length=1, max_length=320)], ...] = ()
    asserted_origin: PreferenceOrigin
    fallback: FallbackBehavior = FallbackBehavior.FORBIDDEN
    origin_receipt: PreferenceOriginReceipt | None = None

    def semantic_projection(self) -> dict[str, Any]:
        value = _semantic_dump(self)
        value.pop("origin_receipt")
        return value

    @property
    def constraint_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=RESOLUTION_CONSTRAINT_HASH_DOMAIN)

    @model_validator(mode="after")
    def _validate_constraint(self) -> ResolutionConstraint:
        if self.dimension in {
            PreferenceDimension.BACKEND,
            PreferenceDimension.BACKEND_KIND,
            PreferenceDimension.TRANSPORT,
        }:
            if self.backend_role is None:
                raise ValueError("backend and transport constraints require a backend role")
        elif (
            self.dimension
            not in {PreferenceDimension.LOCALITY, PreferenceDimension.NETWORK}
            and self.backend_role is not None
        ):
            raise ValueError("backend role is not valid for this preference dimension")

        if self.mode == PreferenceMode.AUTOMATIC:
            if self.targets or self.fallback != FallbackBehavior.ANY_POLICY_ELIGIBLE:
                raise ValueError("automatic resolution requires no targets and policy fallback")
        else:
            if not self.targets:
                raise ValueError("non-automatic resolution constraint requires targets")
            if self.mode == PreferenceMode.REQUIRED and len(self.targets) != 1:
                raise ValueError("required resolution requires one exact target")
            if (
                self.mode != PreferenceMode.PREFERRED
                and self.fallback != FallbackBehavior.FORBIDDEN
            ):
                raise ValueError("fallback is meaningful only for a preferred constraint")

        if self.mode == PreferenceMode.PREFERRED:
            _unique(self.targets, key=lambda item: item, name="ranked preference targets")
        else:
            _unique(self.targets, key=lambda item: item, name="resolution targets")
            _canonical_order(
                self.targets,
                key=lambda item: item.encode("utf-8"),
                name="resolution targets",
            )

        if self.dimension == PreferenceDimension.IMPLEMENTATION:
            for target in self.targets:
                identifier, separator, version = target.partition("@")
                if _REGISTRY_ID_RE.fullmatch(identifier) is None or (
                    separator and _SEMVER_RE.fullmatch(version) is None
                ):
                    raise ValueError(
                        "implementation targets must be a registry ID or ID@semantic-version"
                    )
        elif self.dimension in {
            PreferenceDimension.BACKEND,
            PreferenceDimension.ADAPTER_FAMILY,
        }:
            if any(_REGISTRY_ID_RE.fullmatch(item) is None for item in self.targets):
                raise ValueError(
                    "backend and adapter targets must be registry IDs"
                )
        elif self.dimension == PreferenceDimension.BACKEND_KIND:
            if any(item not in {value.value for value in BackendKind} for item in self.targets):
                raise ValueError("backend-kind target is outside the closed vocabulary")
        elif self.dimension == PreferenceDimension.TRANSPORT:
            if any(item not in {value.value for value in TransportKind} for item in self.targets):
                raise ValueError("transport target is outside the closed vocabulary")
        elif self.dimension == PreferenceDimension.LOCALITY:
            if any(item not in {"local_only", "remote_allowed"} for item in self.targets):
                raise ValueError("locality target is outside the closed vocabulary")
        elif self.dimension == PreferenceDimension.NETWORK and any(
            item not in {"forbidden", "allowed"} for item in self.targets
        ):
            raise ValueError("network target is outside the closed vocabulary")

        receipt = self.origin_receipt
        if receipt is not None and (
            receipt.asserted_origin != self.asserted_origin
            or receipt.constraint_hash != self.constraint_hash
        ):
            raise ValueError("origin receipt must bind this exact asserted constraint")
        if self.asserted_origin in {
            PreferenceOrigin.HOST_POLICY,
            PreferenceOrigin.AUTOMATIC_RESOLUTION,
        } and receipt is not None:
            raise ValueError("host-policy and automatic origins do not use user-origin receipts")
        return self

    @property
    def origin_verified(self) -> bool:
        if self.asserted_origin in {
            PreferenceOrigin.HOST_POLICY,
            PreferenceOrigin.AUTOMATIC_RESOLUTION,
        }:
            return True
        return self.origin_receipt is not None


class ResolutionConstraintSet(_ClosedModel):
    constraints: tuple[ResolutionConstraint, ...] = ()

    @model_validator(mode="after")
    def _validate_constraints(self) -> ResolutionConstraintSet:
        _unique(
            self.constraints,
            key=lambda item: item.constraint_id,
            name="resolution constraints",
        )
        _canonical_order(
            self.constraints,
            key=lambda item: item.constraint_id.encode("utf-8"),
            name="resolution constraints",
        )
        return self


class PlanProposal(_ClosedModel):
    schema_version: Literal[2] = 2
    method_id: Annotated[str, Field(pattern=r"^dq(?:\.[a-z][a-z0-9_]*){2,5}$")]
    method_version: SemVer
    financial_inputs: JsonObject
    conventions: JsonObject
    resolution_constraints: ResolutionConstraintSet = ResolutionConstraintSet()
    agent_rationale: RegistryText | None = None

    @model_validator(mode="after")
    def _validate_proposal(self) -> PlanProposal:
        if set(self.financial_inputs) & set(self.conventions):
            raise ValueError("financial inputs and conventions must use distinct fields")
        for name, values in (
            ("financial inputs", self.financial_inputs),
            ("conventions", self.conventions),
        ):
            if any(re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", field) is None for field in values):
                raise ValueError(f"proposal {name} keys must be safe field IDs")
            canonical_json_bytes(values)
            _validate_proposal_json(values)
        if self.agent_rationale is not None:
            _validate_proposal_json(self.agent_rationale)
        if any(
            item.asserted_origin != PreferenceOrigin.USER_EXPLICIT
            for item in self.resolution_constraints.constraints
        ):
            raise ValueError("agent proposal constraints may assert only user_explicit origin")
        if any(
            item.mode == PreferenceMode.AUTOMATIC
            for item in self.resolution_constraints.constraints
        ):
            raise ValueError("automatic resolution is represented by omitted user constraints")
        if any(
            item.origin_receipt is not None
            for item in self.resolution_constraints.constraints
        ):
            raise ValueError(
                "origin receipts are accepted only through the host-trusted receipt set"
            )
        return self

    def semantic_projection(self) -> dict[str, Any]:
        value = _semantic_dump(self)
        value.pop("agent_rationale")
        return value

    @property
    def proposal_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=PLAN_PROPOSAL_HASH_DOMAIN)


class PolicyRef(_ClosedModel):
    id: SafeId
    version: SemVer
    policy_hash: Sha256


class PolicyImplementation(_ClosedModel):
    implementation: ImplementationRef
    priority: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class BackendRolePolicy(_ClosedModel):
    role: BackendRole
    allowed_backends: tuple[BackendRef, ...] = ()
    allowed_kinds: tuple[BackendKind, ...] = Field(min_length=1)
    allowed_transports: tuple[TransportKind, ...] = Field(min_length=1)
    allowed_localities: tuple[RuntimeLocality, ...] = Field(min_length=1)
    network_allowed: bool
    allowed_data_egress: tuple[DataEgress, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_role(self) -> BackendRolePolicy:
        _unique(self.allowed_backends, key=lambda item: item.spec_hash, name="allowed backends")
        _canonical_order(
            self.allowed_backends,
            key=lambda item: (item.id, item.version, item.spec_hash),
            name="allowed backends",
        )
        _unique(self.allowed_kinds, key=lambda item: item.value, name="allowed backend kinds")
        _canonical_order(
            self.allowed_kinds,
            key=lambda item: item.value,
            name="allowed backend kinds",
        )
        for name in (
            "allowed_transports",
            "allowed_localities",
            "allowed_data_egress",
        ):
            values = getattr(self, name)
            _unique(values, key=lambda item: item.value, name=name)
            _canonical_order(values, key=lambda item: item.value, name=name)
        return self


class CapabilityPolicyRule(_ClosedModel):
    capability: CapabilityRef
    implementations: tuple[PolicyImplementation, ...] = Field(min_length=1)
    backend_roles: tuple[BackendRolePolicy, ...] = Field(min_length=1)
    required_trust_dimensions: tuple[TrustDimension, ...] = ()

    @model_validator(mode="after")
    def _validate_rule(self) -> CapabilityPolicyRule:
        _unique(
            self.implementations,
            key=lambda item: item.implementation.spec_hash,
            name="policy implementations",
        )
        _canonical_order(
            self.implementations,
            key=lambda item: (
                item.priority,
                item.implementation.id,
                item.implementation.version,
                item.implementation.spec_hash,
            ),
            name="policy implementations",
        )
        for name in (
            "backend_roles",
            "required_trust_dimensions",
        ):
            values = getattr(self, name)
            _unique(
                values,
                key=lambda item: item.role if name == "backend_roles" else item.value,
                name=name,
            )
            _canonical_order(
                values,
                key=lambda item: (
                    item.role.value if name == "backend_roles" else item.value
                ),
                name=name,
            )
        if not any(item.role == BackendRole.RUNTIME for item in self.backend_roles):
            raise ValueError("capability policy requires a runtime backend rule")
        return self


class ResolutionPolicy(_ClosedModel):
    schema_version: Literal[2] = 2
    id: SafeId
    version: SemVer
    capability_rules: tuple[CapabilityPolicyRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_policy(self) -> ResolutionPolicy:
        _unique(
            self.capability_rules,
            key=lambda item: item.capability.contract_hash,
            name="policy capability rules",
        )
        _canonical_order(
            self.capability_rules,
            key=lambda item: (
                item.capability.id,
                item.capability.version,
                item.capability.contract_hash,
            ),
            name="policy capability rules",
        )
        return self

    @property
    def policy_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=RESOLUTION_POLICY_HASH_DOMAIN)

    @property
    def ref(self) -> PolicyRef:
        return PolicyRef(id=self.id, version=self.version, policy_hash=self.policy_hash)


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class InstallationStatus(StrEnum):
    INSTALLED = "installed"
    NOT_INSTALLED = "not_installed"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class RequirementStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    SATISFIED = "satisfied"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AvailabilityReason(StrEnum):
    READY = "ready"
    DISABLED = "disabled"
    NOT_INSTALLED = "not_installed"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CREDENTIAL_CAPABILITY_UNAVAILABLE = "credential_capability_unavailable"
    LICENCE_UNAVAILABLE = "licence_unavailable"
    ENTITLEMENT_UNAVAILABLE = "entitlement_unavailable"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    REACHABILITY_UNAVAILABLE = "reachability_unavailable"
    STATUS_UNKNOWN = "status_unknown"


class ImplementationAvailability(_ClosedModel):
    implementation: ImplementationRef
    adapter: AdapterRef
    backend_bindings: tuple[BackendBinding, ...] = Field(min_length=1)
    enabled: bool
    installation: InstallationStatus
    artifact: RequirementStatus
    dependencies: RequirementStatus
    credentials: RequirementStatus
    licence: RequirementStatus
    entitlement: RequirementStatus
    transport: RequirementStatus
    reachability: RequirementStatus
    status: AvailabilityStatus
    reasons: tuple[AvailabilityReason, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_availability(self) -> ImplementationAvailability:
        _unique(
            self.backend_bindings,
            key=lambda item: item.role,
            name="availability backend roles",
        )
        _canonical_order(
            self.backend_bindings,
            key=lambda item: item.role.value,
            name="availability backend roles",
        )
        backends = {item.role: item.backend for item in self.backend_bindings}
        if self.adapter.backend != backends.get(BackendRole.RUNTIME):
            raise ValueError("availability adapter must match the runtime backend")
        _unique(self.reasons, key=lambda item: item.value, name="availability reasons")
        _canonical_order(self.reasons, key=lambda item: item.value, name="availability reasons")

        hard_reasons: set[AvailabilityReason] = set()
        unknown = False
        if not self.enabled:
            hard_reasons.add(AvailabilityReason.DISABLED)
        if self.installation == InstallationStatus.NOT_INSTALLED:
            hard_reasons.add(AvailabilityReason.NOT_INSTALLED)
        elif self.installation == InstallationStatus.MISMATCH:
            hard_reasons.add(AvailabilityReason.ARTIFACT_MISMATCH)
        elif self.installation == InstallationStatus.UNKNOWN:
            unknown = True
        checks = (
            (self.artifact, AvailabilityReason.ARTIFACT_MISMATCH),
            (self.dependencies, AvailabilityReason.DEPENDENCY_UNAVAILABLE),
            (self.credentials, AvailabilityReason.CREDENTIAL_CAPABILITY_UNAVAILABLE),
            (self.licence, AvailabilityReason.LICENCE_UNAVAILABLE),
            (self.entitlement, AvailabilityReason.ENTITLEMENT_UNAVAILABLE),
            (self.transport, AvailabilityReason.TRANSPORT_UNAVAILABLE),
            (self.reachability, AvailabilityReason.REACHABILITY_UNAVAILABLE),
        )
        for state, reason in checks:
            if state == RequirementStatus.UNAVAILABLE:
                hard_reasons.add(reason)
            elif state == RequirementStatus.UNKNOWN:
                unknown = True

        if hard_reasons:
            expected_status = AvailabilityStatus.UNAVAILABLE
            expected_reasons = tuple(sorted(hard_reasons, key=lambda item: item.value))
        elif unknown:
            expected_status = AvailabilityStatus.UNKNOWN
            expected_reasons = (AvailabilityReason.STATUS_UNKNOWN,)
        else:
            expected_status = AvailabilityStatus.AVAILABLE
            expected_reasons = (AvailabilityReason.READY,)
        if self.status != expected_status or self.reasons != expected_reasons:
            raise ValueError("availability status and reasons must match the explicit checks")
        return self


class AvailabilitySnapshotRef(_ClosedModel):
    snapshot_id: SafeId
    snapshot_hash: Sha256


class AvailabilitySnapshot(_ClosedModel):
    schema_version: Literal[2] = 2
    snapshot_id: SafeId
    observed_at: AwareDatetime
    evaluator: RuntimeIdentity
    implementations: tuple[ImplementationAvailability, ...] = ()

    @model_validator(mode="after")
    def _validate_snapshot(self) -> AvailabilitySnapshot:
        _unique(
            self.implementations,
            key=lambda item: item.implementation.spec_hash,
            name="availability implementations",
        )
        _canonical_order(
            self.implementations,
            key=lambda item: (
                item.implementation.id,
                item.implementation.version,
                item.implementation.spec_hash,
            ),
            name="availability implementations",
        )
        return self

    @property
    def snapshot_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=AVAILABILITY_SNAPSHOT_HASH_DOMAIN)

    @property
    def ref(self) -> AvailabilitySnapshotRef:
        return AvailabilitySnapshotRef(
            snapshot_id=self.snapshot_id,
            snapshot_hash=self.snapshot_hash,
        )


class AppliedDefault(_ClosedModel):
    field: SafeId
    value: JsonValue

    @model_validator(mode="after")
    def _validate_value(self) -> AppliedDefault:
        canonical_json_bytes(self.value)
        return self


class WarningSourceKind(StrEnum):
    METHOD = "method"
    CAPABILITY = "capability"
    ADAPTER = "adapter"
    HOST = "host"


class WarningSource(_ClosedModel):
    """The exact contract or trusted boundary that emitted one warning."""

    kind: WarningSourceKind
    subject_id: RegistryId


class ValidationWarning(_ClosedModel):
    source: WarningSource
    code: SafeId
    field: SafeId | None = None
    message: RegistryText


class CandidateRejectionCode(StrEnum):
    CAPABILITY_MISMATCH = "capability_mismatch"
    USER_CONSTRAINT = "user_constraint"
    POLICY = "policy"
    TRUST = "trust"
    APPLICABILITY = "applicability"
    BACKEND_KIND = "backend_kind"
    ADAPTER_FAMILY = "adapter_family"
    TRANSPORT = "transport"
    LOCALITY = "locality"
    NETWORK = "network"
    DATA_HANDLING = "data_handling"
    UNAVAILABLE = "unavailable"


class CandidateDecision(_ClosedModel):
    implementation: ImplementationRef
    adapter: AdapterRef
    backend_bindings: tuple[BackendBinding, ...] = Field(min_length=1)
    priority: Annotated[int, Field(strict=True, ge=0, le=1_000_000)] | None = None
    preference_rank: Annotated[int, Field(strict=True, ge=0)] | None = None
    capability_match: bool
    user_constraints_satisfied: bool
    policy_allowed: bool
    trust_satisfied: bool
    satisfied_trust_dimensions: tuple[TrustDimension, ...] = ()
    missing_trust_dimensions: tuple[TrustDimension, ...] = ()
    applicability_satisfied: bool
    backend_kind_allowed: bool
    adapter_family_allowed: bool
    transport_allowed: bool
    locality_allowed: bool
    network_allowed: bool
    data_handling_allowed: bool
    availability: ImplementationAvailability
    rejection_reasons: tuple[CandidateRejectionCode, ...] = ()
    selected: bool

    @model_validator(mode="after")
    def _validate_candidate(self) -> CandidateDecision:
        _unique(
            self.backend_bindings,
            key=lambda item: item.role,
            name="candidate backend roles",
        )
        _canonical_order(
            self.backend_bindings,
            key=lambda item: item.role.value,
            name="candidate backend roles",
        )
        backends = {item.role: item.backend for item in self.backend_bindings}
        if self.adapter.backend != backends.get(BackendRole.RUNTIME):
            raise ValueError("candidate adapter must match the runtime backend")
        if (
            self.availability.implementation != self.implementation
            or self.availability.adapter != self.adapter
            or self.availability.backend_bindings != self.backend_bindings
        ):
            raise ValueError("candidate availability must bind the same exact realization")
        for name in ("satisfied_trust_dimensions", "missing_trust_dimensions"):
            values = getattr(self, name)
            _unique(values, key=lambda item: item.value, name=name)
            _canonical_order(values, key=lambda item: item.value, name=name)
        if set(self.satisfied_trust_dimensions) & set(self.missing_trust_dimensions):
            raise ValueError("trust dimensions cannot be both satisfied and missing")
        if self.trust_satisfied != (not self.missing_trust_dimensions):
            raise ValueError("trust eligibility must match the missing trust dimensions")
        expected: list[CandidateRejectionCode] = []
        facts = (
            (self.capability_match, CandidateRejectionCode.CAPABILITY_MISMATCH),
            (self.user_constraints_satisfied, CandidateRejectionCode.USER_CONSTRAINT),
            (self.policy_allowed, CandidateRejectionCode.POLICY),
            (self.trust_satisfied, CandidateRejectionCode.TRUST),
            (self.applicability_satisfied, CandidateRejectionCode.APPLICABILITY),
            (self.backend_kind_allowed, CandidateRejectionCode.BACKEND_KIND),
            (self.adapter_family_allowed, CandidateRejectionCode.ADAPTER_FAMILY),
            (self.transport_allowed, CandidateRejectionCode.TRANSPORT),
            (self.locality_allowed, CandidateRejectionCode.LOCALITY),
            (self.network_allowed, CandidateRejectionCode.NETWORK),
            (self.data_handling_allowed, CandidateRejectionCode.DATA_HANDLING),
        )
        expected.extend(reason for passed, reason in facts if not passed)
        if self.availability.status != AvailabilityStatus.AVAILABLE:
            expected.append(CandidateRejectionCode.UNAVAILABLE)
        expected_tuple = tuple(sorted(expected, key=lambda item: item.value))
        if self.rejection_reasons != expected_tuple:
            raise ValueError("candidate rejection reasons must exactly match eligibility facts")
        if (self.priority is not None) != self.policy_allowed:
            raise ValueError("candidate priority must occur exactly when admitted by policy")
        if self.selected and (self.rejection_reasons or self.priority is None):
            raise ValueError("selected candidate must be fully eligible and prioritized")
        return self


class ResolutionExplanationCode(StrEnum):
    AUTOMATIC_POLICY_PRIORITY = "automatic_policy_priority"
    REQUIRED_EXACT = "required_exact"
    PREFERRED_SELECTED = "preferred_selected"
    PREFERRED_FALLBACK = "preferred_fallback"
    ALLOWED_SET_SELECTED = "allowed_set_selected"


class ResolutionDecision(_ClosedModel):
    step_id: SafeId
    capability: CapabilityRef
    constraint_hashes: tuple[Sha256, ...] = ()
    candidates: tuple[CandidateDecision, ...] = Field(min_length=1)
    selected_implementation: ImplementationRef
    selected_adapter: AdapterRef
    selected_backend_bindings: tuple[BackendBinding, ...] = Field(min_length=1)
    selected_transport: TransportKind
    selected_locality: RuntimeLocality
    fallback_permitted: bool
    fallback_used: bool
    requested_targets_not_selected: tuple[RegistryId, ...] = ()
    explanation_code: ResolutionExplanationCode
    explanation: RegistryText
    runtime_fallback_allowed: Literal[False] = False

    @model_validator(mode="after")
    def _validate_decision(self) -> ResolutionDecision:
        _unique(self.constraint_hashes, key=lambda item: item, name="decision constraints")
        _canonical_order(self.constraint_hashes, key=lambda item: item, name="decision constraints")
        _unique(
            self.candidates,
            key=lambda item: item.implementation.spec_hash,
            name="resolution candidates",
        )
        _canonical_order(
            self.candidates,
            key=lambda item: (
                item.implementation.id,
                item.implementation.version,
                item.implementation.spec_hash,
            ),
            name="resolution candidates",
        )
        selected = tuple(item for item in self.candidates if item.selected)
        if len(selected) != 1:
            raise ValueError("resolution decision requires one exact selected candidate")
        chosen = selected[0]
        if (
            chosen.implementation != self.selected_implementation
            or chosen.adapter != self.selected_adapter
            or chosen.backend_bindings != self.selected_backend_bindings
        ):
            raise ValueError("selected identities must match the selected candidate")
        runtime = next(
            (
                item
                for item in self.selected_backend_bindings
                if item.role == BackendRole.RUNTIME
            ),
            None,
        )
        if (
            runtime is None
            or runtime.transport != self.selected_transport
            or runtime.locality != self.selected_locality
        ):
            raise ValueError("selected runtime transport and locality must match its binding")
        if self.fallback_used and not self.fallback_permitted:
            raise ValueError("fallback cannot be used without explicit permission")
        if self.fallback_used != bool(self.requested_targets_not_selected):
            raise ValueError("fallback disclosure must identify unselected requested targets")
        _unique(
            self.requested_targets_not_selected,
            key=lambda item: item,
            name="unselected requested targets",
        )
        return self

    @property
    def decision_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=RESOLUTION_DECISION_HASH_DOMAIN)


class CompiledStep(_ClosedModel):
    step_id: SafeId
    capability: CapabilityRef
    implementation: ImplementationRef
    adapter: AdapterRef
    backend_bindings: tuple[BackendBinding, ...] = Field(min_length=1)
    transport: TransportKind
    locality: RuntimeLocality
    depends_on: tuple[SafeId, ...] = ()
    input_bindings: tuple[RecipeInputBinding, ...] = ()
    resolution: ResolutionDecision

    @model_validator(mode="after")
    def _validate_step(self) -> CompiledStep:
        if (
            self.resolution.step_id != self.step_id
            or self.resolution.capability != self.capability
            or self.resolution.selected_implementation != self.implementation
            or self.resolution.selected_adapter != self.adapter
            or self.resolution.selected_backend_bindings != self.backend_bindings
            or self.resolution.selected_transport != self.transport
            or self.resolution.selected_locality != self.locality
        ):
            raise ValueError("compiled step identities must match its resolution decision")
        runtime = next(
            (item for item in self.backend_bindings if item.role == BackendRole.RUNTIME),
            None,
        )
        if (
            runtime is None
            or runtime.transport != self.transport
            or runtime.locality != self.locality
        ):
            raise ValueError("compiled runtime transport and locality must match its binding")
        if self.step_id in self.depends_on:
            raise ValueError("compiled step cannot depend on itself")
        _unique(self.depends_on, key=lambda item: item, name="compiled dependencies")
        _canonical_order(self.depends_on, key=lambda item: item, name="compiled dependencies")
        return self


class PlanRef(_ClosedModel):
    reference: Annotated[str, Field(pattern=r"^dqplan:[0-9a-f]{64}$")]

    @property
    def plan_hash(self) -> str:
        return self.reference.removeprefix("dqplan:")

    @classmethod
    def from_hash(cls, plan_hash: str) -> PlanRef:
        return cls(reference=f"dqplan:{plan_hash}")


class CompiledPlan(_ClosedModel):
    status: Literal["compiled"] = "compiled"
    schema_version: Literal[2] = 2
    algorithm: Literal["methods_first_resolution"] = "methods_first_resolution"
    method: MethodRef
    proposal_hash: Sha256
    policy: PolicyRef
    availability_snapshot: AvailabilitySnapshotRef
    original_user_constraints: ResolutionConstraintSet
    effective_constraints: ResolutionConstraintSet
    resolved_financial_inputs: JsonObject
    resolved_conventions: JsonObject
    applied_defaults: tuple[AppliedDefault, ...] = ()
    warnings: tuple[ValidationWarning, ...] = ()
    steps: tuple[CompiledStep, ...] = Field(min_length=1)
    result_bindings: tuple[RecipeOutputBinding, ...] = Field(min_length=1)
    runtime_fallback_allowed: Literal[False] = False

    @model_validator(mode="after")
    def _validate_plan(self) -> CompiledPlan:
        if set(self.resolved_financial_inputs) & set(self.resolved_conventions):
            raise ValueError("resolved inputs and conventions must use distinct fields")
        canonical_json_bytes(self.resolved_financial_inputs)
        canonical_json_bytes(self.resolved_conventions)
        if any(
            item.asserted_origin != PreferenceOrigin.USER_EXPLICIT
            or item.origin_receipt is not None
            or item.mode == PreferenceMode.AUTOMATIC
            for item in self.original_user_constraints.constraints
        ):
            raise ValueError("original user constraints must be raw user-explicit assertions")
        if any(
            not item.origin_verified for item in self.effective_constraints.constraints
        ):
            raise ValueError("every effective resolution constraint requires verified origin")
        original_hashes = {
            item.constraint_hash for item in self.original_user_constraints.constraints
        }
        effective_constraint_hashes = {
            item.constraint_hash for item in self.effective_constraints.constraints
        }
        if not original_hashes <= effective_constraint_hashes:
            raise ValueError("effective constraints must retain every original user constraint")
        _unique(self.applied_defaults, key=lambda item: item.field, name="applied defaults")
        _canonical_order(
            self.applied_defaults,
            key=lambda item: item.field,
            name="applied defaults",
        )
        resolved = dict(self.resolved_financial_inputs) | dict(self.resolved_conventions)
        for default in self.applied_defaults:
            if default.field not in resolved or canonical_json_bytes(
                default.value
            ) != canonical_json_bytes(resolved[default.field]):
                raise ValueError("applied default must match one resolved field")
        expected_warning_source = WarningSource(
            kind=WarningSourceKind.METHOD,
            subject_id=self.method.id,
        )
        if any(item.source != expected_warning_source for item in self.warnings):
            raise ValueError("plan warnings must identify the compiled method")
        _unique(
            self.warnings,
            key=lambda item: (item.source.kind, item.source.subject_id, item.code, item.field),
            name="plan warnings",
        )
        _canonical_order(
            self.warnings,
            key=lambda item: (
                item.source.kind.value,
                item.source.subject_id,
                item.code,
                item.field or "",
            ),
            name="plan warnings",
        )
        _unique(self.steps, key=lambda item: item.step_id, name="compiled step IDs")
        by_id = {item.step_id: item for item in self.steps}
        remaining = set(by_id)
        complete: set[str] = set()
        canonical: list[str] = []
        while remaining:
            ready = sorted(
                (item for item in remaining if set(by_id[item].depends_on) <= complete),
                key=lambda item: item.encode("utf-8"),
            )
            if not ready:
                raise ValueError("compiled plan must be an acyclic graph")
            for item in ready:
                remaining.remove(item)
                complete.add(item)
                canonical.append(item)
        if tuple(item.step_id for item in self.steps) != tuple(canonical):
            raise ValueError("compiled steps must use canonical topological order")
        if any(
            not set(step.resolution.constraint_hashes) <= effective_constraint_hashes
            for step in self.steps
        ):
            raise ValueError("step resolution cites an unknown effective constraint")
        return self

    def semantic_projection(self) -> dict[str, Any]:
        return _semantic_dump(self)

    @property
    def plan_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=COMPILED_PLAN_HASH_DOMAIN)

    @property
    def ref(self) -> PlanRef:
        return PlanRef.from_hash(self.plan_hash)


class PlanRecord(_ClosedModel):
    """One publish-once audit envelope around a semantic compiled-plan identity.

    ``dqplan`` identifies ``plan``. ``record_hash`` additionally binds compiler and
    publication time; an execution request carries both and cannot silently switch
    envelopes for the same plan.
    """

    record_kind: Literal["plan"] = "plan"
    schema_version: Literal[1] = 1
    plan: CompiledPlan
    compiler: RuntimeIdentity
    compiled_at: AwareDatetime

    @property
    def record_hash(self) -> str:
        return canonical_hash(_semantic_dump(self), domain=PLAN_RECORD_HASH_DOMAIN)

    @property
    def ref(self) -> PlanRef:
        return self.plan.ref


class ResolutionQuestionCode(StrEnum):
    REQUIRED_INPUT = "required_input"
    REQUIRED_CONVENTION = "required_convention"
    ORIGIN_CONFIRMATION = "origin_confirmation"
    FALLBACK_PERMISSION = "fallback_permission"
    IMPLEMENTATION_SELECTION = "implementation_selection"


class ResolutionQuestion(_ClosedModel):
    question_id: SafeId
    code: ResolutionQuestionCode
    field_path: Annotated[str, Field(min_length=1, max_length=320)]
    description: RegistryText
    allowed_values: tuple[JsonValue, ...] = ()

    @model_validator(mode="after")
    def _validate_values(self) -> ResolutionQuestion:
        encoded = tuple(canonical_json_bytes(value) for value in self.allowed_values)
        if len(encoded) != len(set(encoded)):
            raise ValueError("question allowed values must be unique")
        return self


class StepResolutionAttempt(_ClosedModel):
    step_id: SafeId
    capability: CapabilityRef
    constraint_hashes: tuple[Sha256, ...] = ()
    candidates: tuple[CandidateDecision, ...] = ()
    explanation: RegistryText

    @model_validator(mode="after")
    def _validate_attempt(self) -> StepResolutionAttempt:
        _unique(self.constraint_hashes, key=lambda item: item, name="attempt constraints")
        _canonical_order(self.constraint_hashes, key=lambda item: item, name="attempt constraints")
        _unique(
            self.candidates,
            key=lambda item: item.implementation.spec_hash,
            name="attempt candidates",
        )
        _canonical_order(
            self.candidates,
            key=lambda item: (
                item.implementation.id,
                item.implementation.version,
                item.implementation.spec_hash,
            ),
            name="attempt candidates",
        )
        if any(item.selected for item in self.candidates):
            raise ValueError("unresolved attempt cannot mark a candidate selected")
        return self


class NeedsInformation(_ClosedModel):
    status: Literal["needs_information"] = "needs_information"
    schema_version: Literal[2] = 2
    proposal: PlanProposal
    questions: tuple[ResolutionQuestion, ...] = Field(min_length=1)
    resolution_attempts: tuple[StepResolutionAttempt, ...] = ()

    @model_validator(mode="after")
    def _validate_questions(self) -> NeedsInformation:
        _unique(self.questions, key=lambda item: item.question_id, name="resolution questions")
        _canonical_order(
            self.questions,
            key=lambda item: (item.field_path, item.question_id),
            name="resolution questions",
        )
        _unique(self.resolution_attempts, key=lambda item: item.step_id, name="resolution attempts")
        _canonical_order(
            self.resolution_attempts,
            key=lambda item: item.step_id,
            name="resolution attempts",
        )
        return self


class PlanRefusalCode(StrEnum):
    METHOD_NOT_FOUND = "method_not_found"
    METHOD_IDENTITY_MISMATCH = "method_identity_mismatch"
    INVALID_INPUT = "invalid_input"
    INVALID_CONVENTION = "invalid_convention"
    INVALID_RESOLUTION_CONSTRAINT = "invalid_resolution_constraint"
    ORIGIN_UNVERIFIED = "origin_unverified"
    REQUIRED_CHOICE_NOT_REGISTERED = "required_choice_not_registered"
    REQUIRED_CHOICE_UNAVAILABLE = "required_choice_unavailable"
    FALLBACK_NOT_PERMITTED = "fallback_not_permitted"
    POLICY_REFUSED = "policy_refused"
    TRUST_REFUSED = "trust_refused"
    DATA_HANDLING_REFUSED = "data_handling_refused"
    NO_ELIGIBLE_IMPLEMENTATION = "no_eligible_implementation"
    INVALID_REGISTRY = "invalid_registry"


class PlanRefusal(_ClosedModel):
    status: Literal["refused"] = "refused"
    schema_version: Literal[2] = 2
    proposal: PlanProposal
    code: PlanRefusalCode
    message: RegistryText
    fields: tuple[SafeId, ...] = ()
    resolution_attempts: tuple[StepResolutionAttempt, ...] = ()

    @model_validator(mode="after")
    def _validate_refusal(self) -> PlanRefusal:
        _unique(self.fields, key=lambda item: item, name="refusal fields")
        _canonical_order(self.fields, key=lambda item: item, name="refusal fields")
        _unique(self.resolution_attempts, key=lambda item: item.step_id, name="resolution attempts")
        _canonical_order(
            self.resolution_attempts,
            key=lambda item: item.step_id,
            name="resolution attempts",
        )
        return self


CompilationOutcome: TypeAlias = Annotated[
    CompiledPlan | NeedsInformation | PlanRefusal,
    Field(discriminator="status"),
]


def resolution_constraint_hash(constraint: ResolutionConstraint) -> str:
    return constraint.constraint_hash


def origin_receipt_hash(receipt: PreferenceOriginReceipt) -> str:
    return receipt.receipt_hash


def resolution_policy_hash(policy: ResolutionPolicy) -> str:
    return policy.policy_hash


def availability_snapshot_hash(snapshot: AvailabilitySnapshot) -> str:
    return snapshot.snapshot_hash


def plan_proposal_hash(proposal: PlanProposal) -> str:
    return proposal.proposal_hash


def compiled_plan_hash(plan: CompiledPlan) -> str:
    return plan.plan_hash


def plan_record_hash(record: PlanRecord) -> str:
    return record.record_hash


__all__ = [
    "AVAILABILITY_SNAPSHOT_HASH_DOMAIN",
    "COMPILED_PLAN_HASH_DOMAIN",
    "ORIGIN_RECEIPT_HASH_DOMAIN",
    "PLAN_PROPOSAL_HASH_DOMAIN",
    "PLAN_RECORD_HASH_DOMAIN",
    "RESOLUTION_CONSTRAINT_HASH_DOMAIN",
    "RESOLUTION_DECISION_HASH_DOMAIN",
    "RESOLUTION_POLICY_HASH_DOMAIN",
    "AppliedDefault",
    "AvailabilityReason",
    "AvailabilitySnapshot",
    "AvailabilitySnapshotRef",
    "AvailabilityStatus",
    "BackendRolePolicy",
    "CandidateDecision",
    "CandidateRejectionCode",
    "CapabilityPolicyRule",
    "CompilationOutcome",
    "CompiledPlan",
    "CompiledStep",
    "FallbackBehavior",
    "ImplementationAvailability",
    "InstallationStatus",
    "NeedsInformation",
    "OriginVerification",
    "OriginReceiptSet",
    "PlanProposal",
    "PlanRecord",
    "PlanRef",
    "PlanRefusal",
    "PlanRefusalCode",
    "PolicyImplementation",
    "PolicyRef",
    "PreferenceDimension",
    "PreferenceMode",
    "PreferenceOrigin",
    "PreferenceOriginReceipt",
    "RequirementStatus",
    "ResolutionConstraint",
    "ResolutionConstraintSet",
    "ResolutionDecision",
    "ResolutionExplanationCode",
    "ResolutionPolicy",
    "ResolutionQuestion",
    "ResolutionQuestionCode",
    "ResolutionScope",
    "StepResolutionAttempt",
    "ValidationWarning",
    "WarningSource",
    "WarningSourceKind",
    "availability_snapshot_hash",
    "compiled_plan_hash",
    "origin_receipt_hash",
    "plan_proposal_hash",
    "plan_record_hash",
    "resolution_constraint_hash",
    "resolution_policy_hash",
]
