"""Immutable governed registries and legacy component compatibility projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from defined_quant_protocol import (
    CapabilitySpecV1,
    ComponentRef,
    ConstraintComparisonV1,
    ConstraintConditionV1,
    ConstraintMeasure,
    ConstraintOperandV1,
    ConstraintOperator,
    ConstraintSpecV1,
    ConventionSpecV1,
    DefaultSpecV1,
    ImplementationSpecV1,
    ImplementationTransport,
    MethodSpecV1,
    NamedPortV1,
    RecipeDagV1,
    RecipeInputBindingV1,
    RecipeStepV1,
    RecipeValueSourceV1,
    SemanticPort,
    TransportLocality,
    TransportMetadataV1,
    TrustedAdapterV1,
    canonical_json_bytes,
)
from defined_quant_protocol.governance import (
    CapabilityKind,
    ConstraintSeverity,
    TrustDimension,
)
from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class GovernedRegistry:
    """One immutable, deterministically ordered registry snapshot."""

    methods: tuple[MethodSpecV1, ...]
    capabilities: tuple[CapabilitySpecV1, ...]
    implementations: tuple[ImplementationSpecV1, ...]

    def __post_init__(self) -> None:
        for name, values in (
            ("methods", self.methods),
            ("capabilities", self.capabilities),
            ("implementations", self.implementations),
        ):
            expected = tuple(sorted(values, key=lambda item: (item.id, item.version)))
            if values != expected:
                raise ValueError(f"governed registry {name} must use canonical order")
            identities = tuple((item.id, item.version) for item in values)
            if len(set(identities)) != len(identities):
                raise ValueError(f"governed registry {name} identities must be unique")

        capability_by_hash = {item.capability_hash: item for item in self.capabilities}
        for method in self.methods:
            if any(
                reference.capability_hash not in capability_by_hash
                or reference != capability_by_hash[reference.capability_hash].ref
                for reference in method.capabilities
            ):
                raise ValueError("method references an unregistered capability")
        for implementation in self.implementations:
            capability = capability_by_hash.get(implementation.capability.capability_hash)
            if capability is None or implementation.capability != capability.ref:
                raise ValueError("implementation references an unregistered capability")

    def method(self, method_id: str, version: str) -> MethodSpecV1 | None:
        """Return one exact method without applying discovery or fuzzy matching."""

        return next(
            (item for item in self.methods if item.id == method_id and item.version == version),
            None,
        )


def _named_ports(
    values: Sequence[Mapping[str, Any]],
) -> tuple[NamedPortV1, ...]:
    return tuple(
        sorted(
            (
                NamedPortV1(
                    field=str(item["name"]),
                    port=SemanticPort.model_validate(item["semantic_port"]),
                )
                for item in values
            ),
            key=lambda item: item.field,
        )
    )


def _operand(value: Mapping[str, Any]) -> ConstraintOperandV1:
    if "value" in value:
        return ConstraintOperandV1(value=value["value"])
    measure = value.get("measure")
    return ConstraintOperandV1(
        field=value.get("field"),
        measure=(None if measure is None else ConstraintMeasure(measure)),
    )


def _condition(value: Mapping[str, Any]) -> ConstraintConditionV1:
    if set(value) == {"left", "op", "right"}:
        left = value["left"]
        right = value["right"]
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            raise ValueError("constraint operands must be objects")
        return ConstraintConditionV1(
            comparison=ConstraintComparisonV1(
                left=_operand(left),
                operator=ConstraintOperator(value["op"]),
                right=_operand(right),
            )
        )
    branch = "all" if set(value) == {"all"} else "any" if set(value) == {"any"} else None
    if branch is None or not isinstance(value[branch], Sequence):
        raise ValueError("constraint condition uses an unsupported shape")
    children = tuple(
        sorted(
            (_condition(item) for item in value[branch] if isinstance(item, Mapping)),
            key=lambda item: canonical_json_bytes(item.model_dump(mode="json")),
        )
    )
    if len(children) != len(value[branch]):
        raise ValueError("constraint branches must contain objects")
    return ConstraintConditionV1(
        all_of=children if branch == "all" else (),
        any_of=children if branch == "any" else (),
    )


def _constraints(values: Sequence[Mapping[str, Any]]) -> tuple[ConstraintSpecV1, ...]:
    return tuple(
        sorted(
            (
                ConstraintSpecV1(
                    id=str(item["id"]),
                    severity=ConstraintSeverity(item["severity"]),
                    when=_condition(item["when"]),
                    message=str(item["message"]),
                )
                for item in values
            ),
            key=lambda item: item.id,
        )
    )


def _schema_allowed_values(schema: Mapping[str, Any], field: str) -> tuple[JsonValue, ...]:
    properties = schema.get("properties")
    definitions = schema.get("$defs")
    if not isinstance(properties, Mapping):
        return ()
    selected = properties.get(field)
    if not isinstance(selected, Mapping):
        return ()

    def enums(value: Mapping[str, Any]) -> list[JsonValue]:
        raw_enum = value.get("enum")
        if isinstance(raw_enum, list):
            return list(raw_enum)
        reference = value.get("$ref")
        if (
            isinstance(reference, str)
            and reference.startswith("#/$defs/")
            and isinstance(definitions, Mapping)
        ):
            target = definitions.get(reference.removeprefix("#/$defs/"))
            if isinstance(target, Mapping):
                return enums(target)
        result: list[JsonValue] = []
        for branch in value.get("anyOf", []):
            if isinstance(branch, Mapping):
                result.extend(enums(branch))
        return result

    values = enums(selected)
    return tuple(
        value
        for _encoded, value in sorted(
            {canonical_json_bytes(value): value for value in values}.items(),
            key=lambda item: item[0],
        )
    )


def project_component_inspection(inspection: Mapping[str, Any]) -> GovernedRegistry:
    """Project one bounded-worker component inspection into V1 compatibility records."""

    component = inspection.get("component")
    governance = inspection.get("governance")
    input_schema = inspection.get("input_schema")
    output_schema = inspection.get("output_schema")
    if not all(
        isinstance(value, Mapping) for value in (component, governance, input_schema, output_schema)
    ):
        raise ValueError("governance inspection projection is incomplete")
    assert isinstance(component, Mapping)
    assert isinstance(governance, Mapping)
    assert isinstance(input_schema, Mapping)
    assert isinstance(output_schema, Mapping)

    method_id = str(component["id"])
    version = str(component["version"])
    _prefix, category, slug = method_id.split(".")
    capability_id = f"component.{category}.{slug}"
    implementation_id = f"dq_native.{category}.{slug}"
    title = str(inspection["title"])
    summary = str(inspection["summary"])
    input_ports = _named_ports(inspection.get("input_ports", ()))
    output_ports = _named_ports(inspection.get("output_ports", ()))

    capability = CapabilitySpecV1(
        id=capability_id,
        version=version,
        title=f"{title} compatibility capability",
        kind=CapabilityKind.CALCULATION,
        summary=summary,
        input_schema=dict(input_schema),
        output_schema=dict(output_schema),
        input_ports=input_ports,
        output_ports=output_ports,
    )

    questions = governance.get("required_questions", ())
    allowed_defaults = governance.get("allowed_defaults", ())
    raw_constraints = governance.get("constraints", ())
    methodology = governance.get("methodology")
    if (
        not isinstance(questions, Sequence)
        or not isinstance(allowed_defaults, Sequence)
        or not isinstance(raw_constraints, Sequence)
        or not isinstance(methodology, Mapping)
    ):
        raise ValueError("governance contract fields are malformed")

    explicit_default_by_field: dict[str, tuple[JsonValue, str]] = {}
    for item in allowed_defaults:
        if not isinstance(item, Mapping) or not isinstance(item.get("field"), str):
            raise ValueError("allowed default projection is malformed")
        if item.get("only_if") is not None:
            raise ValueError("conditional defaults are outside the V1 compatibility projection")
        explicit_default_by_field[item["field"]] = (
            item.get("value"),
            str(item.get("why") or "Default explicitly authored by the method contract."),
        )

    conventions: list[ConventionSpecV1] = []
    for item in questions:
        if not isinstance(item, Mapping):
            raise ValueError("required question projection is malformed")
        field = str(item["resolves_to"])
        question_default = item.get("default")
        has_default = question_default is not None or field in explicit_default_by_field
        if question_default is not None and field not in explicit_default_by_field:
            explicit_default_by_field[field] = (
                question_default,
                "Default explicitly authored by the method question.",
            )
        conventions.append(
            ConventionSpecV1(
                id=str(item["id"]),
                field=field,
                question=str(item["ask"]),
                materiality=str(item["why"]),
                required_from_user=not has_default,
                allowed_values=_schema_allowed_values(input_schema, field),
            )
        )

    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError("input schema properties are malformed")
    for field, property_schema in properties.items():
        if (
            field not in explicit_default_by_field
            and isinstance(property_schema, Mapping)
            and "default" in property_schema
        ):
            explicit_default_by_field[str(field)] = (
                property_schema["default"],
                "Default explicitly authored by the canonical component input model.",
            )

    defaults = tuple(
        DefaultSpecV1(field=field, value=value, rationale=rationale)
        for field, (value, rationale) in sorted(explicit_default_by_field.items())
    )
    input_bindings = tuple(
        RecipeInputBindingV1(
            target_field=str(field),
            source=RecipeValueSourceV1(source="method_input", field=str(field)),
        )
        for field in sorted(properties)
    )
    output_properties = output_schema.get("properties")
    if not isinstance(output_properties, Mapping):
        raise ValueError("output schema properties are malformed")
    recipe = RecipeDagV1(
        steps=(
            RecipeStepV1(
                step_id="calculate",
                capability_id=capability.id,
                input_bindings=input_bindings,
                output_fields=tuple(sorted(str(field) for field in output_properties)),
            ),
        ),
        result_steps=("calculate",),
    )
    formula = methodology.get("formula")
    assumptions = methodology.get("assumptions", ())
    limitations = methodology.get("limitations", ())
    if (
        not isinstance(formula, str)
        or not isinstance(assumptions, Sequence)
        or isinstance(assumptions, str | bytes)
        or not all(isinstance(item, str) for item in assumptions)
        or not isinstance(limitations, Sequence)
        or isinstance(limitations, str | bytes)
        or not all(isinstance(item, str) for item in limitations)
    ):
        raise ValueError("methodology formula is malformed")
    method = MethodSpecV1(
        id=method_id,
        version=version,
        title=title,
        summary=summary,
        input_schema=dict(input_schema),
        output_schema=dict(output_schema),
        input_ports=input_ports,
        output_ports=output_ports,
        methodology=(formula,),
        assumptions=tuple(assumptions),
        limitations=tuple(limitations),
        interpretation=str(governance["interpretation"]),
        constraints=_constraints(
            tuple(item for item in raw_constraints if isinstance(item, Mapping))
        ),
        conventions=tuple(sorted(conventions, key=lambda item: (item.field, item.id))),
        defaults=defaults,
        capabilities=(capability.ref,),
        recipe=recipe,
    )
    if len(method.constraints) != len(raw_constraints):
        raise ValueError("constraint projection is malformed")

    implementation = ImplementationSpecV1(
        id=implementation_id,
        version=version,
        title=f"{title} DQ-native implementation",
        capability=capability.ref,
        input_ports=input_ports,
        output_ports=output_ports,
        adapter=TrustedAdapterV1(
            id="dq_native_component",
            version="1.0.0",
            distribution="defined-quant",
        ),
        transport=TransportMetadataV1(
            kind=ImplementationTransport.DQ_NATIVE,
            locality=TransportLocality.LOCAL,
            system_id="local_component_runtime",
            requires_network=False,
            requires_credentials=False,
        ),
        component=ComponentRef.model_validate(component),
        trust_dimensions=(
            TrustDimension.SCHEMA_CHECKED,
            TrustDimension.TRUSTED_ADAPTER,
        ),
    )
    return GovernedRegistry(
        methods=(method,),
        capabilities=(capability,),
        implementations=(implementation,),
    )


__all__ = ["GovernedRegistry", "project_component_inspection"]
