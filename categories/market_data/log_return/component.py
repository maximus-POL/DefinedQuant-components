"""Canonical models and implementation for ``dq.market_data.log_return``."""

from __future__ import annotations

import math
import sys
from datetime import datetime
from typing import Literal

from defined_quant import preflight, subject_hash
from defined_quant.types import (
    MAX_VISUALIZATION_POINTS,
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
    ReturnKind,
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
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictFloat, model_validator

COMPONENT_ID = "dq.market_data.log_return"
COMPONENT_VERSION = "0.1.0"
FORMULA = (
    "rₜ = log1p((Pₜ − Pₜ₋₁) / Pₜ₋₁) if Pₜ ≥ Pₜ₋₁ / 2 and Pₜ₋₁ ≥ Pₜ / 2; "
    "otherwise q = Pₜ / Pₜ₋₁ and rₜ = log(q) if 2⁻¹⁰²² ≤ q < ∞, else "
    "log(Pₜ) − log(Pₜ₋₁)"
)
_DISCLOSURES = (
    "gap_check_not_assessed: This component does not apply a calendar-aware gap policy, "
    "so gaps were not assessed.",
    "frequency_not_inferred: Observation frequency is retained only when the caller declares "
    "it; this component does not infer frequency from timestamps.",
    "adjustment_method_not_audited: The caller's adjusted or unadjusted label is preserved, "
    "but the upstream provider's adjustment methodology is not audited.",
)


class Inputs(BaseModel):
    """Ordered price observations and the conventions needed to interpret them."""

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
        description="Whether the caller supplied adjusted or unadjusted prices.",
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
    timestamps: tuple[AwareDatetime, ...] | None = Field(
        default=None,
        description="Optional strictly increasing timestamps aligned one-to-one with prices.",
    )
    declared_frequency: Frequency | None = Field(
        default=None,
        description=(
            "Optional caller declaration. It is disclosure only; no market-calendar gap "
            "inference is performed."
        ),
    )


class Output(ComponentOutput):
    """Log periodic returns plus explicit alignment and interpretation state."""

    returns: tuple[float, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.PERIODIC_RETURN_SERIES,
            unit=PortUnit.DECIMAL,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=PortConvention.LOG_PERIODIC_RETURN,
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    return_kind: Literal[ReturnKind.LOG] = Field(
        default=ReturnKind.LOG,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.RETURN_CONVENTION,
            unit=PortUnit.UNITLESS,
            shape=PortShape.SCALAR,
            cardinality=PortCardinality.EXACTLY_ONE,
            convention=PortConvention.LOG_PERIODIC_RETURN,
            ordering=PortOrdering.NOT_APPLICABLE,
            frequency=PortFrequency.NOT_APPLICABLE,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    price_kind: PriceKind
    return_timestamps: tuple[AwareDatetime, ...] | None
    declared_frequency: Frequency | None
    ordering_status: Literal["verified", "unverified"]
    gap_check: Literal["not_assessed"] = "not_assessed"
    derivations: tuple[Derivation, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_complete_return_lineage(self) -> Output:
        if len(self.derivations) != len(self.returns):
            raise ValueError("every return must have exactly one derivation")
        for index, derivation in enumerate(self.derivations):
            if derivation.output != OutputRef(field="returns", index=index):
                raise ValueError("return derivations must be ordered and complete")
            input_locations = tuple((item.field, item.index) for item in derivation.inputs)
            if input_locations != (("prices", index), ("prices", index + 1)):
                raise ValueError("each return derivation must reference its two source prices")
            allowed_expressions = {
                f"log1p((prices[{index + 1}] - prices[{index}]) / prices[{index}])",
                f"log(prices[{index + 1}] / prices[{index}])",
                f"log(prices[{index + 1}]) - log(prices[{index}])",
            }
            if derivation.expression not in allowed_expressions:
                raise ValueError("return derivation expression must identify the executed branch")
        return self


def _non_finite_result() -> DomainError:
    message = (
        "non_finite_result: A finite positive price pair produced a log return that cannot "
        "be represented as a finite binary64 value."
    )
    return DomainError(
        message,
        component_id=COMPONENT_ID,
        details={
            "violations": [
                {
                    "rule": "non_finite_result",
                    "severity": "blocking",
                    "message": message,
                    "context": {},
                }
            ]
        },
    )


def _log_return(previous: float, current: float, *, index: int) -> tuple[float, str]:
    # Sterbenz's lemma makes the subtraction exact within this factor-of-two
    # neighborhood, while log1p preserves changes close to zero.
    if current >= previous / 2.0 and previous >= current / 2.0:
        relative = (current - previous) / previous
        value = math.log1p(relative)
        expression = f"log1p((prices[{index + 1}] - prices[{index}]) / prices[{index}])"
    else:
        ratio = current / previous
        if math.isfinite(ratio) and ratio >= sys.float_info.min:
            value = math.log(ratio)
            expression = f"log(prices[{index + 1}] / prices[{index}])"
        else:
            value = math.log(current) - math.log(previous)
            expression = f"log(prices[{index + 1}]) - log(prices[{index}])"
    if not math.isfinite(value):
        raise _non_finite_result()
    return value, expression


def _calculate(
    prices: tuple[float, ...],
) -> tuple[tuple[float, ...], tuple[Derivation, ...]]:
    returns: list[float] = []
    derivations: list[Derivation] = []
    for index, (previous, current) in enumerate(
        zip(prices, prices[1:], strict=False)
    ):
        value, expression = _log_return(previous, current, index=index)
        returns.append(value)
        derivations.append(
            Derivation(
                output=OutputRef(field="returns", index=index),
                inputs=(
                    InputRef(field="prices", index=index),
                    InputRef(field="prices", index=index + 1),
                ),
                expression=expression,
                value=value,
            )
        )
    return tuple(returns), tuple(derivations)


def _visualization(
    *,
    returns: tuple[float, ...],
    inputs: Inputs,
    assumptions: tuple[str, ...],
    warnings: tuple[str, ...],
    ordering_status: Literal["verified", "unverified"],
) -> VisualizationSpec:
    if inputs.timestamps is None:
        categories = tuple(str(index) for index in range(1, len(inputs.prices)))
        x_axis_label = "Observation end index"
    else:
        categories = tuple(timestamp.isoformat() for timestamp in inputs.timestamps[1:])
        x_axis_label = "Period end"

    minimum = min(returns)
    maximum = max(returns)
    alt_text = (
        f"Line chart of {len(returns)} log periodic return"
        f"{'' if len(returns) == 1 else 's'} from {inputs.price_kind.value} prices. "
        f"Values range from {minimum:.6g} to {maximum:.6g} in decimal units."
    )
    caption = (
        f"Each value is {FORMULA}. "
        f"Price kind: {inputs.price_kind.value}; ordering: {ordering_status}; "
        "calendar-aware gaps: not assessed."
    )
    return VisualizationSpec(
        id="log_periodic_returns",
        kind=ChartKind.LINE,
        title="Log periodic returns",
        alt_text=alt_text,
        categories=categories,
        series=(
            ChartSeries(
                key="log_return",
                label="Log return",
                values=returns,
            ),
        ),
        x_axis=AxisSpec(label=x_axis_label, unit=Unit.UNITLESS),
        y_axis=AxisSpec(
            label="Log return",
            unit=Unit.DECIMAL,
            number_format=NumberFormat.DECIMAL,
        ),
        caption=caption,
        assumptions=assumptions,
        warnings=warnings,
    )


def log_return(
    prices: tuple[float, ...] | list[float],
    *,
    price_kind: PriceKind | str,
    timestamps: tuple[datetime | str, ...] | list[datetime | str] | None = None,
    declared_frequency: Frequency | str | None = None,
) -> Output:
    """Return precision-preserving log returns for each successive observation."""

    inputs = Inputs.model_validate(
        {
            "prices": prices,
            "price_kind": price_kind,
            "timestamps": timestamps,
            "declared_frequency": declared_frequency,
        }
    )
    violations = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))
    returns, derivations = _calculate(inputs.prices)
    warnings = tuple(violation.message for violation in violations)
    ordering_status: Literal["verified", "unverified"] = (
        "verified" if inputs.timestamps is not None else "unverified"
    )
    assumptions = (
        "Successive observations refer to the same instrument and price unit.",
        (
            f"Price semantics are caller-declared as {inputs.price_kind.value}; "
            "the provider's adjustment methodology is accepted as supplied."
        ),
    )
    transformations = (
        f"Computed each value as {FORMULA}.",
        "Associated each return with the interval-end timestamp when timestamps were supplied.",
    )
    visualizations = (
        (
            _visualization(
                returns=returns,
                inputs=inputs,
                assumptions=assumptions,
                warnings=warnings,
                ordering_status=ordering_status,
            ),
        )
        if len(returns) <= MAX_VISUALIZATION_POINTS
        else ()
    )

    return Output(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        subject_hash=subject_hash(COMPONENT_ID),
        unit=Unit.DECIMAL,
        assumptions=assumptions,
        disclosures=_DISCLOSURES,
        warnings=warnings,
        transformations=transformations,
        derivations=derivations,
        visualizations=visualizations,
        returns=returns,
        price_kind=inputs.price_kind,
        return_timestamps=(
            inputs.timestamps[1:] if inputs.timestamps is not None else None
        ),
        declared_frequency=inputs.declared_frequency,
        ordering_status=ordering_status,
    )


__all__ = [
    "COMPONENT_ID",
    "COMPONENT_VERSION",
    "FORMULA",
    "Inputs",
    "Output",
    "log_return",
]
