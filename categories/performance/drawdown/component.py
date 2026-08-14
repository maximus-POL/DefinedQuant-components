"""Canonical models and implementation for ``dq.performance.drawdown``."""

from __future__ import annotations

import math
from datetime import datetime
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
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictFloat, model_validator

COMPONENT_ID = "dq.performance.drawdown"
COMPONENT_VERSION = "0.1.1"
FORMULA = "Dₜ = Pₜ / max(P₀, …, Pₜ) − 1; MDD = minₜ Dₜ"
_DISCLOSURES = (
    "running_peak_convention: Equal highs replace the running-peak index with the latest high.",
    "episode_tie_convention: The earliest maximum-drawdown trough is selected.",
    "recovery_convention: For a negative selected drawdown, recovery is the first later "
    "observation at or above the selected peak value; when maximum drawdown is zero, the "
    "shared peak/trough observation is treated as recovered immediately.",
    "gap_check_not_assessed: Calendar-aware gaps and elapsed-time duration were not assessed.",
)


class Inputs(BaseModel):
    """Ordered positive prices used to measure peak-relative losses."""

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
    timestamps: tuple[AwareDatetime, ...] | None = None
    declared_frequency: Frequency | None = None


class Output(ComponentOutput):
    """Full underwater series and one deterministic maximum-drawdown episode."""

    drawdowns: tuple[float, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.DRAWDOWN_SERIES,
            unit=PortUnit.DECIMAL,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=PortConvention.RUNNING_PEAK_DRAWDOWN,
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    maximum_drawdown: float = Field(
        ...,
        ge=-1.0,
        le=0.0,
        allow_inf_nan=False,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.MAXIMUM_DRAWDOWN,
            unit=PortUnit.DECIMAL,
            shape=PortShape.SCALAR,
            cardinality=PortCardinality.EXACTLY_ONE,
            convention=PortConvention.RUNNING_PEAK_DRAWDOWN,
            ordering=PortOrdering.NOT_APPLICABLE,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    price_kind: PriceKind
    running_peak_indices: tuple[int, ...] = Field(..., min_length=1)
    peak_index: int = Field(ge=0)
    trough_index: int = Field(ge=0)
    recovery_index: int | None = Field(default=None, ge=0)
    recovered: bool
    drawdown_timestamps: tuple[AwareDatetime, ...] | None
    peak_timestamp: AwareDatetime | None
    trough_timestamp: AwareDatetime | None
    recovery_timestamp: AwareDatetime | None
    declared_frequency: Frequency | None
    ordering_status: Literal["verified", "unverified"]
    gap_check: Literal["not_assessed"] = "not_assessed"
    derivations: tuple[Derivation, ...] = Field(..., min_length=2)

    @model_validator(mode="after")
    def validate_episode_and_lineage(self) -> Output:
        count = len(self.drawdowns)
        if len(self.running_peak_indices) != count:
            raise ValueError("running peak indexes must align with drawdowns")
        if not (0 <= self.peak_index <= self.trough_index < count):
            raise ValueError("peak and trough indexes must form an ordered episode")
        if self.peak_index != self.running_peak_indices[self.trough_index]:
            raise ValueError(
                "selected peak index must equal the running peak at the selected trough"
            )
        if self.recovered != (self.recovery_index is not None):
            raise ValueError("recovered must agree with recovery_index")
        if self.recovery_index is not None and self.recovery_index < self.trough_index:
            raise ValueError("recovery cannot precede the selected trough")
        if self.maximum_drawdown != self.drawdowns[self.trough_index]:
            raise ValueError("maximum drawdown must equal the selected trough value")
        if self.trough_index != self.drawdowns.index(min(self.drawdowns)):
            raise ValueError("the earliest minimum drawdown must be selected")
        if len(self.derivations) != count + 1:
            raise ValueError("every drawdown and maximum drawdown need derivations")

        for index, derivation in enumerate(self.derivations[:-1]):
            peak_index = self.running_peak_indices[index]
            if derivation.output != OutputRef(field="drawdowns", index=index):
                raise ValueError("drawdown derivations must be ordered and complete")
            if peak_index == index:
                expected_inputs: tuple[InputRef, ...] = (
                    InputRef(field="prices", index=index),
                )
                expected_expression = f"prices[{index}] is running peak; drawdown = 0"
            else:
                expected_inputs = (
                    InputRef(field="prices", index=index),
                    InputRef(field="prices", index=peak_index),
                )
                expected_expression = f"prices[{index}] / prices[{peak_index}] - 1"
            if derivation.inputs != expected_inputs:
                raise ValueError("drawdown lineage must reference current and peak prices")
            if derivation.expression != expected_expression:
                raise ValueError("drawdown derivation expression is not canonical")

        maximum = self.derivations[-1]
        if maximum.output != OutputRef(field="maximum_drawdown", index=0):
            raise ValueError("maximum drawdown derivation must target scalar index zero")
        if self.peak_index == self.trough_index:
            maximum_inputs: tuple[InputRef, ...] = (
                InputRef(field="prices", index=self.peak_index),
            )
            expected_expression = "zero drawdown at initial selected peak"
        else:
            maximum_inputs = (
                InputRef(field="prices", index=self.trough_index),
                InputRef(field="prices", index=self.peak_index),
            )
            expected_expression = (
                f"prices[{self.trough_index}] / prices[{self.peak_index}] - 1"
            )
        if maximum.inputs != maximum_inputs or maximum.expression != expected_expression:
            raise ValueError("maximum drawdown lineage must match the selected episode")
        return self


def _blocking_error(message: str) -> DomainError:
    full_message = f"non_finite_result: {message}"
    return DomainError(
        full_message,
        component_id=COMPONENT_ID,
        details={
            "violations": [
                {
                    "rule": "non_finite_result",
                    "severity": "blocking",
                    "message": full_message,
                    "context": {},
                }
            ]
        },
    )


def _calculate_path(
    prices: tuple[float, ...],
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    running_peak = prices[0]
    running_peak_index = 0
    drawdowns: list[float] = []
    peak_indices: list[int] = []
    for index, price in enumerate(prices):
        if price >= running_peak:
            running_peak = price
            running_peak_index = index
            drawdown = 0.0
        else:
            ratio = price / running_peak
            if ratio == 0.0:
                raise _blocking_error(
                    "A positive price-to-peak ratio rounded to zero."
                )
            drawdown = ratio - 1.0
            if not math.isfinite(drawdown):
                raise _blocking_error("A price-to-peak ratio was unrepresentable.")
        drawdowns.append(drawdown)
        peak_indices.append(running_peak_index)
    return tuple(drawdowns), tuple(peak_indices)


def _visualization(
    *,
    inputs: Inputs,
    drawdowns: tuple[float, ...],
    assumptions: tuple[str, ...],
    warnings: tuple[str, ...],
) -> VisualizationSpec:
    categories = (
        tuple(str(index) for index in range(len(drawdowns)))
        if inputs.timestamps is None
        else tuple(timestamp.isoformat() for timestamp in inputs.timestamps)
    )
    return VisualizationSpec(
        id="underwater_drawdown",
        kind=ChartKind.LINE,
        title="Drawdown / underwater path",
        alt_text=(
            f"Underwater line chart of {len(drawdowns)} peak-relative drawdowns. "
            f"The deepest observation is {min(drawdowns):.6g} in decimal units."
        ),
        categories=categories,
        series=(
            ChartSeries(key="drawdown", label="Drawdown", values=drawdowns),
        ),
        x_axis=AxisSpec(
            label="Observation" if inputs.timestamps is None else "Observation time"
        ),
        y_axis=AxisSpec(
            label="Drawdown",
            unit=Unit.DECIMAL,
            number_format=NumberFormat.PERCENT,
        ),
        caption=(
            f"{FORMULA}. Zero marks a running high; negative values show depth below it."
        ),
        assumptions=assumptions,
        warnings=warnings,
    )


def drawdown(
    prices: tuple[float, ...] | list[float],
    *,
    price_kind: PriceKind | str,
    timestamps: tuple[datetime | str, ...] | list[datetime | str] | None = None,
    declared_frequency: Frequency | str | None = None,
) -> Output:
    """Calculate the full underwater path and maximum drawdown episode."""

    inputs = Inputs.model_validate(
        {
            "prices": prices,
            "price_kind": price_kind,
            "timestamps": timestamps,
            "declared_frequency": declared_frequency,
        }
    )
    violations = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))
    drawdowns, running_peak_indices = _calculate_path(inputs.prices)
    trough_index = drawdowns.index(min(drawdowns))
    peak_index = running_peak_indices[trough_index]
    peak_value = inputs.prices[peak_index]
    if trough_index == peak_index:
        recovery_index: int | None = peak_index
    else:
        recovery_index = next(
            (
                index
                for index in range(trough_index + 1, len(inputs.prices))
                if inputs.prices[index] >= peak_value
            ),
            None,
        )

    point_derivations = tuple(
        Derivation(
            output=OutputRef(field="drawdowns", index=index),
            inputs=(
                (InputRef(field="prices", index=index),)
                if running_peak_indices[index] == index
                else (
                    InputRef(field="prices", index=index),
                    InputRef(field="prices", index=running_peak_indices[index]),
                )
            ),
            expression=(
                f"prices[{index}] is running peak; drawdown = 0"
                if running_peak_indices[index] == index
                else f"prices[{index}] / prices[{running_peak_indices[index]}] - 1"
            ),
            value=value,
        )
        for index, value in enumerate(drawdowns)
    )
    maximum_drawdown = drawdowns[trough_index]
    maximum_derivation = Derivation(
        output=OutputRef(field="maximum_drawdown", index=0),
        inputs=(
            (InputRef(field="prices", index=peak_index),)
            if peak_index == trough_index
            else (
                InputRef(field="prices", index=trough_index),
                InputRef(field="prices", index=peak_index),
            )
        ),
        expression=(
            "zero drawdown at initial selected peak"
            if peak_index == trough_index
            else f"prices[{trough_index}] / prices[{peak_index}] - 1"
        ),
        value=maximum_drawdown,
    )
    warnings = tuple(violation.message for violation in violations)
    assumptions = (
        "All observations refer to one instrument in a consistent price unit.",
        f"Price semantics are caller-declared as {inputs.price_kind.value}.",
    )
    transformations = (
        f"Computed the underwater path and selected episode as {FORMULA}.",
        "Updated running peaks on equal highs, selected the earliest deepest trough, and "
        "treated a zero-drawdown selected observation as immediately recovered.",
    )
    timestamp = inputs.timestamps
    visualization = _visualization(
        inputs=inputs,
        drawdowns=drawdowns,
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
        derivations=point_derivations + (maximum_derivation,),
        visualizations=(visualization,),
        drawdowns=drawdowns,
        maximum_drawdown=maximum_drawdown,
        price_kind=inputs.price_kind,
        running_peak_indices=running_peak_indices,
        peak_index=peak_index,
        trough_index=trough_index,
        recovery_index=recovery_index,
        recovered=recovery_index is not None,
        drawdown_timestamps=timestamp,
        peak_timestamp=None if timestamp is None else timestamp[peak_index],
        trough_timestamp=None if timestamp is None else timestamp[trough_index],
        recovery_timestamp=(
            None
            if timestamp is None or recovery_index is None
            else timestamp[recovery_index]
        ),
        declared_frequency=inputs.declared_frequency,
        ordering_status="verified" if timestamp is not None else "unverified",
    )


__all__ = [
    "COMPONENT_ID",
    "COMPONENT_VERSION",
    "FORMULA",
    "Inputs",
    "Output",
    "drawdown",
]
