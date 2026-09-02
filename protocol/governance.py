"""Closed Phase-1 governance records for deterministic implementation resolution.

These records describe financial methods, backend-neutral capabilities, trusted installed
implementations, deterministic resolution policy, and compile-only outcomes.  They do not fetch
data, execute an implementation, authorize a managed run, or attest that a calculation occurred.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

from ._immutable_json import freeze_json
from .canonical import canonical_hash, canonical_json_bytes
from .operation import ComponentRef
from .ports import PortDirection, SemanticPort

_SAFE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_REGISTRY_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,5}$"
_METHOD_ID_PATTERN = r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_DISTRIBUTION_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$"

METHOD_SPEC_HASH_DOMAIN = "governance.method_spec.v1"
CAPABILITY_SPEC_HASH_DOMAIN = "governance.capability_spec.v1"
IMPLEMENTATION_SPEC_HASH_DOMAIN = "governance.implementation_spec.v1"
RESOLUTION_POLICY_HASH_DOMAIN = "governance.resolution_policy.v1"
COMPILED_PLAN_HASH_DOMAIN = "governance.compiled_plan.v1"
MAX_CONSTRAINT_EXPRESSION_DEPTH = 3

_FORBIDDEN_PROPOSAL_KEYS = frozenset(
    {
        "api_key",
        "backend",
        "code",
        "credential",
        "credentials",
        "connection",
        "endpoint",
        "implementation",
        "import",
        "import_path",
        "mcp",
        "mcp_tool",
        "provider",
        "python",
        "python_code",
        "secret",
        "sql",
        "token",
        "url",
    }
)
_FORBIDDEN_PROPOSAL_KEY_TOKENS = frozenset(
    {
        "backend",
        "credential",
        "credentials",
        "connection",
        "endpoint",
        "implementation",
        "import",
        "mcp",
        "provider",
        "python",
        "secret",
        "sql",
        "token",
        "url",
    }
)

SafeId: TypeAlias = Annotated[str, Field(pattern=_SAFE_ID_PATTERN)]
RegistryId: TypeAlias = Annotated[str, Field(max_length=240, pattern=_REGISTRY_ID_PATTERN)]
MethodId: TypeAlias = Annotated[str, Field(pattern=_METHOD_ID_PATTERN)]
SemVer: TypeAlias = Annotated[str, Field(pattern=_SEMVER_PATTERN)]
Sha256: TypeAlias = Annotated[str, Field(pattern=_SHA256_PATTERN)]
GovernanceText: TypeAlias = Annotated[str, Field(min_length=1, max_length=4000)]
JsonObject: TypeAlias = dict[str, JsonValue]


def _canonical_ref_key(value: Any) -> tuple[str, str, str]:
    identity_hash = (
        getattr(value, "spec_hash", None)
        or getattr(value, "capability_hash", None)
        or getattr(value, "implementation_hash", None)
    )
    identifier = getattr(value, "id", None)
    version = getattr(value, "version", None)
    if (
        not isinstance(identifier, str)
        or not isinstance(version, str)
        or not isinstance(identity_hash, str)
    ):
        raise TypeError("governance reference is incomplete")
    return (identifier, version, identity_hash)


def _require_canonical_order(values: tuple[Any, ...], *, key: Any, name: str) -> None:
    if tuple(sorted(values, key=key)) != values:
        raise ValueError(f"{name} must use canonical order")


def _require_unique(values: tuple[Any, ...], *, key: Any, name: str) -> None:
    keys = tuple(key(value) for value in values)
    if len(set(keys)) != len(keys):
        raise ValueError(f"{name} must be unique")


def _validate_object_schema(value: JsonObject, *, name: str) -> dict[str, Any]:
    canonical_json_bytes(value)
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ValueError(f"{name} must be a closed object JSON Schema")
    properties = value.get("properties")
    if not isinstance(properties, dict):
        raise ValueError(f"{name} must declare object properties")
    if any(
        not isinstance(field, str) or re.fullmatch(_SAFE_ID_PATTERN, field) is None
        for field in properties
    ):
        raise ValueError(f"{name} property names must be safe field IDs")
    return properties


def _proposal_key_is_forbidden(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    tokens = frozenset(part for part in normalized.split("_") if part)
    return normalized in _FORBIDDEN_PROPOSAL_KEYS or bool(
        tokens & _FORBIDDEN_PROPOSAL_KEY_TOKENS
    )


def _validate_schema_control_fields(value: Any, *, name: str) -> None:
    if not isinstance(value, dict):
        return
    properties = value.get("properties")
    if isinstance(properties, dict):
        forbidden = tuple(sorted(key for key in properties if _proposal_key_is_forbidden(key)))
        if forbidden:
            raise ValueError(f"{name} contains forbidden control-plane fields: {forbidden}")
        for nested in properties.values():
            _validate_schema_control_fields(nested, name=name)
    definitions = value.get("$defs")
    if isinstance(definitions, dict):
        for nested in definitions.values():
            _validate_schema_control_fields(nested, name=name)
    items = value.get("items")
    if isinstance(items, dict):
        _validate_schema_control_fields(items, name=name)
    branches = value.get("anyOf")
    if isinstance(branches, list):
        for nested in branches:
            _validate_schema_control_fields(nested, name=name)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def freeze_hash_bound_json(self) -> Self:
        """Detach and recursively freeze every nested JSON container."""

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
        """Return a fully revalidated copy; Pydantic's unchecked update is unsafe here."""

        del deep
        values = self.model_dump(
            mode="python",
            exclude=set(type(self).model_computed_fields),
            exclude_unset=True,
        )
        if update is not None:
            values.update(update)
        return type(self).model_validate(values)


class CapabilityKind(StrEnum):
    ACQUISITION = "acquisition"
    TRANSFORMATION = "transformation"
    CALCULATION = "calculation"
    DIAGNOSTIC = "diagnostic"
    VERIFICATION = "verification"


class ImplementationTransport(StrEnum):
    DQ_NATIVE = "dq_native"
    PYTHON_PACKAGE = "python_package"
    NATIVE_LIBRARY = "native_library"
    HTTP_API = "http_api"
    SQL = "sql"
    EXTERNAL_MCP = "external_mcp"


