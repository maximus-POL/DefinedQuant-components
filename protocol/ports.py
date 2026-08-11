"""Closed semantic-port metadata and compatibility checks for component fields."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, TypeAlias, overload

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

PORT_SCHEMA_KEY = "x-defined-quant-port"


class PortDirection(StrEnum):
    """Whether a component field consumes or produces a semantic value."""

    INPUT = "input"
    OUTPUT = "output"


class PortConcept(StrEnum):
    """Closed financial concepts that may cross a component boundary."""

    PRICE_SERIES = "price_series"
    PRICE_ADJUSTMENT_KIND = "price_adjustment_kind"
    PERIODIC_RETURN_SERIES = "periodic_return_series"
    RETURN_CONVENTION = "return_convention"
    ANNUALIZATION_FACTOR = "annualization_factor"
    PERIODIC_VOLATILITY = "periodic_volatility"
    ANNUALIZED_VOLATILITY = "annualized_volatility"


class PortUnit(StrEnum):
    """Closed unit vocabulary for semantic ports."""

    PRICE = "price"
    DECIMAL = "decimal"
    UNITLESS = "unitless"
    VOLATILITY = "volatility"


class PortShape(StrEnum):
    """Closed structural shapes for semantic values."""

    SCALAR = "scalar"
    ORDERED_SERIES = "ordered_series"


class PortCardinality(StrEnum):
    """Closed value-count guarantees for semantic ports."""

    EXACTLY_ONE = "exactly_one"
    ONE_OR_MORE = "one_or_more"


class PortConvention(StrEnum):
    """Closed calculation and interpretation conventions."""

    ORDERED_POSITIVE_PRICES = "ordered_positive_prices"
    ADJUSTED_OR_UNADJUSTED_PRICE_KIND = "adjusted_or_unadjusted_price_kind"
    SIMPLE_PERIODIC_RETURN = "simple_periodic_return"
    LOG_PERIODIC_RETURN = "log_periodic_return"
    EXPLICIT_PERIODS_PER_YEAR_FACTOR = "explicit_periods_per_year_factor"
    SAMPLE_STANDARD_DEVIATION_N_MINUS_1 = "sample_standard_deviation_n_minus_1"
    SAMPLE_STANDARD_DEVIATION_N_MINUS_1_SQUARE_ROOT_ANNUALIZATION = (
        "sample_standard_deviation_n_minus_1_square_root_annualization"
    )


class PortOrdering(StrEnum):
    """Closed ordering guarantees for semantic values."""

    NOT_APPLICABLE = "not_applicable"
    PRESERVE_SOURCE_ORDER = "preserve_source_order"


class PortFrequency(StrEnum):
    """Closed frequency semantics for semantic values."""

    NOT_APPLICABLE = "not_applicable"
    INHERITED = "inherited"
    ANNUAL = "annual"


class PortProvenanceRequirement(StrEnum):
    """Closed producer guarantees and consumer requirements for provenance."""

    NOT_REQUIRED = "not_required"
    SOURCE_BOUND = "source_bound"
    COMPONENT_BOUND = "component_bound"
    SOURCE_OR_COMPONENT_BOUND = "source_or_component_bound"


class PortDimension(StrEnum):
    """The eight semantic dimensions compared between two ports."""

    CONCEPT = "concept"
    UNIT = "unit"
    SHAPE = "shape"
    CARDINALITY = "cardinality"
    CONVENTION = "convention"
    ORDERING = "ordering"
    FREQUENCY = "frequency"
    PROVENANCE_REQUIREMENT = "provenance_requirement"


PortValue: TypeAlias = (
    PortConcept
    | PortUnit
    | PortShape
    | PortCardinality
    | PortConvention
    | PortOrdering
    | PortFrequency
    | PortProvenanceRequirement
)

_VALUES_BY_DIMENSION: dict[PortDimension, frozenset[str]] = {
    PortDimension.CONCEPT: frozenset(item.value for item in PortConcept),
    PortDimension.UNIT: frozenset(item.value for item in PortUnit),
    PortDimension.SHAPE: frozenset(item.value for item in PortShape),
    PortDimension.CARDINALITY: frozenset(item.value for item in PortCardinality),
    PortDimension.CONVENTION: frozenset(item.value for item in PortConvention),
    PortDimension.ORDERING: frozenset(item.value for item in PortOrdering),
    PortDimension.FREQUENCY: frozenset(item.value for item in PortFrequency),
    PortDimension.PROVENANCE_REQUIREMENT: frozenset(
        item.value for item in PortProvenanceRequirement
    ),
}
_VALUE_TYPE_BY_DIMENSION: dict[PortDimension, type[StrEnum]] = {
    PortDimension.CONCEPT: PortConcept,
    PortDimension.UNIT: PortUnit,
    PortDimension.SHAPE: PortShape,
    PortDimension.CARDINALITY: PortCardinality,
    PortDimension.CONVENTION: PortConvention,
    PortDimension.ORDERING: PortOrdering,
    PortDimension.FREQUENCY: PortFrequency,
    PortDimension.PROVENANCE_REQUIREMENT: PortProvenanceRequirement,
}
_ALL_PORT_VALUES = sorted(set().union(*_VALUES_BY_DIMENSION.values()))


def _add_port_difference_dimension_schema(schema: dict[str, Any]) -> None:
    """Narrow each difference value to the vocabulary selected by ``dimension``."""

    schema["allOf"] = [
        {
            "if": {
                "properties": {"dimension": {"const": dimension.value}},
                "required": ["dimension"],
            },
            "then": {
                "properties": {
                    "producer_value": {"enum": sorted(values)},
                    "consumer_value": {"enum": sorted(values)},
                }
            },
        }
        for dimension, values in _VALUES_BY_DIMENSION.items()
    ]
    schema["not"] = {
        "anyOf": [
            {
                "properties": {
                    "producer_value": {"const": value},
                    "consumer_value": {"const": value},
                },
                "required": ["producer_value", "consumer_value"],
            }
            for value in _ALL_PORT_VALUES
        ]
    }


def _add_port_compatibility_schema(schema: dict[str, Any]) -> None:
    """Expose direction and empty/non-empty result invariants to JSON Schema consumers."""

    schema["allOf"] = [
        {
            "properties": {
                "producer": {
                    "properties": {"direction": {"const": PortDirection.OUTPUT.value}},
                    "required": ["direction"],
                },
                "consumer": {
                    "properties": {"direction": {"const": PortDirection.INPUT.value}},
                    "required": ["direction"],
                },
            }
        },
        {
            "if": {
                "properties": {"compatible": {"const": True}},
                "required": ["compatible"],
            },
            "then": {"properties": {"differences": {"maxItems": 0}}},
        },
        {
            "if": {
                "properties": {"compatible": {"const": False}},
                "required": ["compatible"],
            },
            "then": {"properties": {"differences": {"minItems": 1}}},
        },
    ]


def _add_semantic_port_shape_schema(schema: dict[str, Any]) -> None:
    """Expose the only two valid shape/cardinality pairs to JSON Schema consumers."""

    schema["allOf"] = [
        {
            "if": {
                "properties": {"shape": {"const": PortShape.SCALAR.value}},
                "required": ["shape"],
            },
            "then": {
                "properties": {
                    "cardinality": {"const": PortCardinality.EXACTLY_ONE.value}
                }
            },
        },
        {
            "if": {
                "properties": {"shape": {"const": PortShape.ORDERED_SERIES.value}},
                "required": ["shape"],
            },
            "then": {
                "properties": {
                    "cardinality": {"const": PortCardinality.ONE_OR_MORE.value}
                }
            },
        },
    ]


class SemanticPort(BaseModel):
    """One complete, closed semantic contract attached to a Pydantic field."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra=_add_semantic_port_shape_schema,
    )

    direction: PortDirection
    concept: PortConcept
    unit: PortUnit
    shape: PortShape
    cardinality: PortCardinality
    convention: PortConvention
    ordering: PortOrdering
    frequency: PortFrequency
    provenance_requirement: PortProvenanceRequirement

    @model_validator(mode="after")
    def validate_shape_cardinality(self) -> SemanticPort:
        expected = (
            PortCardinality.EXACTLY_ONE
            if self.shape == PortShape.SCALAR
            else PortCardinality.ONE_OR_MORE
        )
        if self.cardinality != expected:
            raise ValueError(
                f"{self.shape.value} semantic port requires {expected.value} cardinality"
            )
        return self


