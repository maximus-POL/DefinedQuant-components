"""Tests for the closed semantic-port metadata and compatibility protocol."""

from __future__ import annotations

from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest
from defined_quant.types import DomainError
from defined_quant_protocol import (
    PORT_SCHEMA_KEY,
    IncompatiblePortsError,
    PortCardinality,
    PortCompatibility,
    PortConcept,
    PortConvention,
    PortDifference,
    PortDimension,
    PortDirection,
    PortFrequency,
    PortOrdering,
    PortProvenanceRequirement,
    PortShape,
    PortUnit,
    SemanticPort,
    SemanticPortError,
    compare_semantic_ports,
    extract_semantic_port,
    require_compatible_ports,
    semantic_port_metadata,
)
from pydantic import BaseModel, Field, ValidationError, create_model

PRODUCER = SemanticPort(
    direction=PortDirection.OUTPUT,
    concept=PortConcept.PERIODIC_RETURN_SERIES,
    unit=PortUnit.DECIMAL,
    shape=PortShape.ORDERED_SERIES,
    cardinality=PortCardinality.ONE_OR_MORE,
    convention=PortConvention.LOG_PERIODIC_RETURN,
    ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
    frequency=PortFrequency.INHERITED,
    provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
)
CONSUMER = SemanticPort(
    direction=PortDirection.INPUT,
    concept=PortConcept.PERIODIC_RETURN_SERIES,
    unit=PortUnit.DECIMAL,
    shape=PortShape.ORDERED_SERIES,
    cardinality=PortCardinality.ONE_OR_MORE,
    convention=PortConvention.LOG_PERIODIC_RETURN,
    ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
    frequency=PortFrequency.INHERITED,
    provenance_requirement=PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND,
)


def _model(name: str, metadata: Any) -> type[BaseModel]:
    return create_model(
        name,
        value=(tuple[float, ...], Field(min_length=1, json_schema_extra=metadata)),
    )


def _models(
    producer: SemanticPort = PRODUCER,
    consumer: SemanticPort = CONSUMER,
) -> tuple[type[BaseModel], type[BaseModel]]:
    return (
        _model("SemanticPortProducer", semantic_port_metadata(producer)),
        _model("SemanticPortConsumer", semantic_port_metadata(consumer)),
    )


def _changed(port: SemanticPort, dimension: PortDimension, value: str) -> SemanticPort:
    payload = port.model_dump(mode="json")
    payload[dimension.value] = value
    return SemanticPort.model_validate(payload)


def test_metadata_helper_accepts_a_model_or_all_keyword_dimensions() -> None:
    from_model = semantic_port_metadata(PRODUCER)
    from_keywords = semantic_port_metadata(
        direction=PortDirection.OUTPUT,
        concept=PortConcept.PERIODIC_RETURN_SERIES,
        unit=PortUnit.DECIMAL,
        shape=PortShape.ORDERED_SERIES,
        cardinality=PortCardinality.ONE_OR_MORE,
        convention=PortConvention.LOG_PERIODIC_RETURN,
        ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
        frequency=PortFrequency.INHERITED,
        provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
    )

    assert from_model == from_keywords
    assert set(from_model) == {PORT_SCHEMA_KEY}
    assert from_model[PORT_SCHEMA_KEY] == PRODUCER.model_dump(mode="json")


def test_metadata_helper_refuses_mixed_or_incomplete_arguments() -> None:
    with pytest.raises(SemanticPortError, match="either"):
        semantic_port_metadata(PRODUCER, direction=PortDirection.OUTPUT)  # type: ignore[call-overload]

    with pytest.raises(SemanticPortError, match="all semantic-port"):
        semantic_port_metadata(direction=PortDirection.OUTPUT)  # type: ignore[call-overload]


def test_semantic_port_is_closed_and_frozen() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SemanticPort.model_validate({**PRODUCER.model_dump(), "dialect": "ad_hoc"})

    with pytest.raises(ValidationError, match="frozen_instance"):
        PRODUCER.unit = PortUnit.PRICE