class TransportLocality(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class TrustDimension(StrEnum):
    TRUSTED_ADAPTER = "trusted_adapter"
    ARTIFACT_PINNED = "artifact_pinned"
    DEPENDENCY_LOCKED = "dependency_locked"
    SCHEMA_CHECKED = "schema_checked"
    DETERMINISTIC = "deterministic"
    UNIT_TESTED = "unit_tested"
    GOLDEN_TESTED = "golden_tested"
    PROPERTY_TESTED = "property_tested"
    DIFFERENTIAL_CHECKED = "differential_checked"
    DOMAIN_REVIEWED = "domain_reviewed"
    SANDBOXED = "sandboxed"
    PROVIDER_AUTHENTICATED = "provider_authenticated"
    REMOTE_EXECUTOR_UNATTESTED = "remote_executor_unattested"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AvailabilityReason(StrEnum):
    READY = "ready"
    DISABLED = "disabled"
    NOT_INSTALLED = "not_installed"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CREDENTIAL_CAPABILITY_UNAVAILABLE = "credential_capability_unavailable"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    STATUS_UNKNOWN = "status_unknown"


class ConstraintSeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


class ConstraintMeasure(StrEnum):
    IDENTITY = "identity"
    COUNT = "count"
    STDEV = "stdev"
    ALL_FINITE = "all_finite"
    MINIMUM = "min"
    MAXIMUM = "max"
    ALL_VALID_CALENDAR_MONTH_LABELS = "all_valid_calendar_month_labels"
    HAS_DUPLICATES = "has_duplicates"
    IS_CONSECUTIVE_CALENDAR_MONTHS = "is_consecutive_calendar_months"
    IS_STRICTLY_INCREASING = "is_strictly_increasing"


class ConstraintOperator(StrEnum):
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    EQ = "eq"
    NE = "ne"
    IN_SET = "in_set"
    NOT_IN_SET = "not_in_set"


class PlanRefusalCode(StrEnum):
    METHOD_NOT_FOUND = "method_not_found"
    METHOD_IDENTITY_MISMATCH = "method_identity_mismatch"
    POLICY_BINDING_MISMATCH = "policy_binding_mismatch"
    EXECUTION_SCOPE_DENIED = "execution_scope_denied"
    INVALID_FINANCIAL_INPUT = "invalid_financial_input"
    CONVENTION_CONFLICT = "convention_conflict"
    CONSTRAINT_VIOLATION = "constraint_violation"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    INCOMPATIBLE_IMPLEMENTATION = "incompatible_implementation"
    TRUST_REQUIREMENT_UNSATISFIED = "trust_requirement_unsatisfied"
    NO_APPROVED_IMPLEMENTATION = "no_approved_implementation"
    AVAILABILITY_UNRESOLVED = "availability_unresolved"
    INVALID_GOVERNANCE_RECORD = "invalid_governance_record"


class NamedPortV1(_ClosedModel):
    field: SafeId
    port: SemanticPort


def _validate_port_schema_binding(
    ports: tuple[NamedPortV1, ...],
    properties: Mapping[str, Any],
    *,
    name: str,
) -> None:
    schema_ports = {
        field: value.get("x-defined-quant-port")
        for field, value in properties.items()
        if isinstance(value, Mapping) and "x-defined-quant-port" in value
    }
    if set(schema_ports) != {item.field for item in ports}:
        raise ValueError(f"{name} must exactly match JSON Schema port metadata")
    for item in ports:
        metadata = schema_ports[item.field]
        if not isinstance(metadata, Mapping) or SemanticPort.model_validate(metadata) != item.port:
            raise ValueError(f"{name} contradict JSON Schema port metadata")


class MethodRefV1(_ClosedModel):
    id: MethodId
    version: SemVer
    spec_hash: Sha256


class CapabilityRefV1(_ClosedModel):
    id: RegistryId
    version: SemVer
    capability_hash: Sha256


class ImplementationRefV1(_ClosedModel):
    id: RegistryId
    version: SemVer
    implementation_hash: Sha256


class ResolutionPolicyRefV1(_ClosedModel):
    id: SafeId
    version: SemVer
    policy_hash: Sha256


class ConstraintOperandV1(_ClosedModel):
    field: SafeId | None = None
    value: JsonValue | None = None
    measure: ConstraintMeasure | None = None

    @model_validator(mode="after")
    def validate_source(self) -> ConstraintOperandV1:
        has_field = self.field is not None
        has_value = "value" in self.model_fields_set
        if has_field == has_value:
            raise ValueError("constraint operand requires exactly one field or value")
        if self.measure is not None and not has_field:
            raise ValueError("constraint operand measure requires a field")
        if has_value:
            canonical_json_bytes(self.value)
        return self


class ConstraintComparisonV1(_ClosedModel):
    left: ConstraintOperandV1
    operator: ConstraintOperator
    right: ConstraintOperandV1


class ConstraintConditionV1(_ClosedModel):
    comparison: ConstraintComparisonV1 | None = None
    all_of: tuple[ConstraintConditionV1, ...] = ()
    any_of: tuple[ConstraintConditionV1, ...] = ()

    @model_validator(mode="after")
    def validate_branch(self) -> ConstraintConditionV1:
        populated = (
            int(self.comparison is not None) + int(bool(self.all_of)) + int(bool(self.any_of))
        )
        if populated != 1:
            raise ValueError("constraint condition requires exactly one non-empty branch")
        for name, values in (("all_of", self.all_of), ("any_of", self.any_of)):
            if values:
                _require_unique(
                    values,
                    key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
                    name=f"constraint {name} branches",
                )
                _require_canonical_order(
                    values,
                    key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
                    name=f"constraint {name} branches",
                )
        return self


def _validate_constraint_condition_static(
    condition: ConstraintConditionV1,
    input_properties: Mapping[str, Any],
    input_schema: Mapping[str, Any],
    *,
    depth: int = 1,
) -> None:
    if depth > MAX_CONSTRAINT_EXPRESSION_DEPTH:
        raise ValueError("constraint expression exceeds the closed maximum depth")
    comparison = condition.comparison
    if comparison is not None:
        for operand in (comparison.left, comparison.right):
            if operand.field is None:
                continue
            field_schema = input_properties.get(operand.field)
            if not isinstance(field_schema, Mapping):
                raise ValueError("constraint operands must name method input fields")
            if not _constraint_measure_matches_schema(
                operand.measure,
                field_schema,
                input_schema,
            ):
                raise ValueError("constraint measure is incompatible with its input schema")
        left_types = _constraint_operand_result_types(
            comparison.left,
            input_properties,
            input_schema,
        )
        right_types = _constraint_operand_result_types(
            comparison.right,
            input_properties,
            input_schema,
        )
        if comparison.operator in {
            ConstraintOperator.LT,
            ConstraintOperator.LE,
            ConstraintOperator.GT,
            ConstraintOperator.GE,
        } and not _relational_operand_types_are_compatible(left_types, right_types):
            raise ValueError("constraint operator is incompatible with operand result types")
        if comparison.operator in {
            ConstraintOperator.IN_SET,
            ConstraintOperator.NOT_IN_SET,
        }:
            right = comparison.right
            if right.field is None and not isinstance(right.value, list):
                raise ValueError("set-membership constraints require an array right operand")
            if (right_types - {"null"}) != {"array"}:
                raise ValueError("constraint operator is incompatible with operand result types")
        return
    children = condition.all_of or condition.any_of
    for child in children:
        _validate_constraint_condition_static(
            child,
            input_properties,
            input_schema,
            depth=depth + 1,
        )


def _schema_instance_types(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> set[str]:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        definitions = root.get("$defs")
        if not isinstance(definitions, Mapping) or reference in seen_refs:
            return set()
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping):
            return set()
        return _schema_instance_types(
            target,
            root,
            seen_refs=seen_refs | {reference},
        )
    branches = schema.get("anyOf")
    if isinstance(branches, list):
        result: set[str] = set()
        for branch in branches:
            if not isinstance(branch, Mapping):
                return set()
            result.update(_schema_instance_types(branch, root, seen_refs=seen_refs))
        return result
    declared = schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list) and all(isinstance(item, str) for item in declared):
        return set(declared)

    values: list[Any] = []
    if "const" in schema:
        values.append(schema["const"])
    enum = schema.get("enum")
    if isinstance(enum, list):
        values.extend(enum)
    result = set()
    for value in values:
        if value is None:
            result.add("null")
        elif isinstance(value, bool):
            result.add("boolean")
        elif isinstance(value, int):
            result.add("integer")
        elif isinstance(value, float):
            result.add("number")
        elif isinstance(value, str):
            result.add("string")
        elif isinstance(value, list):
            result.add("array")
        elif isinstance(value, dict):
            result.add("object")
    return result