class PortDifference(BaseModel):
    """One incompatible semantic dimension and both declared values."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra=_add_port_difference_dimension_schema,
    )

    dimension: PortDimension
    producer_value: PortValue
    consumer_value: PortValue

    @model_validator(mode="before")
    @classmethod
    def coerce_values_to_dimension_type(cls, value: Any) -> Any:
        """Resolve shared wire values, such as ``not_applicable``, without union ambiguity."""

        if not isinstance(value, Mapping):
            return value
        raw_dimension = value.get("dimension")
        if not isinstance(raw_dimension, str):
            return value
        try:
            dimension = PortDimension(raw_dimension)
        except ValueError:
            return value
        payload = dict(value)
        value_type = _VALUE_TYPE_BY_DIMENSION[dimension]
        for field_name in ("producer_value", "consumer_value"):
            if field_name not in payload:
                continue
            try:
                payload[field_name] = value_type(payload[field_name])
            except (TypeError, ValueError):
                pass
        return payload

    @model_validator(mode="after")
    def validate_dimension_values(self) -> PortDifference:
        allowed = _VALUES_BY_DIMENSION[self.dimension]
        if self.producer_value.value not in allowed:
            raise ValueError("producer value does not belong to the declared dimension")
        if self.consumer_value.value not in allowed:
            raise ValueError("consumer value does not belong to the declared dimension")
        if self.producer_value.value == self.consumer_value.value:
            raise ValueError("a semantic-port difference must contain distinct values")
        return self


def _provenance_is_compatible(
    producer: PortProvenanceRequirement,
    consumer: PortProvenanceRequirement,
) -> bool:
    if consumer == PortProvenanceRequirement.NOT_REQUIRED:
        return True
    if consumer == PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND:
        return producer in {
            PortProvenanceRequirement.SOURCE_BOUND,
            PortProvenanceRequirement.COMPONENT_BOUND,
            PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND,
        }
    return producer == consumer


def _differences(producer: SemanticPort, consumer: SemanticPort) -> tuple[PortDifference, ...]:
    differences: list[PortDifference] = []
    for dimension in PortDimension:
        producer_value = getattr(producer, dimension.value)
        consumer_value = getattr(consumer, dimension.value)
        compatible = producer_value == consumer_value
        if dimension == PortDimension.PROVENANCE_REQUIREMENT:
            compatible = _provenance_is_compatible(producer_value, consumer_value)
        if not compatible:
            differences.append(
                PortDifference(
                    dimension=dimension,
                    producer_value=producer_value,
                    consumer_value=consumer_value,
                )
            )
    return tuple(differences)


class PortCompatibility(BaseModel):
    """Auditable result of comparing one output port with one input port.

    JSON Schema exposes direction and empty/non-empty consistency. Exact recomputation of the
    differences from both semantic ports remains a runtime Pydantic invariant.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra=_add_port_compatibility_schema,
    )

    producer: SemanticPort
    consumer: SemanticPort
    compatible: bool
    differences: tuple[PortDifference, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> PortCompatibility:
        if self.producer.direction != PortDirection.OUTPUT:
            raise ValueError("producer semantic port must have output direction")
        if self.consumer.direction != PortDirection.INPUT:
            raise ValueError("consumer semantic port must have input direction")
        expected = _differences(self.producer, self.consumer)
        if self.differences != expected or self.compatible != (not expected):
            raise ValueError("compatibility result does not match its semantic ports")
        return self


class SemanticPortError(ValueError):
    """Raised when field metadata does not contain one valid closed semantic port."""


class IncompatiblePortsError(ValueError):
    """Raised when a producer field cannot feed a consumer field."""

    def __init__(self, compatibility: PortCompatibility) -> None:
        details = ", ".join(
            f"{difference.dimension.value}: {difference.producer_value.value} -> "
            f"{difference.consumer_value.value}"
            for difference in compatibility.differences
        )
        super().__init__(f"semantic ports are incompatible ({details})")
        self.compatibility = compatibility


@overload
def semantic_port_metadata(port: SemanticPort) -> dict[str, Any]: ...


@overload
def semantic_port_metadata(
    *,
    direction: PortDirection,
    concept: PortConcept,
    unit: PortUnit,
    shape: PortShape,
    cardinality: PortCardinality,
    convention: PortConvention,
    ordering: PortOrdering,
    frequency: PortFrequency,
    provenance_requirement: PortProvenanceRequirement,
) -> dict[str, Any]: ...


def semantic_port_metadata(
    port: SemanticPort | None = None,
    *,
    direction: PortDirection | None = None,
    concept: PortConcept | None = None,
    unit: PortUnit | None = None,
    shape: PortShape | None = None,
    cardinality: PortCardinality | None = None,
    convention: PortConvention | None = None,
    ordering: PortOrdering | None = None,
    frequency: PortFrequency | None = None,
    provenance_requirement: PortProvenanceRequirement | None = None,
) -> dict[str, Any]:
    """Return the sole permitted ``json_schema_extra`` mapping for a semantic port."""

    supplied_dimensions = {
        "direction": direction,
        "concept": concept,
        "unit": unit,
        "shape": shape,
        "cardinality": cardinality,
        "convention": convention,
        "ordering": ordering,
        "frequency": frequency,
        "provenance_requirement": provenance_requirement,
    }
    if port is not None:
        if any(value is not None for value in supplied_dimensions.values()):
            raise SemanticPortError(
                "pass either a SemanticPort or all keyword dimensions, not both"
            )
        resolved = port
    else:
        try:
            resolved = SemanticPort.model_validate(supplied_dimensions)
        except ValidationError as exc:
            raise SemanticPortError("all semantic-port keyword dimensions are required") from exc
    return {PORT_SCHEMA_KEY: resolved.model_dump(mode="json")}


def _schema_shapes(schema: Mapping[str, Any], root: Mapping[str, Any]) -> set[str]:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definitions = root.get("$defs")
        name = reference.removeprefix("#/$defs/")
        if isinstance(definitions, Mapping):
            target = definitions.get(name)
            if isinstance(target, Mapping):
                return _schema_shapes(target, root)
    alternatives = schema.get("anyOf", schema.get("oneOf"))
    if isinstance(alternatives, list):
        return set().union(
            *(
                _schema_shapes(item, root)
                for item in alternatives
                if isinstance(item, Mapping)
            )
        )
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        types = {raw_type}
    elif isinstance(raw_type, list) and all(isinstance(item, str) for item in raw_type):
        types = set(raw_type)
    else:
        types = set()
    types.discard("null")
    if not types and ("enum" in schema or "const" in schema):
        values = schema.get("enum", [schema.get("const")])
        if isinstance(values, list) and all(
            value is None or isinstance(value, (bool, int, float, str))
            for value in values
        ):
            types.add("scalar")
    shapes: set[str] = set()
    if types & {"boolean", "integer", "number", "string", "scalar"}:
        shapes.add("scalar")
    if "array" in types and schema.get("uniqueItems") is not True:
        shapes.add("ordered_series")
    return shapes


def _array_minimum_items(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
) -> tuple[int | None, ...]:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definitions = root.get("$defs")
        name = reference.removeprefix("#/$defs/")
        if isinstance(definitions, Mapping):
            target = definitions.get(name)
            if isinstance(target, Mapping):
                return _array_minimum_items(target, root)
    alternatives = schema.get("anyOf", schema.get("oneOf"))
    if isinstance(alternatives, list):
        return tuple(
            minimum
            for item in alternatives
            if isinstance(item, Mapping)
            for minimum in _array_minimum_items(item, root)
        )
    raw_type = schema.get("type")
    types = {raw_type} if isinstance(raw_type, str) else set()
    if isinstance(raw_type, list) and all(isinstance(item, str) for item in raw_type):
        types = set(raw_type)
    if "array" not in types:
        return ()
    minimum = schema.get("minItems")
    if isinstance(minimum, bool) or not isinstance(minimum, int):
        return (None,)
    return (minimum,)


def _schema_allows_null(schema: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definitions = root.get("$defs")
        name = reference.removeprefix("#/$defs/")
        if isinstance(definitions, Mapping):
            target = definitions.get(name)
            if isinstance(target, Mapping) and _schema_allows_null(target, root):
                return True
    alternatives = schema.get("anyOf", schema.get("oneOf"))
    if isinstance(alternatives, list) and any(
        _schema_allows_null(item, root)
        for item in alternatives
        if isinstance(item, Mapping)
    ):
        return True
    raw_type = schema.get("type")
    if raw_type == "null":
        return True
    if isinstance(raw_type, list) and "null" in raw_type:
        return True
    if schema.get("const", object()) is None:
        return True
    enum_values = schema.get("enum")
    return isinstance(enum_values, list) and None in enum_values


def _field_shape_and_cardinality(
    model: type[BaseModel],
    field_name: str,
) -> tuple[PortShape, PortCardinality]:
    model_schema = model.model_json_schema(by_alias=False)
    properties = model_schema.get("properties")
    field_schema = properties.get(field_name) if isinstance(properties, Mapping) else None
    if not isinstance(field_schema, Mapping):
        raise SemanticPortError(
            f"{model.__name__}.{field_name} has no generated JSON Schema property"
        )
    if _schema_allows_null(field_schema, model_schema):
        raise SemanticPortError(
            f"{model.__name__}.{field_name} semantic-port field cannot be nullable"
        )
    shapes = _schema_shapes(field_schema, model_schema)
    if shapes == {"ordered_series"}:
        minimums = _array_minimum_items(field_schema, model_schema)
        if not minimums or any(minimum is None or minimum < 1 for minimum in minimums):
            raise SemanticPortError(
                f"{model.__name__}.{field_name} one_or_more semantic port requires "
                "an ordered array schema with minItems >= 1"
            )
        return PortShape.ORDERED_SERIES, PortCardinality.ONE_OR_MORE
    if shapes == {"scalar"}:
        return PortShape.SCALAR, PortCardinality.EXACTLY_ONE
    raise SemanticPortError(
        f"{model.__name__}.{field_name} field type does not resolve to one "
        "unambiguous semantic-port shape"
    )


def extract_semantic_port(
    model: type[BaseModel],
    field_name: str,
    expected_direction: PortDirection,
) -> SemanticPort:
    """Read and validate one field's closed semantic-port metadata."""

    field = model.model_fields.get(field_name)
    if field is None:
        raise SemanticPortError(f"{model.__name__}.{field_name} is not a model field")
    metadata = field.json_schema_extra
    if metadata is None:
        raise SemanticPortError(
            f"{model.__name__}.{field_name} is missing {PORT_SCHEMA_KEY!r} metadata"
        )
    if not isinstance(metadata, Mapping):
        raise SemanticPortError(
            f"{model.__name__}.{field_name} json_schema_extra must be a mapping"
        )
    metadata_keys = set(metadata)
    if PORT_SCHEMA_KEY not in metadata_keys:
        raise SemanticPortError(
            f"{model.__name__}.{field_name} is missing {PORT_SCHEMA_KEY!r} metadata"
        )
    if metadata_keys != {PORT_SCHEMA_KEY}:
        unsupported = sorted(str(key) for key in metadata_keys - {PORT_SCHEMA_KEY})
        raise SemanticPortError(
            f"{model.__name__}.{field_name} has unsupported sibling metadata keys: {unsupported}"
        )
    try:
        port = SemanticPort.model_validate(metadata[PORT_SCHEMA_KEY])
    except ValidationError as exc:
        raise SemanticPortError(
            f"{model.__name__}.{field_name} has invalid {PORT_SCHEMA_KEY!r} metadata"
        ) from exc
    if port.direction != expected_direction:
        raise SemanticPortError(
            f"{model.__name__}.{field_name} semantic-port direction is {port.direction.value!r}; "
            f"expected {expected_direction.value!r}"
        )
    actual_shape, actual_cardinality = _field_shape_and_cardinality(model, field_name)
    if port.shape != actual_shape:
        raise SemanticPortError(
            f"{model.__name__}.{field_name} semantic-port shape {port.shape.value!r} "
            f"contradicts its Pydantic field shape {actual_shape.value!r}"
        )
    if port.cardinality != actual_cardinality:
        raise SemanticPortError(
            f"{model.__name__}.{field_name} semantic-port cardinality "
            f"{port.cardinality.value!r} contradicts its Pydantic field shape; "
            f"expected {actual_cardinality.value!r}"
        )
    return port


def compare_semantic_ports(
    producer_model: type[BaseModel],
    producer_field: str,
    consumer_model: type[BaseModel],
    consumer_field: str,
) -> PortCompatibility:
    """Compare one producer output field with one consumer input field."""

    producer = extract_semantic_port(producer_model, producer_field, PortDirection.OUTPUT)
    consumer = extract_semantic_port(consumer_model, consumer_field, PortDirection.INPUT)
    differences = _differences(producer, consumer)
    return PortCompatibility(
        producer=producer,
        consumer=consumer,
        compatible=not differences,
        differences=differences,
    )


def require_compatible_ports(
    producer_model: type[BaseModel],
    producer_field: str,
    consumer_model: type[BaseModel],
    consumer_field: str,
) -> PortCompatibility:
    """Return compatibility or raise with its complete typed difference record."""

    compatibility = compare_semantic_ports(
        producer_model,
        producer_field,
        consumer_model,
        consumer_field,
    )
    if not compatibility.compatible:
        raise IncompatiblePortsError(compatibility)
    return compatibility


__all__ = [
    "PORT_SCHEMA_KEY",
    "IncompatiblePortsError",
    "PortCardinality",
    "PortCompatibility",
    "PortConcept",
    "PortConvention",
    "PortDifference",
    "PortDimension",
    "PortDirection",
    "PortFrequency",
    "PortOrdering",
    "PortProvenanceRequirement",
    "PortShape",
    "PortUnit",
    "PortValue",
    "SemanticPort",
    "SemanticPortError",
    "compare_semantic_ports",
    "extract_semantic_port",
    "require_compatible_ports",
    "semantic_port_metadata",
]