@pytest.mark.parametrize(
    ("shape", "cardinality"),
    [
        (PortShape.SCALAR, PortCardinality.ONE_OR_MORE),
        (PortShape.ORDERED_SERIES, PortCardinality.EXACTLY_ONE),
    ],
)
def test_semantic_port_model_and_schema_reject_mismatched_shape_cardinality(
    shape: PortShape,
    cardinality: PortCardinality,
) -> None:
    payload = {
        **PRODUCER.model_dump(mode="json"),
        "shape": shape.value,
        "cardinality": cardinality.value,
    }
    validator = jsonschema.Draft202012Validator(SemanticPort.model_json_schema())

    assert list(validator.iter_errors(payload))
    with pytest.raises(ValidationError, match="semantic port requires"):
        SemanticPort.model_validate(payload)


@pytest.mark.parametrize(
    "producer_provenance",
    [
        PortProvenanceRequirement.SOURCE_BOUND,
        PortProvenanceRequirement.COMPONENT_BOUND,
    ],
)
def test_source_or_component_consumer_accepts_either_exact_producer_provenance(
    producer_provenance: PortProvenanceRequirement,
) -> None:
    producer = _changed(
        PRODUCER,
        PortDimension.PROVENANCE_REQUIREMENT,
        producer_provenance.value,
    )
    producer_model, consumer_model = _models(producer, CONSUMER)

    compatibility = compare_semantic_ports(
        producer_model,
        "value",
        consumer_model,
        "value",
    )

    assert compatibility.compatible is True
    assert compatibility.differences == ()
    assert require_compatible_ports(
        producer_model,
        "value",
        consumer_model,
        "value",
    ) == compatibility


@pytest.mark.parametrize("producer_provenance", list(PortProvenanceRequirement))
def test_consumer_without_a_provenance_requirement_accepts_every_producer(
    producer_provenance: PortProvenanceRequirement,
) -> None:
    producer = _changed(
        PRODUCER,
        PortDimension.PROVENANCE_REQUIREMENT,
        producer_provenance.value,
    )
    consumer = _changed(
        CONSUMER,
        PortDimension.PROVENANCE_REQUIREMENT,
        PortProvenanceRequirement.NOT_REQUIRED.value,
    )
    producer_model, consumer_model = _models(producer, consumer)

    assert compare_semantic_ports(
        producer_model,
        "value",
        consumer_model,
        "value",
    ).compatible


@pytest.mark.parametrize(
    ("dimension", "consumer_value"),
    [
        (PortDimension.CONCEPT, PortConcept.PRICE_SERIES.value),
        (PortDimension.UNIT, PortUnit.PRICE.value),
        (PortDimension.CONVENTION, PortConvention.SIMPLE_PERIODIC_RETURN.value),
        (PortDimension.ORDERING, PortOrdering.NOT_APPLICABLE.value),
        (PortDimension.FREQUENCY, PortFrequency.ANNUAL.value),
        (
            PortDimension.PROVENANCE_REQUIREMENT,
            PortProvenanceRequirement.SOURCE_BOUND.value,
        ),
    ],
)
def test_comparison_reports_each_incompatible_semantic_dimension(
    dimension: PortDimension,
    consumer_value: str,
) -> None:
    consumer = _changed(CONSUMER, dimension, consumer_value)
    producer_model, consumer_model = _models(PRODUCER, consumer)

    compatibility = compare_semantic_ports(
        producer_model,
        "value",
        consumer_model,
        "value",
    )

    assert compatibility.compatible is False
    assert len(compatibility.differences) == 1
    difference = compatibility.differences[0]
    assert difference.dimension == dimension
    assert difference.producer_value.value == getattr(PRODUCER, dimension.value).value
    assert difference.consumer_value.value == consumer_value

    with pytest.raises(IncompatiblePortsError) as exc_info:
        require_compatible_ports(producer_model, "value", consumer_model, "value")
    assert exc_info.value.compatibility == compatibility
    assert dimension.value in str(exc_info.value)


def test_comparison_reports_truthful_shape_and_cardinality_differences() -> None:
    scalar_consumer = CONSUMER.model_copy(
        update={
            "shape": PortShape.SCALAR,
            "cardinality": PortCardinality.EXACTLY_ONE,
        }
    )
    producer_model = _model(
        "StructuralProducer",
        semantic_port_metadata(PRODUCER),
    )
    consumer_model = create_model(
        "StructuralConsumer",
        value=(float, Field(json_schema_extra=semantic_port_metadata(scalar_consumer))),
    )

    compatibility = compare_semantic_ports(
        producer_model,
        "value",
        consumer_model,
        "value",
    )

    assert tuple(item.dimension for item in compatibility.differences) == (
        PortDimension.SHAPE,
        PortDimension.CARDINALITY,
    )