def _schema_array_item_types(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> set[str]:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        definitions = root.get("$defs")
        if not isinstance(definitions, Mapping) or reference in seen_refs:
            return set()
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping):
            return set()
        return _schema_array_item_types(
            target,
            root,
            seen_refs=seen_refs | {reference},
        )
    branches = schema.get("anyOf")
    if isinstance(branches, list):
        result: set[str] = set()
        for branch in branches:
            if not isinstance(branch, Mapping):
                return set()
            result.update(_schema_array_item_types(branch, root, seen_refs=seen_refs))
        return result
    if "array" not in _schema_instance_types(schema, root):
        return set()
    items = schema.get("items")
    if not isinstance(items, Mapping):
        return set()
    return _schema_instance_types(items, root)


def _json_instance_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    raise TypeError("constraint literal must be a JSON value")


def _constraint_operand_result_types(
    operand: ConstraintOperandV1,
    input_properties: Mapping[str, Any],
    input_schema: Mapping[str, Any],
) -> set[str]:
    if operand.field is None:
        return {_json_instance_type(operand.value)}
    field_schema = input_properties[operand.field]
    if not isinstance(field_schema, Mapping):
        return set()
    selected = (
        ConstraintMeasure.IDENTITY if operand.measure is None else operand.measure
    )
    if selected == ConstraintMeasure.IDENTITY:
        return _schema_instance_types(field_schema, input_schema)
    if selected == ConstraintMeasure.COUNT:
        return {"integer"}
    if selected == ConstraintMeasure.STDEV:
        return {"number"}
    if selected in {
        ConstraintMeasure.MINIMUM,
        ConstraintMeasure.MAXIMUM,
    }:
        return _schema_array_item_types(field_schema, input_schema)
    return {"boolean"}


def _relational_operand_types_are_compatible(
    left_types: set[str],
    right_types: set[str],
) -> bool:
    left = left_types - {"null"}
    right = right_types - {"null"}
    numeric = {"integer", "number"}
    return bool(left and right) and (
        (left <= numeric and right <= numeric)
        or (left == {"string"} and right == {"string"})
    )


def _constraint_measure_matches_schema(
    measure: ConstraintMeasure | None,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
) -> bool:
    selected = ConstraintMeasure.IDENTITY if measure is None else measure
    if selected == ConstraintMeasure.IDENTITY:
        return True
    types = _schema_instance_types(schema, root) - {"null"}
    if not types:
        return False
    if selected == ConstraintMeasure.COUNT:
        return types <= {"array", "object"}
    if selected == ConstraintMeasure.ALL_FINITE:
        if not types <= {"array", "integer", "number"}:
            return False
        if "array" not in types:
            return True
    elif not types <= {"array"}:
        return False

    if "array" not in types:
        return True
    items = schema.get("items")
    if not isinstance(items, Mapping):
        branches = schema.get("anyOf")
        if isinstance(branches, list):
            return all(
                not isinstance(branch, Mapping)
                or "null" in _schema_instance_types(branch, root)
                or _constraint_measure_matches_schema(selected, branch, root)
                for branch in branches
            )
        return selected in {
            ConstraintMeasure.HAS_DUPLICATES,
            ConstraintMeasure.COUNT,
        }
    item_types = _schema_instance_types(items, root) - {"null"}
    if selected in {ConstraintMeasure.ALL_FINITE, ConstraintMeasure.STDEV}:
        return bool(item_types) and item_types <= {"integer", "number"}
    if selected in {
        ConstraintMeasure.ALL_VALID_CALENDAR_MONTH_LABELS,
        ConstraintMeasure.IS_CONSECUTIVE_CALENDAR_MONTHS,
    }:
        return item_types == {"string"}
    if selected in {
        ConstraintMeasure.MINIMUM,
        ConstraintMeasure.MAXIMUM,
        ConstraintMeasure.IS_STRICTLY_INCREASING,
    }:
        return bool(item_types) and item_types <= {"integer", "number", "string"}
    return selected == ConstraintMeasure.HAS_DUPLICATES


