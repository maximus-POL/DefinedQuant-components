"""Canonical models and implementation for ``dq.market_data.rebased_price_index``."""

from __future__ import annotations

import math
import sys
from datetime import datetime
from fractions import Fraction
from typing import Literal

from defined_quant import preflight, subject_hash
from defined_quant.types import (
    AxisSpec,
    ChartKind,
    ChartSeries,
    ComponentOutput,
    Derivation,
    DomainError,
    Frequency,
    InputRef,
    NumberFormat,
    OutputRef,
    PriceKind,
    Unit,
    VisualizationSpec,
)
from defined_quant_protocol import (
    PortCardinality,
    PortConcept,
    PortConvention,
    PortDirection,
    PortFrequency,
    PortOrdering,
    PortProvenanceRequirement,
    PortShape,
    PortUnit,
    semantic_port_metadata,
)
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)

COMPONENT_ID = "dq.market_data.rebased_price_index"
COMPONENT_VERSION = "0.1.0"
FORMULA = "Iₜ = B × (Pₜ / P_b)"
_DISCLOSURES = (
    "base_explicit: The base observation and base value were supplied explicitly; no base "
    "was inferred.",
    "gap_check_not_assessed: This component does not apply a calendar-aware gap policy.",
)


class Inputs(BaseModel):
    """Ordered prices and the explicit base used to normalize them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prices: tuple[StrictFloat, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.INPUT,
            concept=PortConcept.PRICE_SERIES,
            unit=PortUnit.PRICE,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=PortConvention.ORDERED_POSITIVE_PRICES,
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.NOT_REQUIRED,
        ),
    )
    price_kind: PriceKind = Field(
        ...,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.INPUT,
            concept=PortConcept.PRICE_ADJUSTMENT_KIND,
            unit=PortUnit.UNITLESS,
            shape=PortShape.SCALAR,
            cardinality=PortCardinality.EXACTLY_ONE,
            convention=PortConvention.ADJUSTED_OR_UNADJUSTED_PRICE_KIND,
            ordering=PortOrdering.NOT_APPLICABLE,
            frequency=PortFrequency.NOT_APPLICABLE,
            provenance_requirement=PortProvenanceRequirement.NOT_REQUIRED,
        ),
    )
    base_index: StrictInt
    base_value: StrictFloat = Field(...)
    timestamps: tuple[AwareDatetime, ...] | None = None
    declared_frequency: Frequency | None = None


class Output(ComponentOutput):
    """A unitless price index preserving the complete supplied path."""

    index_values: tuple[float, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.REBASED_PRICE_INDEX_SERIES,
            unit=PortUnit.UNITLESS,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=PortConvention.EXPLICIT_BASE_OBSERVATION_REBASING,
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    price_kind: PriceKind
    base_index: int = Field(ge=0)
    base_value: float = Field(gt=0.0, allow_inf_nan=False)
    index_timestamps: tuple[AwareDatetime, ...] | None
    declared_frequency: Frequency | None
    ordering_status: Literal["verified", "unverified"]
    gap_check: Literal["not_assessed"] = "not_assessed"
    derivations: tuple[Derivation, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_complete_lineage(self) -> Output:
        if len(self.derivations) != len(self.index_values):
            raise ValueError("every index value must have exactly one derivation")
        for index, derivation in enumerate(self.derivations):
            if derivation.output != OutputRef(field="index_values", index=index):
                raise ValueError("index derivations must be ordered and complete")
            expected_inputs: tuple[InputRef, ...] = (
                InputRef(field="prices", index=self.base_index),
                InputRef(field="base_value", index=0),
            )
            expected_expression = "base_value at base_index"
            if index != self.base_index:
                expected_inputs = (
                    InputRef(field="prices", index=index),
                    InputRef(field="prices", index=self.base_index),
                    InputRef(field="base_value", index=0),
                )
                expected_expression = (
                    f"base_value * (prices[{index}] / prices[{self.base_index}])"
                )
            if derivation.inputs != expected_inputs:
                raise ValueError("index derivation inputs must match the explicit base")
            if derivation.expression != expected_expression:
                raise ValueError("index derivation expression must match executed indexing")
        return self


def _blocking_error(rule: str, message: str) -> DomainError:
    full_message = f"{rule}: {message}"
    return DomainError(
        full_message,
        component_id=COMPONENT_ID,
        details={
            "violations": [
                {
                    "rule": rule,
                    "severity": "blocking",
                    "message": full_message,
                    "context": {},
                }
            ]
        },
    )


def _rebased_value(base_value: float, price: float, base_price: float) -> float:
    ratio = price / base_price
    value = base_value * ratio
    if (
        math.isfinite(value)
        and ratio >= sys.float_info.min
        and value >= sys.float_info.min
    ):
        return value
    try:
        return float(
            Fraction.from_float(base_value)
            * Fraction.from_float(price)
            / Fraction.from_float(base_price)
        )
    except OverflowError:
        return math.inf


def _visualization(
    *,
    inputs: Inputs,
    index_values: tuple[float, ...],
    assumptions: tuple[str, ...],
    warnings: tuple[str, ...],
) -> VisualizationSpec:
    categories = (
        tuple(str(index) for index in range(len(index_values)))
        if inputs.timestamps is None
        else tuple(timestamp.isoformat() for timestamp in inputs.timestamps)
    )
    return VisualizationSpec(
        id="rebased_price_index",
        kind=ChartKind.LINE,
        title=f"Rebased price index (base = {inputs.base_value:.6g})",
        alt_text=(
            f"Line chart of {len(index_values)} rebased price observations from "
            f"{inputs.price_kind.value} prices, ranging from {min(index_values):.6g} "
            f"to {max(index_values):.6g}."
        ),
        categories=categories,
        series=(
            ChartSeries(
                key="rebased_price_index",
                label="Rebased price index",
                values=index_values,
            ),
        ),
        x_axis=AxisSpec(
            label="Observation" if inputs.timestamps is None else "Observation time"
        ),
        y_axis=AxisSpec(
            label="Rebased price index",
            unit=Unit.UNITLESS,
            number_format=NumberFormat.DECIMAL,
        ),
        caption=(
            f"{FORMULA}; b = {inputs.base_index}, B = {inputs.base_value:.6g}. "
            "This is a normalized price path, not a return series."
        ),
        assumptions=assumptions,
        warnings=warnings,
    )


def rebased_price_index(
    prices: tuple[float, ...] | list[float],
    *,
    price_kind: PriceKind | str,
    base_index: int,
    base_value: float,
    timestamps: tuple[datetime | str, ...] | list[datetime | str] | None = None,
    declared_frequency: Frequency | str | None = None,
) -> Output:
    """Normalize an ordered positive price path to an explicit base observation."""

    inputs = Inputs.model_validate(
        {
            "prices": prices,
            "price_kind": price_kind,
            "base_index": base_index,
            "base_value": base_value,
            "timestamps": timestamps,
            "declared_frequency": declared_frequency,
        }
    )
    violations = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))
    if inputs.base_index >= len(inputs.prices):
        raise _blocking_error(
            "base_index_out_of_range",
            "The base index must identify an observation in prices.",
        )

    base_price = inputs.prices[inputs.base_index]
    values_list: list[float] = []
    for index, price in enumerate(inputs.prices):
        value = (
            inputs.base_value
            if index == inputs.base_index
            else _rebased_value(inputs.base_value, price, base_price)
        )
        if not math.isfinite(value) or value <= 0.0:
            raise _blocking_error(
                "non_finite_result",
                "Finite positive inputs produced an unrepresentable rebased value.",
            )
        values_list.append(value)
    index_values = tuple(values_list)

    derivations = tuple(
        Derivation(
            output=OutputRef(field="index_values", index=index),
            inputs=(
                (
                    InputRef(field="prices", index=inputs.base_index),
                    InputRef(field="base_value", index=0),
                )
                if index == inputs.base_index
                else (
                    InputRef(field="prices", index=index),
                    InputRef(field="prices", index=inputs.base_index),
                    InputRef(field="base_value", index=0),
                )
            ),
            expression=(
                "base_value at base_index"
                if index == inputs.base_index
                else f"base_value * (prices[{index}] / prices[{inputs.base_index}])"
            ),
            value=value,
        )
        for index, value in enumerate(index_values)
    )
    warnings = tuple(violation.message for violation in violations)
    ordering_status: Literal["verified", "unverified"] = (
        "verified" if inputs.timestamps is not None else "unverified"
    )
    assumptions = (
        "All observations refer to the same instrument and price unit.",
        f"Price semantics are caller-declared as {inputs.price_kind.value}.",
    )
    transformations = (
        f"Computed every index value as {FORMULA}.",
        "Preserved the supplied observation order without sorting or resampling.",
    )
    visualization = _visualization(
        inputs=inputs,
        index_values=index_values,
        assumptions=assumptions,
        warnings=warnings,
    )
    return Output(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        subject_hash=subject_hash(COMPONENT_ID),
        unit=Unit.UNITLESS,
        assumptions=assumptions,
        disclosures=_DISCLOSURES,
        warnings=warnings,
        transformations=transformations,
        derivations=derivations,
        visualizations=(visualization,),
        index_values=index_values,
        price_kind=inputs.price_kind,
        base_index=inputs.base_index,
        base_value=inputs.base_value,
        index_timestamps=inputs.timestamps,
        declared_frequency=inputs.declared_frequency,
        ordering_status=ordering_status,
    )


__all__ = [
    "COMPONENT_ID",
    "COMPONENT_VERSION",
    "FORMULA",
    "Inputs",
    "Output",
    "rebased_price_index",
]