def test_extract_returns_the_closed_port() -> None:
    producer_model, _ = _models()

    assert extract_semantic_port(
        producer_model,
        "value",
        PortDirection.OUTPUT,
    ) == PRODUCER


def test_extract_refuses_an_unknown_model_field() -> None:
    producer_model, _ = _models()

    with pytest.raises(SemanticPortError, match="not a model field"):
        extract_semantic_port(producer_model, "missing", PortDirection.OUTPUT)


def test_extract_refuses_absent_metadata() -> None:
    model = _model("AbsentMetadata", None)

    with pytest.raises(SemanticPortError, match="missing"):
        extract_semantic_port(model, "value", PortDirection.OUTPUT)


def test_extract_refuses_non_mapping_metadata() -> None:
    def mutate_schema(schema: dict[str, Any]) -> None:
        schema["description"] = "callable metadata is not a semantic port"

    model = _model("CallableMetadata", mutate_schema)

    with pytest.raises(SemanticPortError, match="must be a mapping"):
        extract_semantic_port(model, "value", PortDirection.OUTPUT)


def test_extract_refuses_a_mapping_without_the_semantic_port_key() -> None:
    model = _model("MissingPortKey", {"unit": "decimal"})

    with pytest.raises(SemanticPortError, match="missing"):
        extract_semantic_port(model, "value", PortDirection.OUTPUT)


def test_extract_refuses_sibling_ad_hoc_metadata() -> None:
    metadata = semantic_port_metadata(PRODUCER)
    metadata["unit"] = "decimal"
    model = _model("SiblingMetadata", metadata)

    with pytest.raises(SemanticPortError, match="unsupported sibling"):
        extract_semantic_port(model, "value", PortDirection.OUTPUT)


@pytest.mark.parametrize("malformation", ["invalid", "partial", "extra"])
def test_extract_refuses_malformed_port_payloads(malformation: str) -> None:
    payload = PRODUCER.model_dump(mode="json")
    if malformation == "invalid":
        payload["concept"] = "invented_concept"
    elif malformation == "partial":
        del payload["convention"]
    else:
        payload["return_counts"] = "ad_hoc"
    model = _model("MalformedPortPayload", {PORT_SCHEMA_KEY: payload})

    with pytest.raises(SemanticPortError, match="invalid"):
        extract_semantic_port(model, "value", PortDirection.OUTPUT)


def test_extract_refuses_the_wrong_direction() -> None:
    producer_model, _ = _models()

    with pytest.raises(SemanticPortError, match="expected 'input'"):
        extract_semantic_port(producer_model, "value", PortDirection.INPUT)


def test_extract_refuses_metadata_that_contradicts_the_pydantic_field_shape() -> None:
    scalar_claiming_series = create_model(
        "ScalarClaimingSeries",
        value=(float, Field(json_schema_extra=semantic_port_metadata(PRODUCER))),
    )
    scalar_port = PRODUCER.model_copy(
        update={
            "shape": PortShape.SCALAR,
            "cardinality": PortCardinality.EXACTLY_ONE,
        }
    )
    series_claiming_scalar = _model(
        "SeriesClaimingScalar",
        semantic_port_metadata(scalar_port),
    )
    series_claiming_scalar_cardinality = _model(
        "SeriesClaimingScalarCardinality",
        semantic_port_metadata(
            PRODUCER.model_copy(
                update={"cardinality": PortCardinality.EXACTLY_ONE}
            )
        ),
    )

    with pytest.raises(SemanticPortError, match="field shape 'scalar'"):
        extract_semantic_port(
            scalar_claiming_series,
            "value",
            PortDirection.OUTPUT,
        )
    with pytest.raises(SemanticPortError, match="field shape 'ordered_series'"):
        extract_semantic_port(
            series_claiming_scalar,
            "value",
            PortDirection.OUTPUT,
        )
    with pytest.raises(SemanticPortError, match="invalid 'x-defined-quant-port'"):
        extract_semantic_port(
            series_claiming_scalar_cardinality,
            "value",
            PortDirection.OUTPUT,
        )


