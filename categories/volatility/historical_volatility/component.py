"""Canonical models and implementation for ``dq.volatility.historical_volatility``."""

from __future__ import annotations

import math
from typing import Literal

from defined_quant import preflight, subject_hash
from defined_quant.types import (
    ComponentOutput,
    Derivation,
    DomainError,
    InputRef,
    OutputRef,
    ReturnKind,
    Unit,
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
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, model_validator

COMPONENT_ID = "dq.volatility.historical_volatility"
COMPONENT_VERSION = "0.1.1"
FORMULA = (
    "a = r₀; cᵢ = rᵢ − a; dᵢ = cᵢ if every cᵢ is finite, otherwise dᵢ = rᵢ; "
    "s = maxᵢ |dᵢ|; if s = 0, σ̂ = 0; otherwise μ = fsum(dᵢ / s) / n, h₀ = 0, "
    "hᵢ₊₁ = hypot(hᵢ, dᵢ / s − μ), and σ̂ = s × (hₙ / sqrt(n − 1)); "
    "σ̂annual = σ̂ × sqrt(A)"
)

_DISCLOSURES = (
    "annualization_factor_explicit: The annualization factor was supplied explicitly; "
    "no default was inferred.",
    "calendar_frequency_not_inferred: No timestamps, observation frequency, market calendar, "
    "or gap policy was inferred.",
)
_CENTERED_PERIODIC_EXPRESSION = (
    "centered_scaled_hypot_sample_stdev_n_minus_1(returns)"
)
_RAW_FALLBACK_PERIODIC_EXPRESSION = (
    "raw_fallback_scaled_hypot_sample_stdev_n_minus_1(returns)"
)
_ZERO_PERIODIC_EXPRESSION = "zero_dispersion_sample_stdev_n_minus_1(returns)"
_PERIODIC_EXPRESSIONS = frozenset(
    {
        _CENTERED_PERIODIC_EXPRESSION,
        _RAW_FALLBACK_PERIODIC_EXPRESSION,
        _ZERO_PERIODIC_EXPRESSION,
    }
)


def _annualized_expression(periodic_expression: str) -> str:
    return f"{periodic_expression} * sqrt(annualization_factor)"


class Inputs(BaseModel):
    """Log periodic returns and an explicit factor for square-root annualization."""

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
    annualization_factor: StrictFloat = Field(
        ...,
        description="Explicit number of observation periods per year.",
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
        description="Return convention; this component accepts log returns only.",
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


class Output(ComponentOutput):
    """Periodic and explicitly annualized historical volatility."""

    periodic_volatility: float = Field(
        ...,
        ge=0.0,
        allow_inf_nan=False,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.PERIODIC_VOLATILITY,
            unit=PortUnit.VOLATILITY,
            shape=PortShape.SCALAR,
            cardinality=PortCardinality.EXACTLY_ONE,
            convention=PortConvention.SAMPLE_STANDARD_DEVIATION_N_MINUS_1,
            ordering=PortOrdering.NOT_APPLICABLE,
            frequency=PortFrequency.INHERITED,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    annualized_volatility: float = Field(
        ...,
        ge=0.0,
        allow_inf_nan=False,
        json_schema_extra=semantic_port_metadata(
            direction=PortDirection.OUTPUT,
            concept=PortConcept.ANNUALIZED_VOLATILITY,
            unit=PortUnit.VOLATILITY,
            shape=PortShape.SCALAR,
            cardinality=PortCardinality.EXACTLY_ONE,
            convention=(
                PortConvention.SAMPLE_STANDARD_DEVIATION_N_MINUS_1_SQUARE_ROOT_ANNUALIZATION
            ),
            ordering=PortOrdering.NOT_APPLICABLE,
            frequency=PortFrequency.ANNUAL,
            provenance_requirement=PortProvenanceRequirement.COMPONENT_BOUND,
        ),
    )
    annualization_factor: float = Field(gt=0.0, allow_inf_nan=False)
    sample_size: int = Field(ge=2)
    degrees_of_freedom_adjustment: Literal[1] = 1
    return_kind: Literal[ReturnKind.LOG] = ReturnKind.LOG
    derivations: tuple[Derivation, ...] = Field(..., min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_complete_volatility_lineage(self) -> Output:
        periodic, annualized = self.derivations
        expected_return_inputs = tuple(
            InputRef(field="returns", index=index)
            for index in range(self.sample_size)
        )
        if periodic.output != OutputRef(field="periodic_volatility", index=0):
            raise ValueError("periodic volatility derivation must target scalar index zero")
        if periodic.inputs != expected_return_inputs:
            raise ValueError("periodic volatility derivation must reference every return")
        if periodic.expression not in _PERIODIC_EXPRESSIONS:
            raise ValueError("periodic volatility derivation expression is not canonical")
        used_zero_branch = periodic.expression == _ZERO_PERIODIC_EXPRESSION
        if used_zero_branch != (self.periodic_volatility == 0.0):
            raise ValueError(
                "zero-dispersion derivation branch must match periodic volatility"
            )

        if annualized.output != OutputRef(field="annualized_volatility", index=0):
            raise ValueError("annualized volatility derivation must target scalar index zero")
        expected_annualized_inputs = expected_return_inputs + (
            InputRef(field="annualization_factor", index=0),
        )
        if annualized.inputs != expected_annualized_inputs:
            raise ValueError(
                "annualized volatility derivation must reference every return and the factor"
            )
        if annualized.expression != _annualized_expression(periodic.expression):
            raise ValueError("annualized volatility derivation expression is not canonical")
        expected_annualized = self.periodic_volatility * math.sqrt(
            self.annualization_factor
        )
        if (
            not math.isfinite(expected_annualized)
            or self.annualized_volatility != expected_annualized
        ):
            raise ValueError(
                "annualized volatility must equal periodic volatility times sqrt of the factor"
            )
        return self


def _non_finite_result(stage: Literal["periodic", "annualized"]) -> DomainError:
    message = (
        "non_finite_result: Finite inputs produced a volatility that cannot be represented "
        "without overflow or meaningful non-zero dispersion rounding to zero."
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
                    "context": {"stage": stage},
                }
            ]
        },
    )


def _sample_standard_deviation(returns: tuple[float, ...]) -> tuple[float, str]:
    anchor = returns[0]
    centered = tuple(value - anchor for value in returns)
    if all(math.isfinite(value) for value in centered):
        working_values = centered
        expression = _CENTERED_PERIODIC_EXPRESSION
    else:
        # Opposite-sign finite extremes can overflow during centering. Scaling the
        # original values first keeps that case representable without sacrificing
        # the centered path's precision for a large common offset.
        working_values = returns
        expression = _RAW_FALLBACK_PERIODIC_EXPRESSION

    scale = max(abs(value) for value in working_values)
    if scale == 0.0:
        return 0.0, _ZERO_PERIODIC_EXPRESSION

    scaled = tuple(value / scale for value in working_values)
    mean = math.fsum(scaled) / len(scaled)
    root_sum_squared = 0.0
    for value in scaled:
        root_sum_squared = math.hypot(root_sum_squared, value - mean)
    normalized = root_sum_squared / math.sqrt(len(scaled) - 1)
    result = scale * normalized
    if not math.isfinite(result) or (normalized > 0.0 and result == 0.0):
        raise _non_finite_result("periodic")
    return result, expression


def historical_volatility(
    returns: tuple[float, ...] | list[float],
    *,
    annualization_factor: float,
    return_kind: ReturnKind | str,
) -> Output:
    """Estimate sample volatility and annualize it with an explicit factor."""

    inputs = Inputs.model_validate(
        {
            "returns": returns,
            "annualization_factor": annualization_factor,
            "return_kind": return_kind,
        }
    )
    violations = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))

    periodic, periodic_expression = _sample_standard_deviation(inputs.returns)
    annualized = periodic * math.sqrt(inputs.annualization_factor)
    if not math.isfinite(annualized) or (periodic > 0.0 and annualized == 0.0):
        raise _non_finite_result("annualized")

    assumptions = (
        "Supplied observations are comparable decimal log returns for one periodic series.",
        "The sample standard deviation uses an n − 1 denominator.",
        "Square-root-of-time scaling is accepted for the requested annualization factor.",
    )
    warnings = tuple(violation.message for violation in violations)
    transformations = (
        f"Computed periodic and annualized volatility as {FORMULA}.",
        (
            "Used a scale-normalized root-sum-of-squares calculation to avoid avoidable "
            "intermediate overflow and underflow."
        ),
    )
    return_inputs = tuple(
        InputRef(field="returns", index=index)
        for index in range(len(inputs.returns))
    )
    derivations = (
        Derivation(
            output=OutputRef(field="periodic_volatility", index=0),
            inputs=return_inputs,
            expression=periodic_expression,
            value=periodic,
        ),
        Derivation(
            output=OutputRef(field="annualized_volatility", index=0),
            inputs=return_inputs + (InputRef(field="annualization_factor", index=0),),
            expression=_annualized_expression(periodic_expression),
            value=annualized,
        ),
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
        derivations=derivations,
        visualizations=(),
        periodic_volatility=periodic,
        annualized_volatility=annualized,
        annualization_factor=inputs.annualization_factor,
        sample_size=len(inputs.returns),
    )


__all__ = [
    "COMPONENT_ID",
    "COMPONENT_VERSION",
    "FORMULA",
    "Inputs",
    "Output",
    "historical_volatility",
]
