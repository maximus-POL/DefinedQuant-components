"""Canonical implementation for ``dq.market_data.monthly_return_matrix``."""

from __future__ import annotations

import math
import re
from typing import Literal

from defined_quant import preflight, subject_hash
from defined_quant.types import (
    AxisSpec,
    ChartKind,
    ChartSeries,
    ComponentOutput,
    Derivation,
    DomainError,
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
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictStr, model_validator

COMPONENT_ID = "dq.market_data.monthly_return_matrix"
COMPONENT_VERSION = "0.1.1"
FORMULA = "r_m = (P_m − P_{m−1}) / P_{m−1}"
_YEAR_MONTH = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")
_DISCLOSURES = (
    "completed_month_end_assertion: The caller explicitly asserted that every price is a "
    "completed month-end observation.",
    "monthly_gap_policy: Month labels were required to be consecutive; no return spans a "
    "missing month.",
)


class Inputs(BaseModel):
    """Consecutive completed month-end prices with explicit price semantics."""

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
            frequency=PortFrequency.MONTHLY,
            provenance_requirement=PortProvenanceRequirement.NOT_REQUIRED,
        ),
    )
    months: tuple[StrictStr, ...] = Field(..., min_length=1)
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
    observation_kind: StrictStr


class Output(ComponentOutput):
    """Monthly simple returns plus a calendar heatmap specification."""

    monthly_returns: tuple[float, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.PERIODIC_RETURN_SERIES,
            unit=PortUnit.DECIMAL,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=PortConvention.SIMPLE_PERIODIC_RETURN,
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.MONTHLY,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    return_kind: Literal[ReturnKind.SIMPLE] = ReturnKind.SIMPLE
    return_months: tuple[str, ...] = Field(..., min_length=1)
    price_kind: PriceKind
    observation_kind: Literal["completed_month_end"] = "completed_month_end"
    gap_check: Literal["verified_consecutive_months"] = "verified_consecutive_months"
    derivations: tuple[Derivation, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_complete_monthly_lineage(self) -> Output:
        if len(self.return_months) != len(self.monthly_returns):
            raise ValueError("return months must align with monthly returns")
        if len(self.derivations) != len(self.monthly_returns):
            raise ValueError("every monthly return must have one derivation")
        for index, derivation in enumerate(self.derivations):
            if derivation.output != OutputRef(field="monthly_returns", index=index):
                raise ValueError("monthly return derivations must be ordered")
            if derivation.inputs != (
                InputRef(field="prices", index=index),
                InputRef(field="prices", index=index + 1),
            ):
                raise ValueError("monthly lineage must reference adjacent month-end prices")
            expected = f"(prices[{index + 1}] - prices[{index}]) / prices[{index}]"
            if derivation.expression != expected:
                raise ValueError("monthly return expression is not canonical")
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


def _month_ordinal(label: str) -> int:
    match = _YEAR_MONTH.fullmatch(label)
    if match is None:
        raise _blocking_error(
            "invalid_month_labels",
            "Every month label must use the YYYY-MM format.",
        )
    return int(match.group("year")) * 12 + int(match.group("month")) - 1


def _simple_return(previous: float, current: float) -> float:
    value = (current - previous) / previous
    if not math.isfinite(value) or value == -1.0:
        raise _blocking_error(
            "non_finite_result",
            "A finite positive price pair produced an unrepresentable monthly return.",
        )
    return value


def _visualization(
    *,
    months: tuple[str, ...],
    returns: tuple[float, ...],
    price_kind: PriceKind,
    assumptions: tuple[str, ...],
    warnings: tuple[str, ...],
) -> VisualizationSpec:
    year_count = len({month[:4] for month in months})
    return VisualizationSpec(
        schema_version=1,
        id="monthly_return_heatmap",
        kind=ChartKind.HEATMAP,
        title="Monthly simple-return heatmap",
        alt_text=(
            f"Calendar heatmap of {len(returns)} monthly simple returns from "
            f"{price_kind.value} completed month-end prices, ranging from "
            f"{min(returns):.6g} to {max(returns):.6g}."
        ),
        categories=months,
        series=(
            ChartSeries(
                key="monthly_returns",
                label="Monthly simple returns",
                values=returns,
            ),
        ),
        x_axis=AxisSpec(label="Calendar month"),
        y_axis=AxisSpec(
            label="Simple return",
            unit=Unit.DECIMAL,
            number_format=NumberFormat.PERCENT,
        ),
        caption=(
            f"{FORMULA}. Red is negative, green is positive, and the color scale is "
            "symmetric around zero."
        ),
        assumptions=assumptions,
        warnings=warnings,
        height=max(440, min(1000, 180 + year_count * 36)),
    )


def monthly_return_matrix(
    prices: tuple[float, ...] | list[float],
    months: tuple[str, ...] | list[str],
    *,
    price_kind: PriceKind | str,
    observation_kind: str,
) -> Output:
    """Calculate consecutive monthly returns and expose them as a calendar heatmap."""

    inputs = Inputs.model_validate(
        {
            "prices": prices,
            "months": months,
            "price_kind": price_kind,
            "observation_kind": observation_kind,
        }
    )
    violations = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))
    ordinals = tuple(_month_ordinal(month) for month in inputs.months)
    if any(right != left + 1 for left, right in zip(ordinals, ordinals[1:], strict=False)):
        raise _blocking_error(
            "non_consecutive_months",
            "Completed month-end labels must be consecutive without missing months.",
        )

    returns = tuple(
        _simple_return(previous, current)
        for previous, current in zip(inputs.prices, inputs.prices[1:], strict=False)
    )
    return_months = inputs.months[1:]
    derivations = tuple(
        Derivation(
            output=OutputRef(field="monthly_returns", index=index),
            inputs=(
                InputRef(field="prices", index=index),
                InputRef(field="prices", index=index + 1),
            ),
            expression=f"(prices[{index + 1}] - prices[{index}]) / prices[{index}]",
            value=value,
        )
        for index, value in enumerate(returns)
    )
    warnings = tuple(violation.message for violation in violations)
    assumptions = (
        "Each price is caller-asserted to be the completed month-end observation for its label.",
        "All observations refer to one instrument in a consistent price unit.",
        f"Price semantics are caller-declared as {inputs.price_kind.value}.",
    )
    transformations = (
        f"Computed each consecutive completed-month return as {FORMULA}.",
        "Placed every return in the calendar cell for its interval-end month.",
    )
    visualization = _visualization(
        months=return_months,
        returns=returns,
        price_kind=inputs.price_kind,
        assumptions=assumptions,
        warnings=warnings,
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
        visualizations=(visualization,),
        monthly_returns=returns,
        return_months=return_months,
        price_kind=inputs.price_kind,
    )


__all__ = [
    "COMPONENT_ID",
    "COMPONENT_VERSION",
    "FORMULA",
    "Inputs",
    "Output",
    "monthly_return_matrix",
]