def test_extract_requires_nonempty_nonnullable_one_or_more_schema() -> None:
    without_minimum = create_model(
        "SeriesWithoutMinimum",
        value=(tuple[float, ...], Field(json_schema_extra=semantic_port_metadata(PRODUCER))),
    )
    nullable = create_model(
        "NullableSeries",
        value=(
            tuple[float, ...] | None,
            Field(min_length=1, json_schema_extra=semantic_port_metadata(PRODUCER)),
        ),
    )

    with pytest.raises(SemanticPortError, match="minItems >= 1"):
        extract_semantic_port(without_minimum, "value", PortDirection.OUTPUT)
    with pytest.raises(SemanticPortError, match="cannot be nullable"):
        extract_semantic_port(nullable, "value", PortDirection.OUTPUT)


@pytest.mark.parametrize(
    ("field_type", "port"),
    [
        (
            dict[str, float],
            PRODUCER.model_copy(
                update={
                    "shape": PortShape.SCALAR,
                    "cardinality": PortCardinality.EXACTLY_ONE,
                    "ordering": PortOrdering.NOT_APPLICABLE,
                }
            ),
        ),
        (set[float], PRODUCER),
    ],
)
def test_extract_refuses_mapping_and_unordered_set_shapes(
    field_type: object,
    port: SemanticPort,
) -> None:
    model = create_model(
        "StructurallyInvalidPort",
        value=(field_type, Field(json_schema_extra=semantic_port_metadata(port))),
    )

    with pytest.raises(SemanticPortError, match="unambiguous semantic-port shape"):
        extract_semantic_port(model, "value", PortDirection.OUTPUT)


def test_comparison_refuses_wrongly_directed_field_metadata() -> None:
    wrong_producer = PRODUCER.model_copy(update={"direction": PortDirection.INPUT})
    producer_model, consumer_model = _models(wrong_producer, CONSUMER)

    with pytest.raises(SemanticPortError, match="expected 'output'"):
        compare_semantic_ports(producer_model, "value", consumer_model, "value")


def test_port_difference_json_schema_closes_values_and_dimension_pairing() -> None:
    schema = PortDifference.model_json_schema()
    validator = jsonschema.Draft202012Validator(schema)
    valid = {
        "dimension": "unit",
        "producer_value": "decimal",
        "consumer_value": "price",
    }

    assert list(validator.iter_errors(valid)) == []
    assert list(
        validator.iter_errors(
            {**valid, "producer_value": "invented_component_dialect"}
        )
    )
    assert list(
        validator.iter_errors(
            {**valid, "producer_value": PortShape.ORDERED_SERIES.value}
        )
    )
    assert list(
        validator.iter_errors(
            {**valid, "consumer_value": PortUnit.DECIMAL.value}
        )
    )
    with pytest.raises(ValidationError, match="declared dimension"):
        PortDifference.model_validate(
            {**valid, "producer_value": "ordered_series"}
        )
    with pytest.raises(ValidationError, match="distinct values"):
        PortDifference.model_validate(
            {**valid, "consumer_value": PortUnit.DECIMAL.value}
        )


def test_port_difference_frequency_values_retain_their_enum_type_across_json() -> None:
    difference = PortDifference(
        dimension=PortDimension.FREQUENCY,
        producer_value=PortFrequency.NOT_APPLICABLE,
        consumer_value=PortFrequency.ANNUAL,
    )

    restored = PortDifference.model_validate_json(difference.model_dump_json())

    assert type(restored.producer_value) is PortFrequency
    assert type(restored.consumer_value) is PortFrequency
    assert restored == difference


