"""Canonical models and implementation for ``dq.market_data.simple_return``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from defined_quant import preflight, subject_hash
from defined_quant.types import (
    AxisSpec,
    ChartKind,
    ChartSeries,
    ComponentOutput,
    Frequency,
    NumberFormat,
    PriceKind,
    ReturnKind,
    Unit,
    VisualizationSpec,
)
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

COMPONENT_ID = "dq.market_data.simple_return"
COMPONENT_VERSION = "0.1.0"


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
            "Optional caller declaration. In v0.1 it is disclosure only; no market-calendar "
            "gap inference is performed."
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
        "Each value is r_t = p_t / p_(t-1) - 1. "
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
    """Return ``prices[i] / prices[i-1] - 1`` for each successive observation."""

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
        current / previous - 1.0
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
        "Computed each value as current_price / previous_price - 1.",
        "Associated each return with the interval-end timestamp when timestamps were supplied.",
    )
    visualization = _visualization(
        returns=returns,
        inputs=inputs,
        assumptions=assumptions,
        warnings=warnings,
        ordering_status=ordering_status,
    )

    return Output(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        subject_hash=subject_hash(COMPONENT_ID),
        unit=Unit.DECIMAL,
        assumptions=assumptions,
        warnings=warnings,
        transformations=transformations,
        visualizations=(visualization,),
        returns=returns,
        price_kind=inputs.price_kind,
        return_timestamps=(
            inputs.timestamps[1:] if inputs.timestamps is not None else None
        ),
        declared_frequency=inputs.declared_frequency,
        ordering_status=ordering_status,
    )
