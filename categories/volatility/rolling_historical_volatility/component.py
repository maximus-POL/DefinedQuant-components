"""Canonical implementation for ``dq.volatility.rolling_historical_volatility``."""

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
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)

COMPONENT_ID = "dq.volatility.rolling_historical_volatility"
COMPONENT_VERSION = "0.1.0"
FORMULA = (
    "σ̂ₜ(w) = stdevₙ₋₁(rₜ₋w₊₁, …, rₜ); "
    "σ̂annual,ₜ(w) = σ̂ₜ(w) × sqrt(A)"
)
_DISCLOSURES = (
    "window_explicit: The rolling window length was supplied explicitly; no default was inferred.",
    "annualization_factor_explicit: The periods-per-year factor was supplied explicitly.",
    "calendar_gap_check_not_assessed: No market-calendar gap policy was applied.",
)


class Inputs(BaseModel):
    """Ordered log returns and explicit rolling and annualization conventions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    returns: tuple[StrictFloat, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.INPUT,
            concept=PortConcept.PERIODIC_RETURN_SERIES,
            unit=PortUnit.DECIMAL,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=PortConvention.LOG_PERIODIC_RETURN,
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.NOT_REQUIRED,
        ),
    )
    window_length: StrictInt
    annualization_factor: StrictFloat = Field(
        ...,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.INPUT,
            concept=PortConcept.ANNUALIZATION_FACTOR,
            unit=PortUnit.UNITLESS,
            shape=PortShape.SCALAR,
            cardinality=PortCardinality.EXACTLY_ONE,
            convention=PortConvention.EXPLICIT_PERIODS_PER_YEAR_FACTOR,
            ordering=PortOrdering.NOT_APPLICABLE,
            frequency=PortFrequency.ANNUAL,
            provenance_requirement=PortProvenanceRequirement.NOT_REQUIRED,
        ),
    )
    return_kind: ReturnKind = Field(
        ...,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.INPUT,
            concept=PortConcept.RETURN_CONVENTION,
            unit=PortUnit.UNITLESS,
            shape=PortShape.SCALAR,
            cardinality=PortCardinality.EXACTLY_ONE,
            convention=PortConvention.LOG_PERIODIC_RETURN,
            ordering=PortOrdering.NOT_APPLICABLE,
            frequency=PortFrequency.NOT_APPLICABLE,
            provenance_requirement=PortProvenanceRequirement.NOT_REQUIRED,
        ),
    )
    timestamps: tuple[AwareDatetime, ...] | None = None
    declared_frequency: Frequency | None = None


class Output(ComponentOutput):
    """Rolling sample volatility aligned to each complete window end."""

    periodic_volatility: tuple[float, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.PERIODIC_VOLATILITY,
            unit=PortUnit.VOLATILITY,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=PortConvention.ROLLING_SAMPLE_STANDARD_DEVIATION_N_MINUS_1,
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    annualized_volatility: tuple[float, ...] = Field(
        ...,
        min_length=1,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.ANNUALIZED_VOLATILITY,
            unit=PortUnit.VOLATILITY,
            shape=PortShape.ORDERED_SERIES,
            cardinality=PortCardinality.ONE_OR_MORE,
            convention=(
                PortConvention.ROLLING_SAMPLE_STANDARD_DEVIATION_N_MINUS_1_SQUARE_ROOT_ANNUALIZATION
            ),
            ordering=PortOrdering.PRESERVE_SOURCE_ORDER,
            frequency=PortFrequency.ANNUAL,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    window_length: int = Field(ge=2)
    annualization_factor: float = Field(gt=0.0, allow_inf_nan=False)
    return_kind: Literal[ReturnKind.LOG] = ReturnKind.LOG
    volatility_timestamps: tuple[AwareDatetime, ...] | None
    declared_frequency: Frequency | None
    ordering_status: Literal["verified", "unverified"]
    gap_check: Literal["not_assessed"] = "not_assessed"
    derivations: tuple[Derivation, ...] = Field(..., min_length=2)

    @model_validator(mode="after")
    def validate_complete_rolling_lineage(self) -> Output:
        count = len(self.periodic_volatility)
        if len(self.annualized_volatility) != count:
            raise ValueError("periodic and annualized series must align")
        if len(self.derivations) != count * 2:
            raise ValueError("every rolling window requires two derivations")
        for output_index in range(count):
            start = output_index
            stop = output_index + self.window_length
            return_inputs = tuple(
                InputRef(field="returns", index=index)
                for index in range(start, stop)
            )
            periodic = self.derivations[output_index * 2]
            annualized = self.derivations[output_index * 2 + 1]
            periodic_expression = f"sample_stdev_n_minus_1(returns[{start}:{stop}])"
            if periodic.output != OutputRef(
                field="periodic_volatility", index=output_index
            ):
                raise ValueError("periodic volatility derivations must be ordered")
            if periodic.inputs != return_inputs or periodic.expression != periodic_expression:
                raise ValueError("periodic volatility lineage must match its window")
            annual_expression = periodic_expression + " * sqrt(annualization_factor)"
            if annualized.output != OutputRef(
                field="annualized_volatility", index=output_index
            ):
                raise ValueError("annualized volatility derivations must be ordered")
            if annualized.inputs != return_inputs + (
                InputRef(field="annualization_factor", index=0),
            ):
                raise ValueError("annualized lineage must reference its window and factor")
            if annualized.expression != annual_expression:
                raise ValueError("annualized volatility expression is not canonical")
            expected = self.periodic_volatility[output_index] * math.sqrt(
                self.annualization_factor
            )
            if expected != self.annualized_volatility[output_index]:
                raise ValueError("annualized values must use square-root scaling")
        return self


def _non_finite_result(stage: str, window_index: int) -> DomainError:
    message = (
        "non_finite_result: Finite inputs produced an unrepresentable rolling "
        "volatility value."
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
                    "context": {"stage": stage, "window_index": window_index},
                }
            ]
        },
    )


def _sample_standard_deviation(values: tuple[float, ...], window_index: int) -> float:
    anchor = values[0]
    centered = tuple(value - anchor for value in values)
    working_values = centered if all(math.isfinite(value) for value in centered) else values
    scale = max(abs(value) for value in working_values)
    if scale == 0.0:
        return 0.0
    scaled = tuple(value / scale for value in working_values)
    mean = math.fsum(scaled) / len(scaled)
    root_sum_squared = 0.0
    for value in scaled:
        root_sum_squared = math.hypot(root_sum_squared, value - mean)
    normalized = root_sum_squared / math.sqrt(len(scaled) - 1)
    result = scale * normalized
    if not math.isfinite(result) or (normalized > 0.0 and result == 0.0):
        raise _non_finite_result("periodic", window_index)
    return result


def _visualization(
    *,
    inputs: Inputs,
    annualized: tuple[float, ...],
    assumptions: tuple[str, ...],
    warnings: tuple[str, ...],
) -> VisualizationSpec:
    start = inputs.window_length - 1
    categories = (
        tuple(str(index) for index in range(start, len(inputs.returns)))
        if inputs.timestamps is None
        else tuple(timestamp.isoformat() for timestamp in inputs.timestamps[start:])
    )
    return VisualizationSpec(
        id="rolling_annualized_volatility",
        kind=ChartKind.LINE,
        title=f"Rolling {inputs.window_length}-period annualized volatility",
        alt_text=(
            f"Line chart of {len(annualized)} rolling annualized volatility values "
            f"ranging from {min(annualized):.6g} to {max(annualized):.6g}."
        ),
        categories=categories,
        series=(
            ChartSeries(
                key="annualized_volatility",
                label="Annualized volatility",
                values=annualized,
            ),
        ),
        x_axis=AxisSpec(
            label="Window end" if inputs.timestamps is None else "Window end time"
        ),
        y_axis=AxisSpec(
            label="Annualized volatility",
            unit=Unit.VOLATILITY,
            number_format=NumberFormat.PERCENT,
        ),
        caption=(
            f"{FORMULA}; w = {inputs.window_length}, A = "
            f"{inputs.annualization_factor:.6g}."
        ),
        assumptions=assumptions,
        warnings=warnings,
    )


def rolling_historical_volatility(
    returns: tuple[float, ...] | list[float],
    *,
    window_length: int,
    annualization_factor: float,
    return_kind: ReturnKind | str,
    timestamps: tuple[datetime | str, ...] | list[datetime | str] | None = None,
    declared_frequency: Frequency | str | None = None,
) -> Output:
    """Calculate sample volatility over every complete rolling window."""

    inputs = Inputs.model_validate(
        {
            "returns": returns,
            "window_length": window_length,
            "annualization_factor": annualization_factor,
            "return_kind": return_kind,
            "timestamps": timestamps,
            "declared_frequency": declared_frequency,
        }
    )
    violations = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))
    periodic_list: list[float] = []
    for start in range(len(inputs.returns) - inputs.window_length + 1):
        window = inputs.returns[start : start + inputs.window_length]
        periodic_list.append(_sample_standard_deviation(window, start))
    periodic = tuple(periodic_list)
    factor_root = math.sqrt(inputs.annualization_factor)
    annualized = tuple(value * factor_root for value in periodic)
    for index, (periodic_value, annualized_value) in enumerate(
        zip(periodic, annualized, strict=True)
    ):
        if not math.isfinite(annualized_value) or (
            periodic_value > 0.0 and annualized_value == 0.0
        ):
            raise _non_finite_result("annualized", index)

    derivations: list[Derivation] = []
    for output_index, (periodic_value, annualized_value) in enumerate(
        zip(periodic, annualized, strict=True)
    ):
        stop = output_index + inputs.window_length
        return_inputs = tuple(
            InputRef(field="returns", index=index)
            for index in range(output_index, stop)
        )
        expression = f"sample_stdev_n_minus_1(returns[{output_index}:{stop}])"
        derivations.extend(
            (
                Derivation(
                    output=OutputRef(
                        field="periodic_volatility", index=output_index
                    ),
                    inputs=return_inputs,
                    expression=expression,
                    value=periodic_value,
                ),
                Derivation(
                    output=OutputRef(
                        field="annualized_volatility", index=output_index
                    ),
                    inputs=return_inputs
                    + (InputRef(field="annualization_factor", index=0),),
                    expression=expression + " * sqrt(annualization_factor)",
                    value=annualized_value,
                ),
            )
        )
    warnings = tuple(violation.message for violation in violations)
    assumptions = (
        "Supplied observations are comparable decimal log returns from one periodic series.",
        "Each window uses sample standard deviation with an n − 1 denominator.",
        "Square-root-of-time scaling is accepted for the explicit annualization factor.",
    )
    transformations = (
        f"Computed every complete rolling window as {FORMULA}.",
        "Aligned each result to the final return observation in its window.",
    )
    visualization = _visualization(
        inputs=inputs,
        annualized=annualized,
        assumptions=assumptions,
        warnings=warnings,
    )
    return Output(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        subject_hash=subject_hash(COMPONENT_ID),
        unit=Unit.VOLATILITY,
        assumptions=assumptions,
        disclosures=_DISCLOSURES,
        warnings=warnings,
        transformations=transformations,
        derivations=tuple(derivations),
        visualizations=(visualization,),
        periodic_volatility=periodic,
        annualized_volatility=annualized,
        window_length=inputs.window_length,
        annualization_factor=inputs.annualization_factor,
        volatility_timestamps=(
            None
            if inputs.timestamps is None
            else inputs.timestamps[inputs.window_length - 1 :]
        ),
        declared_frequency=inputs.declared_frequency,
        ordering_status="verified" if inputs.timestamps is not None else "unverified",
    )


__all__ = [
    "COMPONENT_ID",
    "COMPONENT_VERSION",
    "FORMULA",
    "Inputs",
    "Output",
    "rolling_historical_volatility",
]
