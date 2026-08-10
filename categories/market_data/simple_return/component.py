"""Canonical models and implementation for ``dq.market_data.simple_return``."""

from __future__ import annotations

import math
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
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

COMPONENT_ID = "dq.market_data.simple_return"
COMPONENT_VERSION = "0.3.1"
FORMULA = "rₜ = (Pₜ − Pₜ₋₁) / Pₜ₋₁"
_GAP_DISCLOSURES = (
    "gap_check_not_assessed: This component does not apply a calendar-aware gap policy, "
    "so gaps were not assessed.",
)


class Inputs(BaseModel):
    """Ordered price observations and the conventions needed to interpret them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prices: tuple[float, ...] = Field(
        ...,
        json_schema_extra={
            "unit": "price",
            "convention": "ordered_observations_for_one_instrument",
        },
    )
    price_kind: PriceKind = Field(
        ...,
        description="Whether the caller supplied adjusted or unadjusted prices.",
    )
    timestamps: tuple[AwareDatetime, ...] | None = Field(
        default=None,
        description="Optional strictly increasing timestamps aligned one-to-one with prices.",
    )
    declared_frequency: Frequency | None = Field(
        default=None,
        description=(
            "Optional caller declaration. In this version it is disclosure only; no "
            "market-calendar gap inference is performed."
        ),
    )


class Output(ComponentOutput):
    """Simple periodic returns plus explicit alignment and interpretation state."""

    returns: tuple[float, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra={
            "unit": "decimal",
            "convention": "simple_periodic_return",
        },
    )
    return_kind: Literal[ReturnKind.SIMPLE] = ReturnKind.SIMPLE
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
            expected_expression = (
                f"(prices[{index + 1}] - prices[{index}]) / prices[{index}]"
            )
            if derivation.expression != expected_expression:
                raise ValueError("return derivation expression must match the executed indexing")
        return self


def _non_finite_result() -> DomainError:
    message = (
        "non_finite_result: A finite positive price pair produced a simple return "
        "that cannot be represented without overflow or rounding to total loss."
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


def _simple_return(previous: float, current: float) -> float:
    value = (current - previous) / previous
    if not math.isfinite(value) or value == -1.0:
        raise _non_finite_result()
    return value


def _derivations(returns: tuple[float, ...]) -> tuple[Derivation, ...]:
    return tuple(
        Derivation(
            output=OutputRef(field="returns", index=index),
            inputs=(
                InputRef(field="prices", index=index),
                InputRef(field="prices", index=index + 1),
            ),
            expression=f"(prices[{index + 1}] - prices[{index}]) / prices[{index}]",
            value=value,
        )
        for index, value in enumerate(returns)
    )


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
        f"Line chart of {len(returns)} simple periodic return"
        f"{'' if len(returns) == 1 else 's'} from {inputs.price_kind.value} prices. "
        f"Values range from {minimum:.6g} to {maximum:.6g} in decimal units."
    )
    caption = (
        f"Each value is {FORMULA}. "
        f"Price kind: {inputs.price_kind.value}; ordering: {ordering_status}; "
        "calendar-aware gaps: not assessed."
    )
    return VisualizationSpec(
        id="simple_periodic_returns",
        kind=ChartKind.LINE,
        title="Simple periodic returns",
        alt_text=alt_text,
        categories=categories,
        series=(
            ChartSeries(
                key="simple_return",
                label="Simple return",
                values=returns,
            ),
        ),
        x_axis=AxisSpec(label=x_axis_label, unit=Unit.UNITLESS),
        y_axis=AxisSpec(
            label="Simple return",
            unit=Unit.DECIMAL,
            number_format=NumberFormat.PERCENT,
        ),
        caption=caption,
        assumptions=assumptions,
        warnings=warnings,
    )


def simple_return(
    prices: tuple[float, ...] | list[float],
    *,
    price_kind: PriceKind | str,
    timestamps: tuple[datetime | str, ...] | list[datetime | str] | None = None,
    declared_frequency: Frequency | str | None = None,
) -> Output:
    """Return precision-preserving simple returns for each successive observation."""

    inputs = Inputs.model_validate(
        {
            "prices": prices,
            "price_kind": price_kind,
            "timestamps": timestamps,
            "declared_frequency": declared_frequency,
        }
    )
    violations = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))

    returns = tuple(
        _simple_return(previous, current)
        for previous, current in zip(inputs.prices, inputs.prices[1:], strict=False)
    )
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
        disclosures=_GAP_DISCLOSURES,
        warnings=warnings,
        transformations=transformations,
        derivations=_derivations(returns),
        visualizations=visualizations,
        returns=returns,
        price_kind=inputs.price_kind,
        return_timestamps=(
            inputs.timestamps[1:] if inputs.timestamps is not None else None
        ),
        declared_frequency=inputs.declared_frequency,
        ordering_status=ordering_status,
    )