def test_port_compatibility_schema_rejects_invalid_directions_and_result_shape() -> None:
    compatibility = PortCompatibility(
        producer=PRODUCER,
        consumer=CONSUMER,
        compatible=True,
        differences=(),
    )
    payload = compatibility.model_dump(mode="json")
    difference = PortDifference(
        dimension=PortDimension.CONVENTION,
        producer_value=PortConvention.SIMPLE_PERIODIC_RETURN,
        consumer_value=PortConvention.LOG_PERIODIC_RETURN,
    ).model_dump(mode="json")
    invalid_payloads = (
        {
            **payload,
            "producer": {**payload["producer"], "direction": PortDirection.INPUT.value},
        },
        {
            **payload,
            "consumer": {**payload["consumer"], "direction": PortDirection.OUTPUT.value},
        },
        {**payload, "compatible": True, "differences": [difference]},
        {**payload, "compatible": False, "differences": []},
    )
    validator = jsonschema.Draft202012Validator(PortCompatibility.model_json_schema())

    assert list(validator.iter_errors(payload)) == []
    for invalid in invalid_payloads:
        assert list(validator.iter_errors(invalid))
        with pytest.raises(ValidationError):
            PortCompatibility.model_validate(invalid)


def test_real_input_series_ports_reject_empty_schema_values_but_keep_contract_minimums() -> None:
    from defined_quant.market_data.log_return.component import (
        Inputs as LogReturnInputs,
    )
    from defined_quant.market_data.log_return.component import (
        log_return,
    )
    from defined_quant.market_data.simple_return.component import (
        Inputs as SimpleReturnInputs,
    )
    from defined_quant.market_data.simple_return.component import (
        simple_return,
    )
    from defined_quant.volatility.historical_volatility.component import (
        Inputs as HistoricalVolatilityInputs,
    )
    from defined_quant.volatility.historical_volatility.component import (
        historical_volatility,
    )

    cases = (
        (SimpleReturnInputs, {"prices": [], "price_kind": "adjusted"}, "prices"),
        (LogReturnInputs, {"prices": [], "price_kind": "adjusted"}, "prices"),
        (
            HistoricalVolatilityInputs,
            {"returns": [], "annualization_factor": 252.0, "return_kind": "log"},
            "returns",
        ),
    )
    for model, payload, field_name in cases:
        schema = model.model_json_schema()
        assert schema["properties"][field_name]["minItems"] == 1
        assert list(jsonschema.Draft202012Validator(schema).iter_errors(payload))

    with pytest.raises(DomainError, match="insufficient_prices"):
        simple_return([100.0], price_kind="adjusted")
    with pytest.raises(DomainError, match="insufficient_prices"):
        log_return([100.0], price_kind="adjusted")
    with pytest.raises(DomainError, match="insufficient_returns"):
        historical_volatility(
            [0.01],
            annualization_factor=252.0,
            return_kind="log",
        )


@pytest.mark.parametrize("field", ["returns", "return_kind"])
def test_real_log_return_ports_feed_historical_volatility(field: str) -> None:
    from defined_quant.market_data.log_return.component import Output as LogReturnOutput
    from defined_quant.volatility.historical_volatility.component import (
        Inputs as HistoricalVolatilityInputs,
    )

    compatibility = require_compatible_ports(
        LogReturnOutput,
        field,
        HistoricalVolatilityInputs,
        field,
    )

    assert compatibility.compatible
    assert compatibility.differences == ()


@pytest.mark.parametrize("field", ["returns", "return_kind"])
def test_real_simple_return_ports_are_incompatible_with_log_only_volatility(
    field: str,
) -> None:
    from defined_quant.market_data.simple_return.component import Output as SimpleReturnOutput
    from defined_quant.volatility.historical_volatility.component import (
        Inputs as HistoricalVolatilityInputs,
    )

    compatibility = compare_semantic_ports(
        SimpleReturnOutput,
        field,
        HistoricalVolatilityInputs,
        field,
    )

    assert not compatibility.compatible
    assert tuple(item.dimension for item in compatibility.differences) == (
        PortDimension.CONVENTION,
    )


def test_real_two_component_chain_preserves_explicit_convention_and_factor() -> None:
    from defined_quant.market_data.log_return.component import log_return
    from defined_quant.volatility.historical_volatility.component import (
        historical_volatility,
    )

    returns = log_return(
        (100.0, 103.0, 101.0, 105.0),
        price_kind="adjusted",
    )
    report = historical_volatility(
        returns.returns,
        return_kind=returns.return_kind,
        annualization_factor=252.0,
    )

    assert report.return_kind.value == "log"
    assert report.annualization_factor == 252.0
    assert report.sample_size == len(returns.returns)
    assert report.periodic_volatility > 0.0
    assert report.annualized_volatility > report.periodic_volatility
    assert len(report.derivations) == 2