class ConstraintSpecV1(_ClosedModel):
    id: SafeId
    severity: ConstraintSeverity
    when: ConstraintConditionV1
    message: GovernanceText


class ConventionSpecV1(_ClosedModel):
    id: SafeId
    field: SafeId
    question: GovernanceText
    materiality: GovernanceText
    required_from_user: bool
    allowed_values: tuple[JsonValue, ...] = ()

    @model_validator(mode="after")
    def validate_allowed_values(self) -> ConventionSpecV1:
        for value in self.allowed_values:
            canonical_json_bytes(value)
        if len({canonical_json_bytes(value) for value in self.allowed_values}) != len(
            self.allowed_values
        ):
            raise ValueError("convention allowed values must be unique")
        return self


class DefaultSpecV1(_ClosedModel):
    field: SafeId
    value: JsonValue
    rationale: GovernanceText

    @model_validator(mode="after")
    def validate_value(self) -> DefaultSpecV1:
        canonical_json_bytes(self.value)
        return self


class AppliedDefaultV1(_ClosedModel):
    field: SafeId
    value: JsonValue
    rationale: GovernanceText

    @model_validator(mode="after")
    def validate_value(self) -> AppliedDefaultV1:
        canonical_json_bytes(self.value)
        return self


class RecipeValueSourceV1(_ClosedModel):
    source: Literal["method_input", "step_output"]
    field: SafeId
    step_id: SafeId | None = None

    @model_validator(mode="after")
    def validate_source(self) -> RecipeValueSourceV1:
        if (self.source == "step_output") != (self.step_id is not None):
            raise ValueError("step_output sources require exactly one step_id")
        return self


class RecipeInputBindingV1(_ClosedModel):
    target_field: SafeId
    source: RecipeValueSourceV1


