"""Canonical methods-first registry records.

The records in this module are independent of the legacy component projection in
``governance.py``.  They describe authored financial contracts and exact backend,
adapter, and implementation identities, but they never import or execute adapter code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_serializer, model_validator

from ._immutable_json import freeze_json
from .canonical import canonical_hash, canonical_json_bytes
from .ports import PORT_SCHEMA_KEY, PortDirection, SemanticPort

_SAFE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_REGISTRY_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,7}$"
_METHOD_ID_PATTERN = r"^dq(?:\.[a-z][a-z0-9_]*){2,5}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_DISTRIBUTION_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$"
_WEBSITE_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

METHOD_CONTRACT_HASH_DOMAIN = "registry.method.contract"
METHOD_RECORD_HASH_DOMAIN = "registry.method.record"
CAPABILITY_CONTRACT_HASH_DOMAIN = "registry.capability.contract"
CAPABILITY_RECORD_HASH_DOMAIN = "registry.capability.record"
BACKEND_SPEC_HASH_DOMAIN = "registry.backend.spec"
ADAPTER_SPEC_HASH_DOMAIN = "registry.adapter.spec"
IMPLEMENTATION_SPEC_HASH_DOMAIN = "registry.implementation.spec"
EVIDENCE_REF_HASH_DOMAIN = "registry.evidence.ref"

_FORBIDDEN_CONTROL_TOKENS = frozenset(
    {
        "api",
        "backend",
        "code",
        "connection",
        "credential",
        "credentials",
        "endpoint",
        "implementation",
        "import",
        "mcp",
        "password",
        "provider",
        "python",
        "secret",
        "sql",
        "token",
        "url",
    }
)

SafeId: TypeAlias = Annotated[str, Field(pattern=_SAFE_ID_PATTERN)]
RegistryId: TypeAlias = Annotated[str, Field(max_length=320, pattern=_REGISTRY_ID_PATTERN)]
MethodId: TypeAlias = Annotated[str, Field(max_length=320, pattern=_METHOD_ID_PATTERN)]
SemVer: TypeAlias = Annotated[str, Field(max_length=128, pattern=_SEMVER_PATTERN)]
Sha256: TypeAlias = Annotated[str, Field(pattern=_SHA256_PATTERN)]
RegistryText: TypeAlias = Annotated[str, Field(min_length=1, max_length=4000)]
JsonObject: TypeAlias = dict[str, JsonValue]


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


def _control_key_is_forbidden(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    tokens = frozenset(token for token in normalized.split("_") if token)
    return bool(tokens & _FORBIDDEN_CONTROL_TOKENS)


def _validate_schema_controls(value: Any, *, name: str) -> None:
    if not isinstance(value, Mapping):
        return
    reference = value.get("$ref")
    if reference is not None and (
        not isinstance(reference, str) or not reference.startswith("#/$defs/")
    ):
        raise ValueError(f"{name} permits local $defs references only")
    properties = value.get("properties")
    if isinstance(properties, Mapping):
        forbidden = tuple(sorted(key for key in properties if _control_key_is_forbidden(key)))
        if forbidden:
            raise ValueError(f"{name} contains forbidden control-plane fields: {forbidden}")
    for key in ("properties", "$defs"):
        nested_values = value.get(key)
        if isinstance(nested_values, Mapping):
            for nested in nested_values.values():
                _validate_schema_controls(nested, name=name)
    items = value.get("items")
    if isinstance(items, Mapping):
        _validate_schema_controls(items, name=name)
    for branch_name in ("anyOf", "allOf", "oneOf"):
        branches = value.get(branch_name)
        if isinstance(branches, Sequence) and not isinstance(branches, str | bytes):
            for nested in branches:
                _validate_schema_controls(nested, name=name)


def _object_schema(value: JsonObject, *, name: str) -> Mapping[str, Any]:
    canonical_json_bytes(value)
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ValueError(f"{name} must be a closed object JSON Schema")
    properties = value.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError(f"{name} must declare object properties")
    if any(
        not isinstance(field, str) or re.fullmatch(_SAFE_ID_PATTERN, field) is None
        for field in properties
    ):
        raise ValueError(f"{name} property names must be safe field IDs")
    required = value.get("required", [])
    if not isinstance(required, list) or any(not isinstance(field, str) for field in required):
        raise ValueError(f"{name} required fields are malformed")
    if len(set(required)) != len(required) or any(field not in properties for field in required):
        raise ValueError(f"{name} required fields must be unique declared properties")
    _validate_schema_controls(value, name=name)
    return properties


def schema_ports(
    schema: Mapping[str, Any],
    *,
    direction: PortDirection,
) -> tuple[tuple[str, SemanticPort], ...]:
    """Derive the one authoritative semantic-port index from a field schema."""

    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError("schema must contain object properties")
    result: list[tuple[str, SemanticPort]] = []
    for field, field_schema in properties.items():
        if not isinstance(field, str) or not isinstance(field_schema, Mapping):
            continue
        raw_port = field_schema.get(PORT_SCHEMA_KEY)
        if raw_port is None:
            continue
        port = SemanticPort.model_validate(raw_port)
        if port.direction != direction:
            raise ValueError("semantic port direction contradicts its containing schema")
        result.append((field, port))
    return tuple(sorted(result, key=lambda item: item[0].encode("utf-8")))


class BackendKind(StrEnum):
    DQ_NATIVE = "dq_native"
    PYTHON_LIBRARY = "python_library"
    NATIVE_LIBRARY = "native_library"
    DATA_PROVIDER = "data_provider"
    ANALYTICS_SERVICE = "analytics_service"
    HTTP_API = "http_api"
    DATABASE = "database"
    EXTERNAL_MCP = "external_mcp"


class TransportKind(StrEnum):
    IN_PROCESS = "in_process"
    SUBPROCESS = "subprocess"
    NATIVE_FFI = "native_ffi"
    HTTP = "http"
    SQL = "sql"
    MCP = "mcp"


class RuntimeLocality(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"


class MethodLifecycle(StrEnum):
    DRAFT = "draft"
    EXPERIMENTAL = "experimental"
    STABLE = "stable"
    DEPRECATED = "deprecated"


class CapabilityKind(StrEnum):
    ACQUISITION = "acquisition"
    TRANSFORMATION = "transformation"
    CALCULATION = "calculation"
    DIAGNOSTIC = "diagnostic"
    VERIFICATION = "verification"


class ConstraintSeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


class CredentialRequirement(StrEnum):
    NONE = "none"
    HOST_CAPABILITY = "host_capability"


class EntitlementRequirement(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    INSTALLATION_DEPENDENT = "installation_dependent"


class LicenseKind(StrEnum):
    OPEN_SOURCE = "open_source"
    COMMERCIAL = "commercial"
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"


class DataEgress(StrEnum):
    NONE = "none"
    LOCAL_PROCESS = "local_process"
    REMOTE_SERVICE = "remote_service"


class BackendRole(StrEnum):
    RUNTIME = "runtime"
    DATA_PROVIDER = "data_provider"
    ANALYTICS_SERVICE = "analytics_service"
    DATABASE = "database"


class SupportLevel(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class TrustDimension(StrEnum):
    ADAPTER_REVIEWED = "adapter_reviewed"
    ARTIFACT_PINNED = "artifact_pinned"
    DEPENDENCY_LOCKED = "dependency_locked"
    SCHEMA_CHECKED = "schema_checked"
    DETERMINISTIC = "deterministic"
    UNIT_TESTED = "unit_tested"
    CONFORMANCE_TESTED = "conformance_tested"
    DIFFERENTIAL_TESTED = "differential_tested"
    SANDBOXED = "sandboxed"
    DOMAIN_REVIEWED = "domain_reviewed"
    INDEPENDENTLY_REPRODUCED = "independently_reproduced"


class MethodRef(_ClosedModel):
    id: MethodId
    version: SemVer
    contract_hash: Sha256


class CapabilityRef(_ClosedModel):
    id: RegistryId
    version: SemVer
    contract_hash: Sha256


class BackendRef(_ClosedModel):
    id: RegistryId
    version: SemVer
    spec_hash: Sha256


class AdapterRef(_ClosedModel):
    id: RegistryId
    version: SemVer
    spec_hash: Sha256
    backend: BackendRef


class ImplementationRef(_ClosedModel):
    id: RegistryId
    version: SemVer
    spec_hash: Sha256


class EvidenceSubjectKind(StrEnum):
    METHOD = "method"
    CAPABILITY = "capability"
    BACKEND = "backend"
    ADAPTER = "adapter"
    IMPLEMENTATION = "implementation"


class EvidenceSubject(_ClosedModel):
    """The exact registry entity to which one evidence record applies."""

    kind: EvidenceSubjectKind
    id: RegistryId
    version: SemVer


class EvidenceRef(_ClosedModel):
    id: RegistryId
    version: SemVer
    evidence_hash: Sha256
    subject: EvidenceSubject

    @property
    def ref_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), domain=EVIDENCE_REF_HASH_DOMAIN)


class TrustAssertion(_ClosedModel):
    """An evidence-backed registry claim, not a host trust or runtime attestation."""

    dimension: TrustDimension
    evidence: EvidenceRef


class RuntimeIdentity(_ClosedModel):
    name: SafeId
    version: SemVer
    artifact_hash: Sha256


class ArtifactPin(_ClosedModel):
    distribution: str = Field(pattern=_DISTRIBUTION_PATTERN)
    version: SemVer
    artifact_hash: Sha256


class DiscoveryMetadata(_ClosedModel):
    slug: SafeId
    website_slug: Annotated[str, Field(max_length=160, pattern=_WEBSITE_SLUG_PATTERN)]
    aliases: tuple[Annotated[str, Field(min_length=1, max_length=160)], ...] = ()
    intents: tuple[SafeId, ...] = ()
    input_concepts: tuple[SafeId, ...] = ()
    output_concepts: tuple[SafeId, ...] = ()
    tags: tuple[SafeId, ...] = ()
    use_when: tuple[RegistryText, ...] = ()
    do_not_use_when: tuple[RegistryText, ...] = ()
    unsupported_scope: tuple[RegistryText, ...] = ()

    @model_validator(mode="after")
    def _validate_values(self) -> DiscoveryMetadata:
        for name in (
            "aliases",
            "intents",
            "input_concepts",
            "output_concepts",
            "tags",
            "use_when",
            "do_not_use_when",
            "unsupported_scope",
        ):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"discovery {name} must be unique")
        for name in ("intents", "input_concepts", "output_concepts", "tags"):
            _canonical_order(getattr(self, name), key=lambda item: item.encode("utf-8"), name=name)
        return self


def _validate_constraint_expression(value: Any, *, depth: int = 0) -> None:
    if depth > 3 or not isinstance(value, Mapping):
        raise ValueError("constraint expression is outside the closed vocabulary")
    if set(value) in ({"all"}, {"any"}):
        branch = value["all"] if "all" in value else value["any"]
        if (
            not isinstance(branch, Sequence)
            or isinstance(branch, str | bytes)
            or not branch
        ):
            raise ValueError("constraint boolean branch must be non-empty")
        for child in branch:
            _validate_constraint_expression(child, depth=depth + 1)
        return
    if set(value) != {"left", "op", "right"}:
        raise ValueError("constraint expression uses an unsupported shape")
    if value["op"] not in {"lt", "le", "gt", "ge", "eq", "ne", "in_set", "not_in_set"}:
        raise ValueError("constraint expression uses an unsupported operator")
    for operand in (value["left"], value["right"]):
        if not isinstance(operand, Mapping) or not set(operand) <= {"field", "value", "measure"}:
            raise ValueError("constraint operand uses an unsupported shape")
        if ("field" in operand) == ("value" in operand):
            raise ValueError("constraint operand requires exactly one field or value")
        if "measure" in operand and "field" not in operand:
            raise ValueError("constraint measures require a field operand")
        if "field" in operand and (
            not isinstance(operand["field"], str)
            or re.fullmatch(_SAFE_ID_PATTERN, operand["field"]) is None
        ):
            raise ValueError("constraint field is not a safe field ID")
        if "measure" in operand and operand["measure"] not in {
            "identity",
            "count",
            "interval_count",
            "stdev",
            "all_finite",
            "all_valid_calendar_month_labels",
            "min",
            "max",
            "has_duplicates",
            "is_consecutive_calendar_months",
            "is_strictly_increasing",
        }:
            raise ValueError("constraint measure is outside the closed vocabulary")
        canonical_json_bytes(dict(operand))


def _constraint_fields(value: Mapping[str, Any]) -> set[str]:
    if "all" in value or "any" in value:
        branch = value.get("all", value.get("any", ()))
        assert isinstance(branch, Sequence)
        return set().union(
            *(_constraint_fields(child) for child in branch if isinstance(child, Mapping))
        )
    fields: set[str] = set()
    for name in ("left", "right"):
        operand = value.get(name)
        if isinstance(operand, Mapping) and isinstance(operand.get("field"), str):
            fields.add(operand["field"])
    return fields


class ConstraintSpec(_ClosedModel):
    id: SafeId
    target: PortDirection
    severity: ConstraintSeverity
    expression: JsonObject
    message: RegistryText

    @model_validator(mode="after")
    def _validate_expression(self) -> ConstraintSpec:
        _validate_constraint_expression(self.expression)
        return self


class ConventionSpec(_ClosedModel):
    id: SafeId
    field: SafeId
    question: RegistryText
    materiality: RegistryText
    required_from_user: bool
    allowed_values: tuple[JsonValue, ...] = ()

    @model_validator(mode="after")
    def _validate_allowed_values(self) -> ConventionSpec:
        encoded = tuple(canonical_json_bytes(value) for value in self.allowed_values)
        if len(set(encoded)) != len(encoded):
            raise ValueError("convention allowed values must be unique")
        return self


class DefaultSpec(_ClosedModel):
    field: SafeId
    value: JsonValue
    rationale: RegistryText

    @model_validator(mode="after")
    def _validate_value(self) -> DefaultSpec:
        canonical_json_bytes(self.value)
        return self


class RecipeValueSource(_ClosedModel):
    kind: Literal["method_input", "step_output", "authored_constant"]
    field: SafeId | None = None
    step_id: SafeId | None = None
    value: JsonValue | None = None

    @model_validator(mode="after")
    def _validate_source(self) -> RecipeValueSource:
        has_value = "value" in self.model_fields_set
        if self.kind == "method_input":
            valid = self.field is not None and self.step_id is None and not has_value
        elif self.kind == "step_output":
            valid = self.field is not None and self.step_id is not None and not has_value
        else:
            valid = self.field is None and self.step_id is None and has_value
        if not valid:
            raise ValueError("recipe value source fields contradict its kind")
        if has_value:
            canonical_json_bytes(self.value)
        return self

    @model_serializer
    def _serialize_source(self) -> dict[str, JsonValue]:
        if self.kind == "method_input":
            assert self.field is not None
            return {"kind": self.kind, "field": self.field}
        if self.kind == "step_output":
            assert self.field is not None and self.step_id is not None
            return {"kind": self.kind, "step_id": self.step_id, "field": self.field}
        return {"kind": self.kind, "value": self.value}


class RecipeInputBinding(_ClosedModel):
    target_field: SafeId
    source: RecipeValueSource


class RecipeStep(_ClosedModel):
    step_id: SafeId
    capability: CapabilityRef
    depends_on: tuple[SafeId, ...] = ()
    input_bindings: tuple[RecipeInputBinding, ...] = ()

    @model_validator(mode="after")
    def _validate_step(self) -> RecipeStep:
        if self.step_id in self.depends_on:
            raise ValueError("recipe step cannot depend on itself")
        _unique(self.depends_on, key=lambda item: item, name="recipe dependencies")
        _canonical_order(
            self.depends_on, key=lambda item: item.encode("utf-8"), name="recipe dependencies"
        )
        _unique(
            self.input_bindings,
            key=lambda item: item.target_field,
            name="recipe input bindings",
        )
        _canonical_order(
            self.input_bindings,
            key=lambda item: item.target_field.encode("utf-8"),
            name="recipe input bindings",
        )
        return self


class RecipeOutputBinding(_ClosedModel):
    target_field: SafeId
    source: RecipeValueSource


class Recipe(_ClosedModel):
    steps: tuple[RecipeStep, ...] = Field(min_length=1)
    result_bindings: tuple[RecipeOutputBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_recipe(self) -> Recipe:
        _unique(self.steps, key=lambda item: item.step_id, name="recipe step IDs")
        by_id = {step.step_id: step for step in self.steps}
        remaining = set(by_id)
        complete: set[str] = set()
        ordered: list[str] = []
        while remaining:
            ready = sorted(
                (step_id for step_id in remaining if set(by_id[step_id].depends_on) <= complete),
                key=lambda item: item.encode("utf-8"),
            )
            if not ready:
                raise ValueError("recipe must be an acyclic graph")
            for step_id in ready:
                remaining.remove(step_id)
                complete.add(step_id)
                ordered.append(step_id)
        if tuple(step.step_id for step in self.steps) != tuple(ordered):
            raise ValueError("recipe steps must use canonical topological order")
        for step in self.steps:
            if any(dependency not in by_id for dependency in step.depends_on):
                raise ValueError("recipe dependency names an unknown step")
            for binding in step.input_bindings:
                source = binding.source
                if source.kind != "step_output":
                    continue
                assert source.step_id is not None and source.field is not None
                if source.step_id not in step.depends_on:
                    raise ValueError("step output source must be an explicit dependency")
        _unique(
            self.result_bindings,
            key=lambda item: item.target_field,
            name="recipe result targets",
        )
        _canonical_order(
            self.result_bindings,
            key=lambda item: item.target_field.encode("utf-8"),
            name="recipe result bindings",
        )
        for result_binding in self.result_bindings:
            source = result_binding.source
            if source.kind != "step_output":
                continue
            assert source.step_id is not None and source.field is not None
            result_step = by_id.get(source.step_id)
            if result_step is None:
                raise ValueError("recipe result binding names an unknown step")
        return self


class SchemaFieldBinding(_ClosedModel):
    """Audit record for one method field expanded from a capability field."""

    method_field: SafeId
    direction: PortDirection
    capability: CapabilityRef
    capability_field: SafeId


class CapabilitySpec(_ClosedModel):
    schema_version: Literal[2] = 2
    id: RegistryId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    kind: CapabilityKind
    summary: RegistryText
    input_schema: JsonObject
    output_schema: JsonObject
    constraints: tuple[ConstraintSpec, ...] = ()

    @model_validator(mode="after")
    def _validate_contract(self) -> CapabilitySpec:
        input_properties = _object_schema(self.input_schema, name="capability input schema")
        output_properties = _object_schema(self.output_schema, name="capability output schema")
        if not output_properties:
            raise ValueError("capability output schema must contain at least one field")
        schema_ports(self.input_schema, direction=PortDirection.INPUT)
        if not schema_ports(self.output_schema, direction=PortDirection.OUTPUT):
            raise ValueError("capability output schema requires a semantic port")
        _unique(self.constraints, key=lambda item: item.id, name="capability constraints")
        _canonical_order(
            self.constraints,
            key=lambda item: item.id.encode("utf-8"),
            name="capability constraints",
        )
        for constraint in self.constraints:
            declared = (
                set(input_properties)
                if constraint.target == PortDirection.INPUT
                else set(output_properties)
            )
            if not _constraint_fields(constraint.expression) <= declared:
                raise ValueError(
                    "capability constraint references an unknown field for its target"
                )
        return self

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "kind": self.kind.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "constraints": [item.model_dump(mode="json") for item in self.constraints],
        }

    @property
    def contract_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=CAPABILITY_CONTRACT_HASH_DOMAIN)

    @property
    def record_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), domain=CAPABILITY_RECORD_HASH_DOMAIN)

    @property
    def ref(self) -> CapabilityRef:
        return CapabilityRef(id=self.id, version=self.version, contract_hash=self.contract_hash)


class MethodSpec(_ClosedModel):
    schema_version: Literal[2] = 2
    id: MethodId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    lifecycle: MethodLifecycle
    summary: RegistryText
    category: SafeId
    discovery: DiscoveryMetadata
    input_schema: JsonObject
    output_schema: JsonObject
    formula: RegistryText
    methodology: tuple[RegistryText, ...] = Field(min_length=1)
    assumptions: tuple[RegistryText, ...] = ()
    limitations: tuple[RegistryText, ...] = ()
    interpretation: RegistryText
    constraints: tuple[ConstraintSpec, ...] = ()
    conventions: tuple[ConventionSpec, ...] = ()
    defaults: tuple[DefaultSpec, ...] = ()
    schema_bindings: tuple[SchemaFieldBinding, ...] = ()
    recipe: Recipe

    @model_validator(mode="after")
    def _validate_method(self) -> MethodSpec:
        input_properties = _object_schema(self.input_schema, name="method input schema")
        output_properties = _object_schema(self.output_schema, name="method output schema")
        if not schema_ports(self.input_schema, direction=PortDirection.INPUT):
            raise ValueError("method input schema requires a semantic port")
        if not schema_ports(self.output_schema, direction=PortDirection.OUTPUT):
            raise ValueError("method output schema requires a semantic port")
        for name in ("methodology", "assumptions", "limitations"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"method {name} must be unique")
        _unique(self.constraints, key=lambda item: item.id, name="method constraints")
        _canonical_order(
            self.constraints,
            key=lambda item: item.id.encode("utf-8"),
            name="method constraints",
        )
        for constraint in self.constraints:
            declared = (
                set(input_properties)
                if constraint.target == PortDirection.INPUT
                else set(output_properties)
            )
            if not _constraint_fields(constraint.expression) <= declared:
                raise ValueError("method constraint references an unknown field for its target")
        _unique(self.conventions, key=lambda item: item.id, name="method convention IDs")
        _unique(self.conventions, key=lambda item: item.field, name="method convention fields")
        _canonical_order(
            self.conventions,
            key=lambda item: (item.field.encode("utf-8"), item.id.encode("utf-8")),
            name="method conventions",
        )
        if any(item.field not in input_properties for item in self.conventions):
            raise ValueError("method convention references an unknown input field")
        _unique(self.defaults, key=lambda item: item.field, name="method defaults")
        _canonical_order(
            self.defaults,
            key=lambda item: item.field.encode("utf-8"),
            name="method defaults",
        )
        default_by_field = {item.field: item for item in self.defaults}
        if any(field not in input_properties for field in default_by_field):
            raise ValueError("method default references an unknown input field")
        if any(
            isinstance(field_schema, Mapping) and "default" in field_schema
            for field_schema in input_properties.values()
        ):
            raise ValueError("method schema defaults must be derived from the defaults block")
        raw_required = self.input_schema.get("required", [])
        required = (
            {item for item in raw_required if isinstance(item, str)}
            if isinstance(raw_required, Sequence) and not isinstance(raw_required, str | bytes)
            else set()
        )
        optional = set(input_properties) - required
        if optional != set(default_by_field):
            raise ValueError("every and only optional method inputs require an authored default")
        convention_by_field = {item.field: item for item in self.conventions}
        if any(
            item.required_from_user and item.field in default_by_field
            for item in self.conventions
        ):
            raise ValueError("required-from-user conventions cannot have defaults")
        if any(
            not item.required_from_user and item.field not in default_by_field
            for item in self.conventions
        ):
            raise ValueError("non-user-required conventions require defaults")
        for field, default in default_by_field.items():
            convention = convention_by_field.get(field)
            if convention is not None and convention.allowed_values:
                if canonical_json_bytes(default.value) not in {
                    canonical_json_bytes(value) for value in convention.allowed_values
                }:
                    raise ValueError("method default is outside convention allowed values")
        for recipe_step in self.recipe.steps:
            for input_binding in recipe_step.input_bindings:
                source = input_binding.source
                if source.kind == "method_input" and source.field not in input_properties:
                    raise ValueError("recipe binding references an unknown method input")
        for result_binding in self.recipe.result_bindings:
            source = result_binding.source
            if source.kind == "method_input" and source.field not in input_properties:
                raise ValueError("recipe result references an unknown method input")
        _unique(
            self.schema_bindings,
            key=lambda item: (item.direction.value, item.method_field),
            name="method schema bindings",
        )
        _canonical_order(
            self.schema_bindings,
            key=lambda item: (
                item.direction.value,
                item.method_field.encode("utf-8"),
                item.capability.id.encode("utf-8"),
                item.capability.version,
                item.capability.contract_hash,
                item.capability_field.encode("utf-8"),
            ),
            name="method schema bindings",
        )
        recipe_capabilities = {recipe_step.capability for recipe_step in self.recipe.steps}
        for schema_binding in self.schema_bindings:
            properties = (
                input_properties
                if schema_binding.direction == PortDirection.INPUT
                else output_properties
            )
            if schema_binding.method_field not in properties:
                raise ValueError("method schema binding names an unknown method field")
            if schema_binding.capability not in recipe_capabilities:
                raise ValueError("method schema binding capability is absent from the recipe")
        if {item.target_field for item in self.recipe.result_bindings} != set(output_properties):
            raise ValueError("recipe result bindings must exactly cover method outputs")
        return self

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "formula": self.formula,
            "methodology": list(self.methodology),
            "assumptions": list(self.assumptions),
            "limitations": list(self.limitations),
            "interpretation": self.interpretation,
            "constraints": [item.model_dump(mode="json") for item in self.constraints],
            "conventions": [item.model_dump(mode="json") for item in self.conventions],
            "defaults": [item.model_dump(mode="json") for item in self.defaults],
            "schema_bindings": [
                item.model_dump(mode="json") for item in self.schema_bindings
            ],
            "recipe": self.recipe.model_dump(mode="json"),
        }

    @property
    def contract_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=METHOD_CONTRACT_HASH_DOMAIN)

    @property
    def record_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), domain=METHOD_RECORD_HASH_DOMAIN)

    @property
    def ref(self) -> MethodRef:
        return MethodRef(id=self.id, version=self.version, contract_hash=self.contract_hash)


class BackendIdentity(_ClosedModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    operator: Annotated[str, Field(min_length=1, max_length=160)] | None = None


class LicenseBoundary(_ClosedModel):
    kind: LicenseKind
    identifier: Annotated[str, Field(min_length=1, max_length=160)] | None = None


class DataBoundary(_ClosedModel):
    egress: DataEgress
    accepts_restricted_data: bool


class BackendSpec(_ClosedModel):
    schema_version: Literal[1] = 1
    id: RegistryId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    kind: BackendKind
    identity: BackendIdentity
    locality: RuntimeLocality
    transports: tuple[TransportKind, ...] = Field(min_length=1)
    requires_network: bool
    credential_requirement: CredentialRequirement
    licensing: LicenseBoundary
    entitlement: EntitlementRequirement
    data_boundary: DataBoundary

    @model_validator(mode="after")
    def _validate_backend(self) -> BackendSpec:
        _unique(self.transports, key=lambda item: item.value, name="backend transports")
        _canonical_order(
            self.transports, key=lambda item: item.value, name="backend transports"
        )
        if self.kind == BackendKind.DQ_NATIVE and (
            self.locality != RuntimeLocality.LOCAL
            or self.requires_network
            or self.credential_requirement != CredentialRequirement.NONE
            or self.data_boundary.egress == DataEgress.REMOTE_SERVICE
        ):
            raise ValueError("DQ-native backend must be local, credential-free, and non-egressing")
        if self.locality == RuntimeLocality.REMOTE and not self.requires_network:
            raise ValueError("remote backend must require network access")
        return self

    def semantic_projection(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value.pop("title")
        return value

    @property
    def spec_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=BACKEND_SPEC_HASH_DOMAIN)

    @property
    def ref(self) -> BackendRef:
        return BackendRef(id=self.id, version=self.version, spec_hash=self.spec_hash)


class BackendBinding(_ClosedModel):
    role: BackendRole
    backend: BackendRef
    transport: TransportKind
    locality: RuntimeLocality


class AdapterSpec(_ClosedModel):
    schema_version: Literal[1] = 1
    id: RegistryId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    family: RegistryId
    backend: BackendRef
    distribution: str = Field(pattern=_DISTRIBUTION_PATTERN)
    contract_version: SemVer
    dispatch_key: SafeId
    transports: tuple[TransportKind, ...] = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def _validate_adapter(self) -> AdapterSpec:
        _unique(self.transports, key=lambda item: item.value, name="adapter transports")
        _canonical_order(
            self.transports, key=lambda item: item.value, name="adapter transports"
        )
        _unique(self.evidence_refs, key=lambda item: item.ref_hash, name="adapter evidence")
        _canonical_order(
            self.evidence_refs,
            key=lambda item: (item.id, item.version, item.evidence_hash),
            name="adapter evidence",
        )
        expected_subject = EvidenceSubject(
            kind=EvidenceSubjectKind.ADAPTER,
            id=self.id,
            version=self.version,
        )
        if any(item.subject != expected_subject for item in self.evidence_refs):
            raise ValueError("adapter evidence must bind the exact adapter")
        return self

    def semantic_projection(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value.pop("title")
        return value

    @property
    def spec_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=ADAPTER_SPEC_HASH_DOMAIN)

    @property
    def ref(self) -> AdapterRef:
        return AdapterRef(
            id=self.id,
            version=self.version,
            spec_hash=self.spec_hash,
            backend=self.backend,
        )


class ImplementationRestriction(_ClosedModel):
    id: SafeId
    description: RegistryText
    expression: JsonObject

    @model_validator(mode="after")
    def _validate_expression(self) -> ImplementationRestriction:
        _validate_constraint_expression(self.expression)
        return self


class AvailabilityRequirements(_ClosedModel):
    installation_required: Literal[True] = True
    dependencies_required: bool
    credentials_required: bool
    licence_required: bool
    entitlement_required: bool
    reachability_required: bool


class DependencyRequirement(_ClosedModel):
    """One non-secret, implementation-specific host dependency probe."""

    id: SafeId
    description: RegistryText


class ImplementationSpec(_ClosedModel):
    schema_version: Literal[2] = 2
    id: RegistryId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    capability: CapabilityRef
    backend_bindings: tuple[BackendBinding, ...] = Field(min_length=1)
    adapter: AdapterRef
    artifact: ArtifactPin
    support_level: SupportLevel
    restrictions: tuple[ImplementationRestriction, ...] = ()
    dependency_requirements: tuple[DependencyRequirement, ...] = ()
    trust: tuple[TrustAssertion, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def _validate_implementation(self) -> ImplementationSpec:
        _unique(
            self.backend_bindings,
            key=lambda item: item.role,
            name="implementation backend roles",
        )
        _canonical_order(
            self.backend_bindings,
            key=lambda item: item.role.value,
            name="implementation backend roles",
        )
        runtime = tuple(
            item for item in self.backend_bindings if item.role == BackendRole.RUNTIME
        )
        if len(runtime) != 1:
            raise ValueError("implementation requires one exact runtime backend")
        if runtime[0].backend != self.adapter.backend:
            raise ValueError("implementation runtime backend must match the adapter backend")
        if self.support_level == SupportLevel.FULL and self.restrictions:
            raise ValueError("full support cannot declare narrowing applicability restrictions")
        if self.support_level == SupportLevel.PARTIAL and not self.restrictions:
            raise ValueError("partial support requires an applicability restriction")
        _unique(self.restrictions, key=lambda item: item.id, name="implementation restrictions")
        _canonical_order(
            self.restrictions,
            key=lambda item: item.id.encode("utf-8"),
            name="implementation restrictions",
        )
        _unique(
            self.dependency_requirements,
            key=lambda item: item.id,
            name="implementation dependency requirements",
        )
        _canonical_order(
            self.dependency_requirements,
            key=lambda item: item.id.encode("utf-8"),
            name="implementation dependency requirements",
        )
        _unique(self.evidence_refs, key=lambda item: item.ref_hash, name="implementation evidence")
        _canonical_order(
            self.evidence_refs,
            key=lambda item: (item.id, item.version, item.evidence_hash),
            name="implementation evidence",
        )
        expected_subject = EvidenceSubject(
            kind=EvidenceSubjectKind.IMPLEMENTATION,
            id=self.id,
            version=self.version,
        )
        if any(item.subject != expected_subject for item in self.evidence_refs):
            raise ValueError("implementation evidence must bind the exact implementation")
        _unique(self.trust, key=lambda item: item.dimension, name="implementation trust")
        _canonical_order(
            self.trust,
            key=lambda item: item.dimension.value,
            name="implementation trust",
        )
        evidence = {item.ref_hash for item in self.evidence_refs}
        if any(item.evidence.ref_hash not in evidence for item in self.trust):
            raise ValueError("trust assertions must cite implementation evidence")
        return self

    @property
    def runtime_backend(self) -> BackendRef:
        return next(
            item.backend
            for item in self.backend_bindings
            if item.role == BackendRole.RUNTIME
        )

    def semantic_projection(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value.pop("title")
        return value

    @property
    def spec_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=IMPLEMENTATION_SPEC_HASH_DOMAIN)

    @property
    def ref(self) -> ImplementationRef:
        return ImplementationRef(id=self.id, version=self.version, spec_hash=self.spec_hash)


def method_contract_hash(method: MethodSpec) -> str:
    return method.contract_hash


def capability_contract_hash(capability: CapabilitySpec) -> str:
    return capability.contract_hash


def backend_spec_hash(backend: BackendSpec) -> str:
    return backend.spec_hash


def adapter_spec_hash(adapter: AdapterSpec) -> str:
    return adapter.spec_hash


def implementation_spec_hash(implementation: ImplementationSpec) -> str:
    return implementation.spec_hash


def derive_availability_requirements(
    implementation: ImplementationSpec,
    backends: Sequence[BackendSpec],
) -> AvailabilityRequirements:
    """Derive operational probes from exact backend boundaries.

    Backend records remain authoritative for network, credential, licensing, and entitlement
    boundaries. Implementations contribute only dependency probes that are specific to that exact
    realization. Passing a missing or contradictory backend set fails closed.
    """

    by_hash = {backend.ref.spec_hash: backend for backend in backends}
    selected: list[BackendSpec] = []
    for binding in implementation.backend_bindings:
        backend = by_hash.get(binding.backend.spec_hash)
        if backend is None or backend.ref != binding.backend:
            raise ValueError("implementation references an unavailable exact backend")
        if binding.transport not in backend.transports:
            raise ValueError("implementation transport contradicts its backend")
        if binding.locality != backend.locality:
            raise ValueError("implementation locality contradicts its backend")
        selected.append(backend)
    return AvailabilityRequirements(
        dependencies_required=bool(implementation.dependency_requirements),
        credentials_required=any(
            backend.credential_requirement != CredentialRequirement.NONE
            for backend in selected
        ),
        licence_required=any(
            backend.licensing.kind != LicenseKind.OPEN_SOURCE for backend in selected
        ),
        entitlement_required=any(
            backend.entitlement != EntitlementRequirement.NONE for backend in selected
        ),
        reachability_required=any(backend.requires_network for backend in selected),
    )


__all__ = [
    "ADAPTER_SPEC_HASH_DOMAIN",
    "BACKEND_SPEC_HASH_DOMAIN",
    "CAPABILITY_CONTRACT_HASH_DOMAIN",
    "CAPABILITY_RECORD_HASH_DOMAIN",
    "IMPLEMENTATION_SPEC_HASH_DOMAIN",
    "METHOD_CONTRACT_HASH_DOMAIN",
    "METHOD_RECORD_HASH_DOMAIN",
    "AdapterRef",
    "AdapterSpec",
    "ArtifactPin",
    "AvailabilityRequirements",
    "BackendBinding",
    "BackendIdentity",
    "BackendKind",
    "BackendRole",
    "BackendRef",
    "BackendSpec",
    "CapabilityKind",
    "CapabilityRef",
    "CapabilitySpec",
    "ConstraintSeverity",
    "ConstraintSpec",
    "ConventionSpec",
    "CredentialRequirement",
    "DataBoundary",
    "DataEgress",
    "DependencyRequirement",
    "DefaultSpec",
    "DiscoveryMetadata",
    "EntitlementRequirement",
    "EvidenceRef",
    "ImplementationRef",
    "ImplementationRestriction",
    "ImplementationSpec",
    "LicenseBoundary",
    "LicenseKind",
    "MethodRef",
    "MethodLifecycle",
    "MethodSpec",
    "Recipe",
    "RecipeInputBinding",
    "RecipeOutputBinding",
    "RecipeStep",
    "RecipeValueSource",
    "RuntimeIdentity",
    "RuntimeLocality",
    "SchemaFieldBinding",
    "SafeId",
    "RegistryId",
    "MethodId",
    "SemVer",
    "Sha256",
    "RegistryText",
    "JsonObject",
    "TransportKind",
    "SupportLevel",
    "TrustAssertion",
    "TrustDimension",
    "adapter_spec_hash",
    "backend_spec_hash",
    "capability_contract_hash",
    "implementation_spec_hash",
    "derive_availability_requirements",
    "method_contract_hash",
    "schema_ports",
]
