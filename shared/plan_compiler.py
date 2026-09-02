"""Pure deterministic compiler for backend-neutral governed financial plans."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from defined_quant.types import ContractEvaluationError
from defined_quant.validation import evaluate_expression
from defined_quant_protocol import (
    AppliedDefaultV1,
    AvailabilitySnapshotV1,
    CandidateResolutionV1,
    CapabilitySpecV1,
    CompiledPlanV1,
    ConstraintConditionV1,
    GovernanceOutcomeV1,
    ImplementationAvailabilityV1,
    ImplementationSpecV1,
    MethodSpecV1,
    NeedsInformationV1,
    PlanProposalV1,
    PlanRefusalV1,
    PortProvenanceRequirement,
    ResolutionPolicyV1,
    ResolutionQuestionV1,
    ResolutionReceiptV1,
    SemanticPort,
    canonical_json_bytes,
)
from defined_quant_protocol.governance import (
    AvailabilityReason,
    AvailabilityStatus,
    CandidateRejectionCode,
    PlanRefusalCode,
)
from pydantic import JsonValue

RESOLUTION_ALGORITHM_VERSION = "governed_resolution_v1"
MAX_COMPILER_CONTAINER_DEPTH = 32
MAX_COMPILER_SEQUENCE_ITEMS = 4096
_RFC3339_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.\d{1,9})?(?:[Zz]|([+-])(\d{2}):(\d{2}))$",
    re.ASCII,
)
_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "default",
        "description",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "x-defined-quant-port",
    }
)
_SCHEMA_METADATA_KEYS = frozenset(
    {"default", "description", "examples", "title", "x-defined-quant-port"}
)


def _refusal(
    proposal: PlanProposalV1,
    code: PlanRefusalCode,
    message: str,
    *,
    fields: Sequence[str] = (),
) -> PlanRefusalV1:
    return PlanRefusalV1(
        proposal=proposal,
        code=code,
        message=message,
        fields=tuple(sorted(set(fields))),
    )


def _schema_is_supported(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> bool:
    if set(schema) - _SUPPORTED_SCHEMA_KEYS:
        return False
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            return False
        if reference in seen_refs:
            return False
        definitions = root.get("$defs")
        if not isinstance(definitions, Mapping):
            return False
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping) or not _schema_is_supported(
            target,
            root,
            seen_refs=seen_refs | {reference},
        ):
            return False
    definitions = schema.get("$defs")
    if definitions is not None:
        if not isinstance(definitions, Mapping) or any(
            not isinstance(name, str)
            or not isinstance(value, Mapping)
            or not _schema_is_supported(value, root)
            for name, value in definitions.items()
        ):
            return False
    branches = schema.get("anyOf")
    if branches is not None:
        if (
            not isinstance(branches, list)
            or not branches
            or any(
                not isinstance(branch, Mapping) or not _schema_is_supported(branch, root)
                for branch in branches
            )
        ):
            return False
    declared_type = schema.get("type")
    if declared_type is not None and declared_type not in {
        "array",
        "boolean",
        "integer",
        "null",
        "number",
        "object",
        "string",
    }:
        return False
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping) or any(
            not isinstance(field, str)
            or not isinstance(value, Mapping)
            or not _schema_is_supported(value, root)
            for field, value in properties.items()
        ):
            return False
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or any(not isinstance(field, str) for field in required)
        or len(set(required)) != len(required)
    ):
        return False
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        return False
    items = schema.get("items")
    if items is not None and (
        not isinstance(items, Mapping) or not _schema_is_supported(items, root)
    ):
        return False
    format_name = schema.get("format")
    if format_name is not None and format_name != "date-time":
        return False
    pattern = schema.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            return False
        try:
            re.compile(pattern)
        except re.error:
            return False
    integer_keywords = ("minItems", "maxItems", "minLength", "maxLength")
    if any(
        key in schema
        and (
            isinstance(schema[key], bool)
            or not isinstance(schema[key], int)
            or schema[key] < 0
        )
        for key in integer_keywords
    ):
        return False
    numeric_keywords = (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    )
    if any(
        key in schema
        and (
            isinstance(schema[key], bool)
            or not isinstance(schema[key], int | float)
            or not math.isfinite(float(schema[key]))
        )
        for key in numeric_keywords
    ):
        return False
    for key in ("const", "default"):
        if key in schema:
            try:
                canonical_json_bytes(schema[key])
            except (TypeError, ValueError):
                return False
    allowed = schema.get("enum")
    if allowed is not None and (
        not isinstance(allowed, list)
        or not allowed
        or len({canonical_json_bytes(item) for item in allowed}) != len(allowed)
    ):
        return False
    return True


def _valid_rfc3339_datetime(value: str) -> bool:
    matched = _RFC3339_DATETIME.fullmatch(value)
    if matched is None:
        return False
    year, month, day, hour, minute, second = (
        int(matched.group(index)) for index in range(1, 7)
    )
    if year == 0 or month not in range(1, 13) or hour > 23 or minute > 59 or second > 59:
        return False
    month_lengths = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if day == 0 or day > month_lengths[month - 1]:
        return False
    offset_hour = matched.group(8)
    offset_minute = matched.group(9)
    return not (
        offset_hour is not None
        and offset_minute is not None
        and (int(offset_hour) > 23 or int(offset_minute) > 59)
    )


def _schema_matches(value: JsonValue, schema: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definitions = root.get("$defs")
        if not isinstance(definitions, Mapping):
            return False
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping) or not _schema_matches(value, target, root):
            return False
    branches = schema.get("anyOf")
    if isinstance(branches, list):
        if not any(
            isinstance(branch, Mapping) and _schema_matches(value, branch, root)
            for branch in branches
        ):
            return False
    if "const" in schema and canonical_json_bytes(value) != canonical_json_bytes(
        schema["const"]
    ):
        return False
    allowed = schema.get("enum")
    if isinstance(allowed, list) and canonical_json_bytes(value) not in {
        canonical_json_bytes(item) for item in allowed
    }:
        return False

    declared_type = schema.get("type")
    if declared_type == "null":
        return value is None
    if declared_type == "boolean":
        return isinstance(value, bool)
    if declared_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif declared_type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        if not math.isfinite(float(value)):
            return False
    elif declared_type == "string":
        if not isinstance(value, str):
            return False
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return False
        if schema.get("format") == "date-time" and not _valid_rfc3339_datetime(value):
            return False
    elif declared_type == "array":
        if not isinstance(value, list):
            return False
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping) and any(
            not _schema_matches(item, item_schema, root) for item in value
        ):
            return False
    elif declared_type == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            return False
        if any(field not in value for field in required):
            return False
        if schema.get("additionalProperties") is False and any(
            field not in properties for field in value
        ):
            return False
        for field, item in value.items():
            property_schema = properties.get(field)
            if isinstance(property_schema, Mapping) and not _schema_matches(
                item, property_schema, root
            ):
                return False

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, int | float) and value < minimum:
            return False
        if isinstance(maximum, int | float) and value > maximum:
            return False
        if isinstance(exclusive_minimum, int | float) and value <= exclusive_minimum:
            return False
        if isinstance(exclusive_maximum, int | float) and value >= exclusive_maximum:
            return False
    return True


def _constraint_validation_value(
    value: JsonValue,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
) -> Any:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        definitions = root.get("$defs")
        if not isinstance(definitions, Mapping):
            raise ValueError("schema reference cannot be resolved")
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping):
            raise ValueError("schema reference cannot be resolved")
        return _constraint_validation_value(value, target, root)
    branches = schema.get("anyOf")
    if isinstance(branches, list):
        selected = next(
            (
                branch
                for branch in branches
                if isinstance(branch, Mapping) and _schema_matches(value, branch, root)
            ),
            None,
        )
        if selected is None:
            raise ValueError("value does not match a supported schema branch")
        return _constraint_validation_value(value, selected, root)
    if schema.get("type") == "string" and schema.get("format") == "date-time":
        if not isinstance(value, str) or not _valid_rfc3339_datetime(value):
            raise ValueError("date-time value is invalid")
        normalized = value.replace("Z", "+00:00").replace("z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(UTC)
    if schema.get("type") == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            return [
                _constraint_validation_value(item, item_schema, root) for item in value
            ]
    if schema.get("type") == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            result: dict[str, Any] = {}
            for field, item in value.items():
                selected = properties.get(field)
                result[field] = (
                    _constraint_validation_value(item, selected, root)
                    if isinstance(selected, Mapping)
                    else item
                )
            return result
    return value


def _constraint_validation_inputs(
    values: Mapping[str, JsonValue],
    method: MethodSpecV1,
) -> dict[str, Any]:
    properties = method.input_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError("method input schema has no properties")
    result: dict[str, Any] = {}
    for field, value in values.items():
        selected = properties.get(field)
        result[field] = (
            _constraint_validation_value(value, selected, method.input_schema)
            if isinstance(selected, Mapping)
            else value
        )
    return result


def _invalid_input_fields(
    values: Mapping[str, JsonValue],
    method: MethodSpecV1,
    *,
    allowed_missing: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    schema = method.input_schema
    properties = schema.get("properties")
    required = schema.get("required", [])
    if (
        not isinstance(properties, Mapping)
        or not isinstance(required, list)
        or any(not isinstance(field, str) for field in required)
    ):
        return tuple(sorted(values))
    required_fields = cast(list[str], required)
    def bounded(value: JsonValue, *, depth: int = 0) -> bool:
        if depth > MAX_COMPILER_CONTAINER_DEPTH:
            return False
        if isinstance(value, list):
            return len(value) <= MAX_COMPILER_SEQUENCE_ITEMS and all(
                bounded(item, depth=depth + 1) for item in value
            )
        if isinstance(value, dict):
            return len(value) <= MAX_COMPILER_SEQUENCE_ITEMS and all(
                bounded(item, depth=depth + 1) for item in value.values()
            )
        return True

    invalid = {
        field
        for field, value in values.items()
        if field not in properties or not bounded(value)
    }
    invalid.update(
        field
        for field in required_fields
        if field not in values and field not in allowed_missing
    )
    for field, value in values.items():
        selected = properties.get(field)
        if isinstance(selected, Mapping) and not _schema_matches(value, selected, schema):
            invalid.add(field)
    return tuple(sorted(invalid))


def _condition_expression(condition: ConstraintConditionV1) -> dict[str, Any]:
    if condition.all_of:
        return {"all": [_condition_expression(item) for item in condition.all_of]}
    if condition.any_of:
        return {"any": [_condition_expression(item) for item in condition.any_of]}
    comparison = condition.comparison
    if comparison is None:
        raise ValueError("constraint comparison is missing")

    def operand(item: Any) -> dict[str, Any]:
        if item.field is None:
            return {"value": item.value}
        result: dict[str, Any] = {"field": item.field}
        if item.measure is not None:
            result["measure"] = item.measure.value
        return result

    return {
        "left": operand(comparison.left),
        "op": comparison.operator.value,
        "right": operand(comparison.right),
    }


def _ports_match(implementation: ImplementationSpecV1, capability: CapabilitySpecV1) -> bool:
    return bool(
        implementation.input_ports == capability.input_ports
        and implementation.output_ports == capability.output_ports
    )


def _producer_satisfies_consumer(producer: SemanticPort, consumer: SemanticPort) -> bool:
    for field in (
        "concept",
        "unit",
        "shape",
        "cardinality",
        "convention",
        "ordering",
        "frequency",
    ):
        if getattr(producer, field) != getattr(consumer, field):
            return False
    requirement = consumer.provenance_requirement
    if requirement == PortProvenanceRequirement.NOT_REQUIRED:
        return True
    if requirement == PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND:
        return producer.provenance_requirement in {
            PortProvenanceRequirement.SOURCE_BOUND,
            PortProvenanceRequirement.COMPONENT_BOUND,
            PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND,
        }
    return bool(producer.provenance_requirement == requirement)


def _validation_schema_projection(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> JsonValue:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        definitions = root.get("$defs")
        if not isinstance(definitions, Mapping) or reference in seen_refs:
            raise ValueError("schema reference cannot be resolved")
        target = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, Mapping):
            raise ValueError("schema reference cannot be resolved")
        target_projection = _validation_schema_projection(
            target,
            root,
            seen_refs=seen_refs | {reference},
        )
        siblings = {
            key: value
            for key, value in schema.items()
            if key != "$ref" and key not in _SCHEMA_METADATA_KEYS
        }
        if not siblings:
            return target_projection
        return {
            "referenced": target_projection,
            "siblings": _validation_schema_projection(siblings, root),
        }

    projected: dict[str, JsonValue] = {}
    for key in sorted(schema):
        if key in _SCHEMA_METADATA_KEYS or key == "$defs":
            continue
        value = schema[key]
        if key == "properties" and isinstance(value, Mapping):
            projected[key] = {
                field: _validation_schema_projection(item, root)
                for field, item in sorted(value.items())
                if isinstance(field, str) and isinstance(item, Mapping)
            }
        elif key == "items" and isinstance(value, Mapping):
            projected[key] = _validation_schema_projection(value, root)
        elif key == "anyOf" and isinstance(value, list):
            projected[key] = [
                _validation_schema_projection(item, root)
                for item in value
                if isinstance(item, Mapping)
            ]
        else:
            projected[key] = cast(JsonValue, value)
    return projected


def _field_schema(root: Mapping[str, Any], field: str) -> Mapping[str, Any] | None:
    properties = root.get("properties")
    if not isinstance(properties, Mapping):
        return None
    selected = properties.get(field)
    return selected if isinstance(selected, Mapping) else None


def _field_schemas_match(
    producer: Mapping[str, Any],
    producer_root: Mapping[str, Any],
    consumer: Mapping[str, Any],
    consumer_root: Mapping[str, Any],
) -> bool:
    try:
        return canonical_json_bytes(
            _validation_schema_projection(producer, producer_root)
        ) == canonical_json_bytes(_validation_schema_projection(consumer, consumer_root))
    except (TypeError, ValueError):
        return False


def _recipe_is_compatible(
    method: MethodSpecV1,
    capabilities: Mapping[str, CapabilitySpecV1],
) -> bool:
    if not _schema_is_supported(method.input_schema, method.input_schema) or not (
        _schema_is_supported(method.output_schema, method.output_schema)
    ):
        return False
    method_input_ports = {item.field: item.port for item in method.input_ports}
    method_output_ports = {item.field: item.port for item in method.output_ports}
    method_input_properties = method.input_schema.get("properties")
    if not isinstance(method_input_properties, Mapping):
        return False
    consumed_method_inputs: set[str] = set()
    capability_by_step: dict[str, CapabilitySpecV1] = {}
    for step in method.recipe.steps:
        capability = capabilities.get(step.capability_id)
        if capability is None:
            return False
        if not _schema_is_supported(
            capability.input_schema, capability.input_schema
        ) or not _schema_is_supported(capability.output_schema, capability.output_schema):
            return False
        capability_by_step[step.step_id] = capability
        input_properties = capability.input_schema.get("properties")
        output_properties = capability.output_schema.get("properties")
        if not isinstance(input_properties, Mapping) or not isinstance(output_properties, Mapping):
            return False
        bound_targets = {binding.target_field for binding in step.input_bindings}
        if bound_targets != set(input_properties):
            return False
        if any(field not in output_properties for field in step.output_fields):
            return False
        consumer_ports = {item.field: item.port for item in capability.input_ports}
        for binding in step.input_bindings:
            consumer = consumer_ports.get(binding.target_field)
            consumer_schema = _field_schema(
                capability.input_schema,
                binding.target_field,
            )
            if consumer_schema is None:
                return False
            source = binding.source
            if source.source == "method_input":
                consumed_method_inputs.add(source.field)
                producer_like = method_input_ports.get(source.field)
                producer_schema = _field_schema(method.input_schema, source.field)
                if producer_schema is None or not _field_schemas_match(
                    producer_schema,
                    method.input_schema,
                    consumer_schema,
                    capability.input_schema,
                ):
                    return False
                if (consumer is None) != (producer_like is None):
                    return False
                if (
                    consumer is not None
                    and producer_like is not None
                    and producer_like.model_copy(update={"direction": "output"})
                    != consumer.model_copy(update={"direction": "output"})
                ):
                    return False
                continue
            if source.step_id is None:
                return False
            producer_capability = capability_by_step.get(source.step_id)
            if producer_capability is None:
                return False
            producer_schema = _field_schema(producer_capability.output_schema, source.field)
            if producer_schema is None or not _field_schemas_match(
                producer_schema,
                producer_capability.output_schema,
                consumer_schema,
                capability.input_schema,
            ):
                return False
            producer = next(
                (
                    item.port
                    for item in producer_capability.output_ports
                    if item.field == source.field
                ),
                None,
            )
            if (consumer is None) != (producer is None):
                return False
            if (
                consumer is not None
                and producer is not None
                and not _producer_satisfies_consumer(producer, consumer)
            ):
                return False

    if consumed_method_inputs != set(method_input_properties):
        return False

    method_output_properties = method.output_schema.get("properties")
    if not isinstance(method_output_properties, Mapping):
        return False
    providers: dict[str, list[CapabilitySpecV1]] = {
        field: [] for field in method_output_properties
    }
    for result_step in method.recipe.result_steps:
        capability = capability_by_step[result_step]
        step = next(item for item in method.recipe.steps if item.step_id == result_step)
        for field in step.output_fields:
            if field in providers:
                providers[field].append(capability)
    for field, method_schema in method_output_properties.items():
        if not isinstance(method_schema, Mapping) or len(providers[field]) != 1:
            return False
        capability = providers[field][0]
        capability_schema = _field_schema(capability.output_schema, field)
        if capability_schema is None or not _field_schemas_match(
            capability_schema,
            capability.output_schema,
            method_schema,
            method.output_schema,
        ):
            return False
        expected = method_output_ports.get(field)
        actual = next(
            (item.port for item in capability.output_ports if item.field == field),
            None,
        )
        if (expected is None) != (actual is None):
            return False
        if expected is not None and actual != expected:
            return False
    return True


def compile_plan(
    proposal: PlanProposalV1,
    method: MethodSpecV1,
    capabilities: Sequence[CapabilitySpecV1],
    implementations: Sequence[ImplementationSpecV1],
    policy: ResolutionPolicyV1,
    availability: AvailabilitySnapshotV1,
) -> GovernanceOutcomeV1:
    """Compile one proposal using only the complete explicit data arguments supplied here."""

    if proposal.method_id != method.id or proposal.method_version != method.version:
        return _refusal(
            proposal,
            PlanRefusalCode.METHOD_IDENTITY_MISMATCH,
            "The requested method identity does not match the selected method specification.",
        )
    if policy.method != method.ref:
        return _refusal(
            proposal,
            PlanRefusalCode.POLICY_BINDING_MISMATCH,
            "The configured policy does not bind the selected method specification.",
        )
    if not _schema_is_supported(method.input_schema, method.input_schema) or not (
        _schema_is_supported(method.output_schema, method.output_schema)
    ):
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_GOVERNANCE_RECORD,
            "The method uses JSON Schema features outside the closed V1 validator.",
        )
    capability_identities = tuple((item.id, item.version) for item in capabilities)
    implementation_identities = tuple((item.id, item.version) for item in implementations)
    if (
        len(set(capability_identities)) != len(capability_identities)
        or len({item.capability_hash for item in capabilities}) != len(capabilities)
        or len(set(implementation_identities)) != len(implementation_identities)
        or len({item.implementation_hash for item in implementations})
        != len(implementations)
    ):
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_GOVERNANCE_RECORD,
            "The supplied registry records contain duplicate identities.",
        )

    convention_by_field = {item.field: item for item in method.conventions}
    convention_fields = set(convention_by_field)
    invalid_conventions: set[str] = set(proposal.conventions) - convention_fields
    misplaced_conventions: set[str] = set(proposal.financial_inputs) & convention_fields
    if invalid_conventions or misplaced_conventions:
        return _refusal(
            proposal,
            PlanRefusalCode.CONVENTION_CONFLICT,
            "Convention values must use only the method's declared convention fields.",
            fields=tuple(invalid_conventions | misplaced_conventions),
        )

    resolved_financial_inputs = dict(proposal.financial_inputs)
    resolved_conventions = dict(proposal.conventions)
    applied_defaults: list[AppliedDefaultV1] = []
    for default in method.defaults:
        target = (
            resolved_conventions
            if default.field in convention_fields
            else resolved_financial_inputs
        )
        if default.field not in target:
            target[default.field] = default.value
            applied_defaults.append(
                AppliedDefaultV1(
                    field=default.field,
                    value=default.value,
                    rationale=default.rationale,
                )
            )

    missing = tuple(
        sorted(
            (
                convention
                for convention in method.conventions
                if convention.required_from_user and convention.field not in resolved_conventions
            ),
            key=lambda item: (item.field, item.id),
        )
    )
    combined: dict[str, JsonValue] = {
        **resolved_financial_inputs,
        **resolved_conventions,
    }
    invalid_fields = _invalid_input_fields(
        combined,
        method,
        allowed_missing=frozenset(item.field for item in missing),
    )
    if invalid_fields:
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_FINANCIAL_INPUT,
            "The structured financial inputs do not satisfy the method input schema.",
            fields=invalid_fields,
        )
    invalid_convention_values = tuple(
        sorted(
            item.field
            for item in method.conventions
            if item.field in resolved_conventions
            and item.allowed_values
            and canonical_json_bytes(resolved_conventions[item.field])
            not in {canonical_json_bytes(value) for value in item.allowed_values}
        )
    )
    if invalid_convention_values:
        return _refusal(
            proposal,
            PlanRefusalCode.CONVENTION_CONFLICT,
            "One or more convention values are outside the method's allowed values.",
            fields=invalid_convention_values,
        )
    if missing:
        return NeedsInformationV1(
            proposal=proposal,
            questions=tuple(
                ResolutionQuestionV1(
                    question_id=item.id,
                    field_path=f"conventions.{item.field}",
                    description=item.question,
                    allowed_values=item.allowed_values,
                )
                for item in missing
            ),
        )

    try:
        validation_inputs = _constraint_validation_inputs(combined, method)
        triggered = tuple(
            item
            for item in method.constraints
            if evaluate_expression(
                _condition_expression(item.when),
                validation_inputs,
                component_id=method.id,
            )
        )
    except (ArithmeticError, ContractEvaluationError, TypeError, ValueError):
        return _refusal(
            proposal,
            PlanRefusalCode.INVALID_GOVERNANCE_RECORD,
            "The selected method constraints could not be evaluated deterministically.",
        )
    blocking = tuple(item for item in triggered if item.severity.value == "blocking")
    if blocking:
        return _refusal(
            proposal,
            PlanRefusalCode.CONSTRAINT_VIOLATION,
            "One or more blocking method constraints were triggered.",
        )

    capability_by_hash = {item.capability_hash: item for item in capabilities}
    capability_by_id = {item.id: item for item in capabilities if item.ref in method.capabilities}
    if not _recipe_is_compatible(method, capability_by_id):
        return _refusal(
            proposal,
            PlanRefusalCode.INCOMPATIBLE_IMPLEMENTATION,
            "The method recipe has incompatible typed bindings or semantic ports.",
        )
    rule_by_hash = {item.capability.capability_hash: item for item in policy.capability_rules}
    availability_by_hash = {
        item.implementation.implementation_hash: item for item in availability.implementations
    }
    receipts: list[ResolutionReceiptV1] = []
    used_capabilities: dict[str, Any] = {}

    for step in method.recipe.steps:
        declared = next(
            (item for item in method.capabilities if item.id == step.capability_id),
            None,
        )
        capability = None if declared is None else capability_by_hash.get(declared.capability_hash)
        if capability is None or capability.ref != declared:
            return _refusal(
                proposal,
                PlanRefusalCode.CAPABILITY_UNAVAILABLE,
                "A recipe capability is not present with its exact registered identity.",
            )
        rule = rule_by_hash.get(capability.capability_hash)
        if rule is None or rule.capability != capability.ref:
            return _refusal(
                proposal,
                PlanRefusalCode.NO_APPROVED_IMPLEMENTATION,
                "The configured policy has no exact rule for a recipe capability.",
            )

        relevant = tuple(
            sorted(
                (
                    item
                    for item in implementations
                    if item.capability == capability.ref
                    and _ports_match(item, capability)
                ),
                key=lambda item: (item.id, item.version, item.implementation_hash),
            )
        )
        if not relevant:
            return _refusal(
                proposal,
                PlanRefusalCode.CAPABILITY_UNAVAILABLE,
                "No registered implementation is relevant to a recipe capability.",
            )
        policy_by_hash = {
            item.implementation.implementation_hash: item for item in rule.implementations
        }
        candidates: list[CandidateResolutionV1] = []
        eligible: list[tuple[int, bytes, ImplementationSpecV1]] = []
        for implementation in relevant:
            capability_match = True
            semantic_ports_match = True
            policy_entry = policy_by_hash.get(implementation.implementation_hash)
            policy_allowed = (
                policy_entry is not None and policy_entry.implementation == implementation.ref
            )
            required_dimensions = set(rule.required_trust_dimensions)
            trust_satisfied = required_dimensions <= set(
                implementation.trust_dimensions
            )
            transport_allowed = implementation.transport.kind in rule.allowed_transports
            availability_entry = availability_by_hash.get(implementation.implementation_hash)
            if (
                availability_entry is None
                or availability_entry.implementation != implementation.ref
            ):
                availability_entry = ImplementationAvailabilityV1(
                    implementation=implementation.ref,
                    status=AvailabilityStatus.UNKNOWN,
                    reason=AvailabilityReason.STATUS_UNKNOWN,
                )
            rejection_reasons: list[CandidateRejectionCode] = []
            if not capability_match:
                rejection_reasons.append(CandidateRejectionCode.CAPABILITY_IDENTITY_MISMATCH)
            if not semantic_ports_match:
                rejection_reasons.append(CandidateRejectionCode.SEMANTIC_PORT_MISMATCH)
            if not policy_allowed:
                rejection_reasons.append(CandidateRejectionCode.FORBIDDEN_BY_POLICY)
            if not trust_satisfied:
                rejection_reasons.append(CandidateRejectionCode.TRUST_REQUIREMENT_UNSATISFIED)
            if not transport_allowed:
                rejection_reasons.append(CandidateRejectionCode.TRANSPORT_FORBIDDEN)
            if availability_entry.status != AvailabilityStatus.AVAILABLE:
                rejection_reasons.append(CandidateRejectionCode.IMPLEMENTATION_UNAVAILABLE)
            priority = (
                policy_entry.priority
                if policy_allowed and policy_entry is not None
                else None
            )
            if not rejection_reasons:
                assert priority is not None
                eligible.append((priority, implementation.id.encode("utf-8"), implementation))
            candidates.append(
                CandidateResolutionV1(
                    implementation=implementation.ref,
                    priority=priority,
                    capability_match=capability_match,
                    semantic_ports_match=semantic_ports_match,
                    policy_allowed=policy_allowed,
                    trust_requirements_satisfied=trust_satisfied,
                    transport_allowed=transport_allowed,
                    availability=availability_entry,
                    rejection_reasons=tuple(sorted(rejection_reasons, key=lambda item: item.value)),
                    selected=False,
                )
            )
        if not eligible:
            return _refusal(
                proposal,
                PlanRefusalCode.NO_APPROVED_IMPLEMENTATION,
                "No relevant implementation is eligible under policy and availability.",
            )
        _priority, _stable_id, selected = min(
            eligible,
            key=lambda item: (item[0], item[1]),
        )
        candidates = [
            CandidateResolutionV1.model_validate(
                {
                    **item.model_dump(mode="python"),
                    "selected": item.implementation == selected.ref,
                }
            )
            for item in candidates
        ]
        selected_policy = policy_by_hash[selected.implementation_hash]
        receipts.append(
            ResolutionReceiptV1(
                step_id=step.step_id,
                capability=capability.ref,
                relevant_candidates=tuple(
                    sorted(
                        candidates,
                        key=lambda item: (
                            item.implementation.id,
                            item.implementation.version,
                            item.implementation.implementation_hash,
                        ),
                    )
                ),
                selected_implementation=selected.ref,
                selected_policy_priority=selected_policy.priority,
                satisfied_trust_dimensions=tuple(
                    sorted(rule.required_trust_dimensions, key=lambda item: item.value)
                ),
            )
        )
        used_capabilities[capability.capability_hash] = capability.ref

    return CompiledPlanV1(
        method=method.ref,
        policy=policy.ref,
        resolved_financial_inputs={
            key: resolved_financial_inputs[key] for key in sorted(resolved_financial_inputs)
        },
        resolved_conventions={
            key: resolved_conventions[key] for key in sorted(resolved_conventions)
        },
        applied_defaults=tuple(sorted(applied_defaults, key=lambda item: item.field)),
        capabilities=tuple(
            sorted(
                used_capabilities.values(),
                key=lambda item: (item.id, item.version, item.capability_hash),
            )
        ),
        resolution_receipts=tuple(sorted(receipts, key=lambda item: item.step_id)),
        agent_rationale=proposal.agent_rationale,
    )


__all__ = ["RESOLUTION_ALGORITHM_VERSION", "compile_plan"]