class RecipeStepV1(_ClosedModel):
    step_id: SafeId
    capability_id: RegistryId
    depends_on: tuple[SafeId, ...] = ()
    input_bindings: tuple[RecipeInputBindingV1, ...] = ()
    output_fields: tuple[SafeId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dependencies(self) -> RecipeStepV1:
        if self.step_id in self.depends_on:
            raise ValueError("recipe step cannot depend on itself")
        if tuple(sorted(self.depends_on)) != self.depends_on:
            raise ValueError("recipe dependencies must use canonical order")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("recipe dependencies must be unique")
        _require_unique(
            self.input_bindings,
            key=lambda item: item.target_field,
            name="recipe input bindings",
        )
        _require_canonical_order(
            self.input_bindings,
            key=lambda item: item.target_field,
            name="recipe input bindings",
        )
        if tuple(sorted(self.output_fields)) != self.output_fields:
            raise ValueError("recipe output fields must use canonical order")
        if len(set(self.output_fields)) != len(self.output_fields):
            raise ValueError("recipe output fields must be unique")
        return self


class RecipeDagV1(_ClosedModel):
    steps: tuple[RecipeStepV1, ...] = Field(min_length=1)
    result_steps: tuple[SafeId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_topology(self) -> RecipeDagV1:
        by_id = {step.step_id: step for step in self.steps}
        if len(by_id) != len(self.steps):
            raise ValueError("recipe step IDs must be unique")
        for step in self.steps:
            if any(dependency not in by_id for dependency in step.depends_on):
                raise ValueError("recipe dependency must name another recipe step")
            for binding in step.input_bindings:
                source = binding.source
                if source.source != "step_output":
                    continue
                assert source.step_id is not None
                if source.step_id not in step.depends_on:
                    raise ValueError("step-output source must be an explicit dependency")
                if source.field not in by_id[source.step_id].output_fields:
                    raise ValueError("step-output source field is not declared by its step")

        remaining = set(by_id)
        completed: set[str] = set()
        canonical_ids: list[str] = []
        while remaining:
            ready = sorted(
                step_id for step_id in remaining if set(by_id[step_id].depends_on) <= completed
            )
            if not ready:
                raise ValueError("recipe must be an acyclic graph")
            for step_id in ready:
                remaining.remove(step_id)
                completed.add(step_id)
                canonical_ids.append(step_id)
        if tuple(step.step_id for step in self.steps) != tuple(canonical_ids):
            raise ValueError("recipe steps must use canonical topological order")

        if tuple(sorted(self.result_steps)) != self.result_steps:
            raise ValueError("recipe result steps must use canonical order")
        if len(set(self.result_steps)) != len(self.result_steps):
            raise ValueError("recipe result steps must be unique")
        if any(step_id not in by_id for step_id in self.result_steps):
            raise ValueError("recipe result step must name a recipe step")
        depended_on = {dependency for step in self.steps for dependency in step.depends_on}
        if any(step_id in depended_on for step_id in self.result_steps):
            raise ValueError("recipe result steps must be terminal steps")
        return self


class CapabilitySpecV1(_ClosedModel):
    schema_version: Literal[1] = 1
    id: RegistryId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    kind: CapabilityKind
    summary: GovernanceText
    input_schema: JsonObject
    output_schema: JsonObject
    input_ports: tuple[NamedPortV1, ...] = ()
    output_ports: tuple[NamedPortV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ports(self) -> CapabilitySpecV1:
        input_properties = _validate_object_schema(
            self.input_schema, name="capability input schema"
        )
        _validate_schema_control_fields(
            self.input_schema,
            name="capability input schema",
        )
        output_properties = _validate_object_schema(
            self.output_schema, name="capability output schema"
        )
        for name, ports, direction, properties in (
            (
                "capability input ports",
                self.input_ports,
                PortDirection.INPUT,
                input_properties,
            ),
            (
                "capability output ports",
                self.output_ports,
                PortDirection.OUTPUT,
                output_properties,
            ),
        ):
            _require_unique(ports, key=lambda item: item.field, name=name)
            _require_canonical_order(ports, key=lambda item: item.field, name=name)
            if any(item.port.direction != direction for item in ports):
                raise ValueError(f"{name} use the wrong semantic direction")
            if any(item.field not in properties for item in ports):
                raise ValueError(f"{name} must name fields in the corresponding JSON Schema")
            _validate_port_schema_binding(ports, properties, name=name)
        return self

    @property
    def capability_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), domain=CAPABILITY_SPEC_HASH_DOMAIN)

    @property
    def ref(self) -> CapabilityRefV1:
        return CapabilityRefV1(
            id=self.id, version=self.version, capability_hash=self.capability_hash
        )


class MethodSpecV1(_ClosedModel):
    schema_version: Literal[1] = 1
    id: MethodId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    summary: GovernanceText
    input_schema: JsonObject
    output_schema: JsonObject
    input_ports: tuple[NamedPortV1, ...] = Field(min_length=1)
    output_ports: tuple[NamedPortV1, ...] = Field(min_length=1)
    methodology: tuple[GovernanceText, ...] = Field(min_length=1)
    assumptions: tuple[GovernanceText, ...] = ()
    limitations: tuple[GovernanceText, ...] = ()
    interpretation: GovernanceText
    constraints: tuple[ConstraintSpecV1, ...] = ()
    conventions: tuple[ConventionSpecV1, ...] = ()
    defaults: tuple[DefaultSpecV1, ...] = ()
    capabilities: tuple[CapabilityRefV1, ...] = Field(min_length=1)
    recipe: RecipeDagV1

    @model_validator(mode="after")
    def validate_method(self) -> MethodSpecV1:
        input_properties = _validate_object_schema(self.input_schema, name="method input schema")
        _validate_schema_control_fields(self.input_schema, name="method input schema")
        output_properties = _validate_object_schema(self.output_schema, name="method output schema")
        for name, values in (
            ("method assumptions", self.assumptions),
            ("method limitations", self.limitations),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")
        for name, ports, direction, properties in (
            ("method input ports", self.input_ports, PortDirection.INPUT, input_properties),
            ("method output ports", self.output_ports, PortDirection.OUTPUT, output_properties),
        ):
            _require_unique(ports, key=lambda item: item.field, name=name)
            _require_canonical_order(ports, key=lambda item: item.field, name=name)
            if any(item.port.direction != direction for item in ports):
                raise ValueError(f"{name} use the wrong semantic direction")
            if any(item.field not in properties for item in ports):
                raise ValueError(f"{name} must name fields in the corresponding JSON Schema")
            _validate_port_schema_binding(ports, properties, name=name)

        _require_unique(self.constraints, key=lambda item: item.id, name="method constraints")
        _require_canonical_order(
            self.constraints, key=lambda item: item.id, name="method constraints"
        )
        for constraint in self.constraints:
            _validate_constraint_condition_static(
                constraint.when,
                input_properties,
                self.input_schema,
            )
        _require_unique(self.conventions, key=lambda item: item.field, name="method conventions")
        _require_unique(self.conventions, key=lambda item: item.id, name="method convention IDs")
        _require_canonical_order(
            self.conventions,
            key=lambda item: (item.field, item.id),
            name="method conventions",
        )
        if any(item.field not in input_properties for item in self.conventions):
            raise ValueError("method convention fields must exist in the input schema")

        _require_unique(self.defaults, key=lambda item: item.field, name="method defaults")
        _require_canonical_order(self.defaults, key=lambda item: item.field, name="method defaults")
        convention_by_field = {item.field: item for item in self.conventions}
        default_by_field = {item.field: item for item in self.defaults}
        for default in self.defaults:
            if default.field not in input_properties:
                raise ValueError("method defaults must resolve declared input fields")
            convention = convention_by_field.get(default.field)
            if convention is not None and convention.required_from_user:
                raise ValueError("required-from-user conventions cannot declare defaults")
            if (
                convention is not None
                and convention.allowed_values
                and canonical_json_bytes(default.value)
                not in {canonical_json_bytes(value) for value in convention.allowed_values}
            ):
                raise ValueError("convention defaults must use an allowed convention value")
        required_fields = self.input_schema.get("required", [])
        if not isinstance(required_fields, list) or any(
            not isinstance(field, str) for field in required_fields
        ):
            raise ValueError("method input schema required fields are malformed")
        for field, property_schema in input_properties.items():
            if not isinstance(property_schema, dict):
                raise ValueError("method input property schemas must be objects")
            authored_default = default_by_field.get(field)
            if field not in required_fields and authored_default is None:
                raise ValueError("every optional method input requires an authored default")
            if "default" in property_schema and (
                authored_default is None
                or canonical_json_bytes(authored_default.value)
                != canonical_json_bytes(property_schema["default"])
            ):
                raise ValueError("schema defaults must exactly match authored method defaults")
        if any(
            not convention.required_from_user
            and convention.field not in default_by_field
            for convention in self.conventions
        ):
            raise ValueError("non-user-required conventions require an authored default")

        _require_unique(self.capabilities, key=lambda item: item.id, name="method capabilities")
        _require_canonical_order(
            self.capabilities, key=_canonical_ref_key, name="method capabilities"
        )
        capability_ids = {item.id for item in self.capabilities}
        recipe_capability_ids = {step.capability_id for step in self.recipe.steps}
        if recipe_capability_ids != capability_ids:
            raise ValueError("recipe must reference every and only embedded capability")
        for step in self.recipe.steps:
            for binding in step.input_bindings:
                source = binding.source
                if source.source == "method_input" and source.field not in input_properties:
                    raise ValueError("recipe input binding must name a method input field")
        result_fields = {
            field
            for step in self.recipe.steps
            if step.step_id in self.recipe.result_steps
            for field in step.output_fields
        }
        if not set(output_properties).issubset(result_fields):
            raise ValueError("recipe result steps must expose every method output field")
        return self

    @property
    def spec_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), domain=METHOD_SPEC_HASH_DOMAIN)

    @property
    def ref(self) -> MethodRefV1:
        return MethodRefV1(id=self.id, version=self.version, spec_hash=self.spec_hash)


class TrustedAdapterV1(_ClosedModel):
    id: SafeId
    version: SemVer
    distribution: str = Field(pattern=_DISTRIBUTION_PATTERN)


class TransportMetadataV1(_ClosedModel):
    kind: ImplementationTransport
    locality: TransportLocality
    system_id: SafeId
    requires_network: bool
    requires_credentials: bool

    @model_validator(mode="after")
    def validate_transport(self) -> TransportMetadataV1:
        if self.kind == ImplementationTransport.DQ_NATIVE and (
            self.locality != TransportLocality.LOCAL
            or self.requires_network
            or self.requires_credentials
        ):
            raise ValueError("DQ-native transport must be local and credential-free")
        if self.kind == ImplementationTransport.HTTP_API and (
            self.locality != TransportLocality.REMOTE or not self.requires_network
        ):
            raise ValueError("HTTP API transport must be remote and require network access")
        return self


class ImplementationSpecV1(_ClosedModel):
    schema_version: Literal[1] = 1
    id: RegistryId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=160)]
    capability: CapabilityRefV1
    input_ports: tuple[NamedPortV1, ...] = ()
    output_ports: tuple[NamedPortV1, ...] = Field(min_length=1)
    adapter: TrustedAdapterV1
    transport: TransportMetadataV1
    component: ComponentRef | None = None
    trust_dimensions: tuple[TrustDimension, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> ImplementationSpecV1:
        if (
            tuple(sorted(self.trust_dimensions, key=lambda item: item.value))
            != self.trust_dimensions
        ):
            raise ValueError("implementation trust dimensions must use canonical order")
        if len(set(self.trust_dimensions)) != len(self.trust_dimensions):
            raise ValueError("implementation trust dimensions must be unique")
        if TrustDimension.TRUSTED_ADAPTER not in self.trust_dimensions:
            raise ValueError("implementation must declare trusted_adapter")
        if (
            self.transport.locality == TransportLocality.REMOTE
            and TrustDimension.REMOTE_EXECUTOR_UNATTESTED not in self.trust_dimensions
        ):
            raise ValueError("remote implementation must disclose remote_executor_unattested")
        for name, ports, direction in (
            ("implementation input ports", self.input_ports, PortDirection.INPUT),
            ("implementation output ports", self.output_ports, PortDirection.OUTPUT),
        ):
            _require_unique(ports, key=lambda item: item.field, name=name)
            _require_canonical_order(ports, key=lambda item: item.field, name=name)
            if any(item.port.direction != direction for item in ports):
                raise ValueError(f"{name} use the wrong semantic direction")
        if (self.transport.kind == ImplementationTransport.DQ_NATIVE) != (
            self.component is not None
        ):
            raise ValueError("DQ-native implementations require exactly one component binding")
        return self

    @property
    def implementation_hash(self) -> str:
        return canonical_hash(
            self.model_dump(mode="json"),
            domain=IMPLEMENTATION_SPEC_HASH_DOMAIN,
        )

    @property
    def ref(self) -> ImplementationRefV1:
        return ImplementationRefV1(
            id=self.id,
            version=self.version,
            implementation_hash=self.implementation_hash,
        )


class PolicyImplementationV1(_ClosedModel):
    implementation: ImplementationRefV1
    priority: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class CapabilityResolutionRuleV1(_ClosedModel):
    capability: CapabilityRefV1
    implementations: tuple[PolicyImplementationV1, ...] = Field(min_length=1)
    required_trust_dimensions: tuple[TrustDimension, ...] = Field(min_length=1)
    allowed_transports: tuple[ImplementationTransport, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rule(self) -> CapabilityResolutionRuleV1:
        _require_unique(
            self.implementations,
            key=lambda item: item.implementation.implementation_hash,
            name="policy implementation priority",
        )
        _require_canonical_order(
            self.implementations,
            key=lambda item: (
                item.priority,
                item.implementation.id.encode("utf-8"),
                item.implementation.version,
                item.implementation.implementation_hash,
            ),
            name="policy implementation priority",
        )
        if (
            tuple(sorted(self.required_trust_dimensions, key=lambda item: item.value))
            != self.required_trust_dimensions
        ):
            raise ValueError("required trust dimensions must use canonical order")
        if len(set(self.required_trust_dimensions)) != len(self.required_trust_dimensions):
            raise ValueError("required trust dimensions must be unique")
        if (
            tuple(sorted(self.allowed_transports, key=lambda item: item.value))
            != self.allowed_transports
        ):
            raise ValueError("allowed transports must use canonical order")
        if len(set(self.allowed_transports)) != len(self.allowed_transports):
            raise ValueError("allowed transports must be unique")
        return self


class ResolutionPolicyV1(_ClosedModel):
    schema_version: Literal[1] = 1
    id: SafeId
    version: SemVer
    method: MethodRefV1
    capability_rules: tuple[CapabilityResolutionRuleV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rules(self) -> ResolutionPolicyV1:
        _require_unique(
            self.capability_rules,
            key=lambda item: item.capability.capability_hash,
            name="policy capability rules",
        )
        _require_canonical_order(
            self.capability_rules,
            key=lambda item: _canonical_ref_key(item.capability),
            name="policy capability rules",
        )
        return self

    @property
    def policy_hash(self) -> str:
        return canonical_hash(
            self.model_dump(mode="json"),
            domain=RESOLUTION_POLICY_HASH_DOMAIN,
        )

    @property
    def ref(self) -> ResolutionPolicyRefV1:
        return ResolutionPolicyRefV1(id=self.id, version=self.version, policy_hash=self.policy_hash)


class ImplementationAvailabilityV1(_ClosedModel):
    implementation: ImplementationRefV1
    status: AvailabilityStatus
    reason: AvailabilityReason

    @model_validator(mode="after")
    def validate_reason(self) -> ImplementationAvailabilityV1:
        if (self.status == AvailabilityStatus.AVAILABLE) != (
            self.reason == AvailabilityReason.READY
        ):
            raise ValueError("available status and ready reason must occur together")
        if (
            self.status == AvailabilityStatus.UNKNOWN
            and self.reason != AvailabilityReason.STATUS_UNKNOWN
        ):
            raise ValueError("unknown availability requires status_unknown reason")
        return self


class AvailabilitySnapshotV1(_ClosedModel):
    schema_version: Literal[1] = 1
    implementations: tuple[ImplementationAvailabilityV1, ...] = ()

    @model_validator(mode="after")
    def validate_implementations(self) -> AvailabilitySnapshotV1:
        _require_unique(
            self.implementations,
            key=lambda item: item.implementation.implementation_hash,
            name="availability implementations",
        )
        _require_canonical_order(
            self.implementations,
            key=lambda item: _canonical_ref_key(item.implementation),
            name="availability implementations",
        )
        return self


def _validate_proposal_value(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _proposal_key_is_forbidden(key):
                raise ValueError("proposal cannot contain implementation or access configuration")
            _validate_proposal_value(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_proposal_value(nested)


class PlanProposalV1(_ClosedModel):
    schema_version: Literal[1] = 1
    method_id: MethodId
    method_version: SemVer
    financial_inputs: JsonObject
    conventions: JsonObject
    agent_rationale: GovernanceText | None = None

    @model_validator(mode="after")
    def validate_proposal(self) -> PlanProposalV1:
        if set(self.financial_inputs) & set(self.conventions):
            raise ValueError("financial inputs and conventions must use distinct fields")
        for name, values in (
            ("financial_inputs", self.financial_inputs),
            ("conventions", self.conventions),
        ):
            if any(re.fullmatch(_SAFE_ID_PATTERN, field) is None for field in values):
                raise ValueError(f"proposal {name} keys must be safe field IDs")
            canonical_json_bytes(values)
            _validate_proposal_value(values)
        return self


class ResolutionQuestionV1(_ClosedModel):
    question_id: SafeId
    field_path: Annotated[str, Field(pattern=r"^conventions\.[a-z][a-z0-9_]{0,63}$")]
    reason_code: Literal["required_convention_missing"] = "required_convention_missing"
    description: GovernanceText
    allowed_values: tuple[JsonValue, ...] = ()

    @model_validator(mode="after")
    def validate_allowed_values(self) -> ResolutionQuestionV1:
        for value in self.allowed_values:
            canonical_json_bytes(value)
        return self


class CandidateRejectionCode(StrEnum):
    CAPABILITY_IDENTITY_MISMATCH = "capability_identity_mismatch"
    SEMANTIC_PORT_MISMATCH = "semantic_port_mismatch"
    FORBIDDEN_BY_POLICY = "forbidden_by_policy"
    TRUST_REQUIREMENT_UNSATISFIED = "trust_requirement_unsatisfied"
    TRANSPORT_FORBIDDEN = "transport_forbidden"
    IMPLEMENTATION_UNAVAILABLE = "implementation_unavailable"


class CandidateResolutionV1(_ClosedModel):
    implementation: ImplementationRefV1
    priority: Annotated[int, Field(strict=True, ge=0, le=1_000_000)] | None = None
    capability_match: bool
    semantic_ports_match: bool
    policy_allowed: bool
    trust_requirements_satisfied: bool
    transport_allowed: bool
    availability: ImplementationAvailabilityV1
    rejection_reasons: tuple[CandidateRejectionCode, ...] = ()
    selected: bool

    @model_validator(mode="after")
    def validate_candidate(self) -> CandidateResolutionV1:
        if self.availability.implementation != self.implementation:
            raise ValueError("candidate availability must bind the same implementation")
        if tuple(sorted(self.rejection_reasons, key=lambda item: item.value)) != (
            self.rejection_reasons
        ):
            raise ValueError("candidate rejection reasons must use canonical order")
        if len(set(self.rejection_reasons)) != len(self.rejection_reasons):
            raise ValueError("candidate rejection reasons must be unique")
        expected_rejections: list[CandidateRejectionCode] = []
        if not self.capability_match:
            expected_rejections.append(CandidateRejectionCode.CAPABILITY_IDENTITY_MISMATCH)
        if not self.semantic_ports_match:
            expected_rejections.append(CandidateRejectionCode.SEMANTIC_PORT_MISMATCH)
        if not self.policy_allowed:
            expected_rejections.append(CandidateRejectionCode.FORBIDDEN_BY_POLICY)
        if not self.trust_requirements_satisfied:
            expected_rejections.append(
                CandidateRejectionCode.TRUST_REQUIREMENT_UNSATISFIED
            )
        if not self.transport_allowed:
            expected_rejections.append(CandidateRejectionCode.TRANSPORT_FORBIDDEN)
        if self.availability.status != AvailabilityStatus.AVAILABLE:
            expected_rejections.append(CandidateRejectionCode.IMPLEMENTATION_UNAVAILABLE)
        if self.rejection_reasons != tuple(
            sorted(expected_rejections, key=lambda item: item.value)
        ):
            raise ValueError("candidate rejection reasons must exactly match eligibility facts")
        if (self.priority is not None) != self.policy_allowed:
            raise ValueError("candidate priority must occur exactly when policy allows it")
        if self.selected and self.rejection_reasons:
            raise ValueError("selected candidate cannot contain rejection reasons")
        if self.selected and self.priority is None:
            raise ValueError("selected candidate requires a policy priority")
        return self


class ResolutionReceiptV1(_ClosedModel):
    schema_version: Literal[1] = 1
    step_id: SafeId
    capability: CapabilityRefV1
    relevant_candidates: tuple[CandidateResolutionV1, ...] = Field(min_length=1)
    selected_implementation: ImplementationRefV1
    selected_policy_priority: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
    satisfied_trust_dimensions: tuple[TrustDimension, ...] = Field(min_length=1)
    algorithm_version: Literal["governed_resolution_v1"] = "governed_resolution_v1"
    runtime_fallback_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_resolution(self) -> ResolutionReceiptV1:
        _require_unique(
            self.relevant_candidates,
            key=lambda item: item.implementation.implementation_hash,
            name="resolution relevant candidates",
        )
        _require_canonical_order(
            self.relevant_candidates,
            key=lambda item: _canonical_ref_key(item.implementation),
            name="resolution relevant candidates",
        )
        candidate_hashes = {
            item.implementation.implementation_hash for item in self.relevant_candidates
        }
        if self.selected_implementation.implementation_hash not in candidate_hashes:
            raise ValueError("selected implementation must be a relevant candidate")
        selected = tuple(item for item in self.relevant_candidates if item.selected)
        if len(selected) != 1 or selected[0].implementation != self.selected_implementation:
            raise ValueError("resolution receipt requires one exact selected implementation")
        if selected[0].priority != self.selected_policy_priority:
            raise ValueError("selected policy priority must match the selected candidate")
        if selected[0].availability.status != AvailabilityStatus.AVAILABLE:
            raise ValueError("selected implementation must be available")
        if (
            tuple(sorted(self.satisfied_trust_dimensions, key=lambda item: item.value))
            != self.satisfied_trust_dimensions
        ):
            raise ValueError("satisfied trust dimensions must use canonical order")
        if len(set(self.satisfied_trust_dimensions)) != len(
            self.satisfied_trust_dimensions
        ):
            raise ValueError("satisfied trust dimensions must be unique")
        return self


class CompiledPlanV1(_ClosedModel):
    status: Literal["compiled"] = "compiled"
    schema_version: Literal[1] = 1
    algorithm_version: Literal["governed_resolution_v1"] = "governed_resolution_v1"
    claims: tuple[
        Literal["PLAN VALIDATION PASSED"],
        Literal["ELIGIBLE UNDER POLICY"],
    ] = ("PLAN VALIDATION PASSED", "ELIGIBLE UNDER POLICY")
    method: MethodRefV1
    policy: ResolutionPolicyRefV1
    resolved_financial_inputs: JsonObject
    resolved_conventions: JsonObject
    applied_defaults: tuple[AppliedDefaultV1, ...] = ()
    capabilities: tuple[CapabilityRefV1, ...] = Field(min_length=1)
    resolution_receipts: tuple[ResolutionReceiptV1, ...] = Field(min_length=1)
    agent_rationale: GovernanceText | None = None

    @model_validator(mode="after")
    def validate_plan(self) -> CompiledPlanV1:
        if set(self.resolved_financial_inputs) & set(self.resolved_conventions):
            raise ValueError("resolved financial inputs and conventions must use distinct fields")
        canonical_json_bytes(self.resolved_financial_inputs)
        canonical_json_bytes(self.resolved_conventions)
        _require_unique(self.applied_defaults, key=lambda item: item.field, name="applied defaults")
        _require_canonical_order(
            self.applied_defaults, key=lambda item: item.field, name="applied defaults"
        )
        for default in self.applied_defaults:
            resolved = (
                self.resolved_conventions
                if default.field in self.resolved_conventions
                else self.resolved_financial_inputs
            )
            if default.field not in resolved:
                raise ValueError("applied defaults must resolve input or convention fields")
            if canonical_json_bytes(default.value) != canonical_json_bytes(resolved[default.field]):
                raise ValueError("applied default value must match the resolved convention")

        _require_unique(
            self.capabilities,
            key=lambda item: item.capability_hash,
            name="compiled capabilities",
        )
        _require_canonical_order(
            self.capabilities, key=_canonical_ref_key, name="compiled capabilities"
        )
        _require_unique(
            self.resolution_receipts,
            key=lambda item: item.step_id,
            name="resolution receipts",
        )
        _require_canonical_order(
            self.resolution_receipts,
            key=lambda item: item.step_id,
            name="resolution receipts",
        )
        capability_hashes = {item.capability_hash for item in self.capabilities}
        if any(
            receipt.capability.capability_hash not in capability_hashes
            for receipt in self.resolution_receipts
        ):
            raise ValueError("resolution receipt capability must be declared by the plan")
        if {
            receipt.capability.capability_hash for receipt in self.resolution_receipts
        } != capability_hashes:
            raise ValueError("compiled capabilities must all have resolution receipts")
        return self

    def semantic_projection(self) -> dict[str, Any]:
        """Return exact plan meaning without non-authoritative agent rationale."""

        return {
            "status": self.status,
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "claims": list(self.claims),
            "method": self.method.model_dump(mode="json"),
            "policy": self.policy.model_dump(mode="json"),
            "resolved_financial_inputs": self.resolved_financial_inputs,
            "resolved_conventions": self.resolved_conventions,
            "applied_defaults": [item.model_dump(mode="json") for item in self.applied_defaults],
            "capabilities": [item.model_dump(mode="json") for item in self.capabilities],
            "resolution_receipts": [
                item.model_dump(mode="json") for item in self.resolution_receipts
            ],
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def plan_hash(self) -> str:
        return canonical_hash(self.semantic_projection(), domain=COMPILED_PLAN_HASH_DOMAIN)


class NeedsInformationV1(_ClosedModel):
    status: Literal["needs_information"] = "needs_information"
    schema_version: Literal[1] = 1
    proposal: PlanProposalV1
    questions: tuple[ResolutionQuestionV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_questions(self) -> NeedsInformationV1:
        _require_unique(
            self.questions, key=lambda item: item.question_id, name="resolution questions"
        )
        _require_unique(
            self.questions,
            key=lambda item: item.field_path,
            name="resolution question fields",
        )
        _require_canonical_order(
            self.questions,
            key=lambda item: (item.field_path, item.question_id),
            name="resolution questions",
        )
        return self


class PlanRefusalV1(_ClosedModel):
    status: Literal["refused"] = "refused"
    schema_version: Literal[1] = 1
    proposal: PlanProposalV1
    code: PlanRefusalCode
    message: GovernanceText
    fields: tuple[SafeId, ...] = ()

    @model_validator(mode="after")
    def validate_fields(self) -> PlanRefusalV1:
        if tuple(sorted(self.fields)) != self.fields:
            raise ValueError("refusal fields must use canonical order")
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("refusal fields must be unique")
        return self


GovernanceOutcomeV1: TypeAlias = Annotated[
    CompiledPlanV1 | NeedsInformationV1 | PlanRefusalV1,
    Field(discriminator="status"),
]


__all__ = [
    "AppliedDefaultV1",
    "AvailabilityReason",
    "AvailabilitySnapshotV1",
    "AvailabilityStatus",
    "CapabilityKind",
    "CapabilityRefV1",
    "CapabilityResolutionRuleV1",
    "CapabilitySpecV1",
    "CompiledPlanV1",
    "CandidateRejectionCode",
    "CandidateResolutionV1",
    "ConstraintComparisonV1",
    "ConstraintConditionV1",
    "ConstraintMeasure",
    "ConstraintOperandV1",
    "ConstraintOperator",
    "ConstraintSeverity",
    "ConstraintSpecV1",
    "ConventionSpecV1",
    "DefaultSpecV1",
    "GovernanceOutcomeV1",
    "ImplementationAvailabilityV1",
    "ImplementationRefV1",
    "ImplementationSpecV1",
    "ImplementationTransport",
    "MethodRefV1",
    "MethodSpecV1",
    "NamedPortV1",
    "NeedsInformationV1",
    "PlanProposalV1",
    "PlanRefusalCode",
    "PlanRefusalV1",
    "PolicyImplementationV1",
    "RecipeDagV1",
    "RecipeInputBindingV1",
    "RecipeStepV1",
    "RecipeValueSourceV1",
    "ResolutionPolicyRefV1",
    "ResolutionPolicyV1",
    "ResolutionQuestionV1",
    "ResolutionReceiptV1",
    "TransportLocality",
    "TransportMetadataV1",
    "TrustDimension",
    "TrustedAdapterV1",
]
