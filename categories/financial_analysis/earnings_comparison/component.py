"""Canonical earnings comparison for ``dq.financial_analysis.earnings_comparison``."""

from __future__ import annotations

import math
import statistics
from enum import StrEnum
from typing import Annotated

from defined_quant import preflight, subject_hash
from defined_quant.charts import (
    DashboardSpec,
    RenderTarget,
    TableColumn,
    TableRow,
    TableSpec,
    ViewBundleSpec,
)
from defined_quant.types import (
    AxisSpec,
    ChartKind,
    ChartSeries,
    ComponentOutput,
    DomainError,
    NumberFormat,
    Unit,
    VisualizationSpec,
)
from pydantic import BaseModel, ConfigDict, Field

COMPONENT_ID = "dq.financial_analysis.earnings_comparison"
COMPONENT_VERSION = "0.1.0"
PeriodLabel = Annotated[str, Field(min_length=1, max_length=40)]


class ValueScale(StrEnum):
    """Declared scale already applied to every supplied monetary value."""

    UNITS = "units"
    THOUSANDS = "thousands"
    MILLIONS = "millions"
    BILLIONS = "billions"


class FiscalPeriodBasis(StrEnum):
    """How the caller aligned periods across companies."""

    CALENDAR_ALIGNED = "calendar_aligned"
    COMPANY_REPORTED = "company_reported"


class EarningsSeries(BaseModel):
    """One company's ordered observations for a single monetary metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=80)
    values: tuple[float, ...]


class Inputs(BaseModel):
    """Aligned monetary earnings observations and their explicit conventions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(min_length=1, max_length=60)
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    value_scale: ValueScale
    period_basis: FiscalPeriodBasis
    periods: tuple[PeriodLabel, ...]
    series: tuple[EarningsSeries, ...]
    source_label: str = Field(min_length=1, max_length=240)


class EarningsObservation(BaseModel):
    """One normalized long-form row suitable for table or data export."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: str
    company_key: str
    company_label: str
    value: float


class CompanyStatistics(BaseModel):
    """Descriptive statistics and endpoint comparison for one company."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    company_key: str
    company_label: str
    observation_count: int = Field(ge=2)
    first_value: float
    latest_value: float
    arithmetic_mean: float
    median: float
    sample_standard_deviation: float = Field(ge=0.0)
    minimum: float
    maximum: float
    absolute_change: float
    percentage_change: float | None
    latest_rank: int = Field(ge=1)
    latest_peer_share: float | None = Field(default=None, ge=0.0, le=1.0)


class PeriodStatistics(BaseModel):
    """Cross-sectional descriptive statistics for one aligned period."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: str
    company_count: int = Field(ge=2)
    total: float
    arithmetic_mean: float
    median: float
    sample_standard_deviation: float = Field(ge=0.0)
    minimum: float
    maximum: float


class Output(ComponentOutput):
    """Comparison tables, descriptive statistics, and chart-ready specifications."""

    metric_name: str
    reporting_currency: str
    value_scale: ValueScale
    period_basis: FiscalPeriodBasis
    periods: tuple[str, ...]
    source_label: str
    dashboard: DashboardSpec
    view_bundle: ViewBundleSpec
    observations: tuple[EarningsObservation, ...]
    company_statistics: tuple[CompanyStatistics, ...]
    period_statistics: tuple[PeriodStatistics, ...]
    latest_period: str
    latest_total: float
    latest_leader_keys: tuple[str, ...] = Field(min_length=1)


def _domain_error(rule: str, message: str) -> DomainError:
    return DomainError(
        message,
        component_id=COMPONENT_ID,
        details={
            "violations": [
                {
                    "rule": rule,
                    "severity": "blocking",
                    "message": message,
                    "context": {},
                }
            ]
        },
    )


def _finite(value: float, *, context: str) -> float:
    if not math.isfinite(value):
        raise _domain_error(
            "non_finite_result",
            f"non_finite_result: {context} is outside the finite binary64 range.",
        )
    return value


def _mean(values: tuple[float, ...]) -> float:
    count = len(values)
    try:
        return _finite(
            math.fsum(value / count for value in values),
            context="A descriptive mean",
        )
    except OverflowError as error:
        raise _domain_error(
            "non_finite_result",
            "non_finite_result: A descriptive mean is outside the finite binary64 range.",
        ) from error


def _median(values: tuple[float, ...]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return _finite(
        ordered[middle - 1] / 2.0 + ordered[middle] / 2.0,
        context="A descriptive median",
    )


def _sample_standard_deviation(values: tuple[float, ...]) -> float:
    try:
        return _finite(
            statistics.stdev(values),
            context="A sample standard deviation",
        )
    except (OverflowError, statistics.StatisticsError) as error:
        raise _domain_error(
            "non_finite_result",
            "non_finite_result: A sample standard deviation is outside the finite "
            "binary64 range.",
        ) from error


def _total(values: tuple[float, ...]) -> float:
    try:
        return _finite(math.fsum(values), context="A cross-company total")
    except OverflowError as error:
        raise _domain_error(
            "non_finite_result",
            "non_finite_result: A cross-company total is outside the finite binary64 range.",
        ) from error


def _validate_shape(inputs: Inputs) -> None:
    period_count = len(inputs.periods)
    if len(set(inputs.periods)) != period_count:
        raise _domain_error(
            "duplicate_periods",
            "duplicate_periods: Period labels must be unique and supplied in intended order.",
        )
    keys = [item.key for item in inputs.series]
    labels = [item.label for item in inputs.series]
    if len(set(keys)) != len(keys):
        raise _domain_error(
            "duplicate_company_keys",
            "duplicate_company_keys: Every company key must be unique.",
        )
    if len(set(labels)) != len(labels):
        raise _domain_error(
            "duplicate_company_labels",
            "duplicate_company_labels: Every company label must be unique.",
        )
    for item in inputs.series:
        if len(item.values) != period_count:
            raise _domain_error(
                "misaligned_company_series",
                "misaligned_company_series: Every company must have exactly one value for "
                "every supplied period.",
            )
        if any(not math.isfinite(value) for value in item.values):
            raise _domain_error(
                "non_finite_values",
                "non_finite_values: Every earnings observation must be finite.",
            )


def _period_statistics(inputs: Inputs) -> tuple[PeriodStatistics, ...]:
    rows: list[PeriodStatistics] = []
    for index, period in enumerate(inputs.periods):
        values = tuple(item.values[index] for item in inputs.series)
        rows.append(
            PeriodStatistics(
                period=period,
                company_count=len(values),
                total=_total(values),
                arithmetic_mean=_mean(values),
                median=_median(values),
                sample_standard_deviation=_sample_standard_deviation(values),
                minimum=min(values),
                maximum=max(values),
            )
        )
    return tuple(rows)


def _company_statistics(
    inputs: Inputs,
) -> tuple[tuple[CompanyStatistics, ...], tuple[str, ...]]:
    latest_values = tuple(item.values[-1] for item in inputs.series)
    latest_total = _total(latest_values)
    shares_available = all(value >= 0.0 for value in latest_values) and latest_total > 0.0
    rows: list[CompanyStatistics] = []
    invalid_growth_labels: list[str] = []

    for item in inputs.series:
        first = item.values[0]
        latest = item.values[-1]
        absolute_change = _finite(
            latest - first,
            context=f"{item.label}'s endpoint change",
        )
        percentage_change: float | None = None
        if first > 0.0:
            percentage_change = _finite(
                latest / first - 1.0,
                context=f"{item.label}'s percentage change",
            )
        else:
            invalid_growth_labels.append(item.label)
        rows.append(
            CompanyStatistics(
                company_key=item.key,
                company_label=item.label,
                observation_count=len(item.values),
                first_value=first,
                latest_value=latest,
                arithmetic_mean=_mean(item.values),
                median=_median(item.values),
                sample_standard_deviation=_sample_standard_deviation(item.values),
                minimum=min(item.values),
                maximum=max(item.values),
                absolute_change=absolute_change,
                percentage_change=percentage_change,
                latest_rank=1 + sum(value > latest for value in latest_values),
                latest_peer_share=latest / latest_total if shares_available else None,
            )
        )
    return tuple(rows), tuple(invalid_growth_labels)


def _value_axis(inputs: Inputs) -> AxisSpec:
    return AxisSpec(
        label=(
            f"{inputs.metric_name} ({inputs.reporting_currency} "
            f"{inputs.value_scale.value})"
        ),
        unit=Unit.CURRENCY,
        number_format=NumberFormat.DECIMAL,
    )


def _visualizations(
    *,
    inputs: Inputs,
    company_statistics: tuple[CompanyStatistics, ...],
    assumptions: tuple[str, ...],
    warnings: tuple[str, ...],
) -> tuple[VisualizationSpec, ...]:
    labels = tuple(item.label for item in inputs.series)
    value_axis = _value_axis(inputs)
    charts = [
        VisualizationSpec(
            id="earnings_trend",
            kind=ChartKind.LINE,
            title=f"{inputs.metric_name} trend",
            alt_text=(
                f"Line chart comparing {inputs.metric_name} across {len(inputs.series)} "
                f"companies and {len(inputs.periods)} ordered periods."
            ),
            categories=inputs.periods,
            series=tuple(
                ChartSeries(key=item.key, label=item.label, values=item.values)
                for item in inputs.series
            ),
            x_axis=AxisSpec(label="Period", unit=Unit.UNITLESS),
            y_axis=value_axis,
            caption=(
                f"Values are caller-supplied {inputs.reporting_currency} "
                f"{inputs.value_scale.value}; periods are not sorted or transformed."
            ),
            assumptions=assumptions,
            warnings=warnings,
            width=1000,
            height=520,
        ),
        VisualizationSpec(
            id="latest_earnings_comparison",
            kind=ChartKind.BAR,
            title=f"{inputs.metric_name} — {inputs.periods[-1]}",
            alt_text=(
                f"Bar chart comparing the latest supplied {inputs.metric_name} value for "
                f"{len(inputs.series)} companies."
            ),
            categories=labels,
            series=(
                ChartSeries(
                    key="latest_value",
                    label=inputs.periods[-1],
                    values=tuple(row.latest_value for row in company_statistics),
                ),
            ),
            x_axis=AxisSpec(label="Company", unit=Unit.UNITLESS),
            y_axis=value_axis,
            caption="Latest-period ranking uses descending values with competition ranks.",
            assumptions=assumptions,
            warnings=warnings,
            width=1000,
            height=520,
        ),
        VisualizationSpec(
            id="earnings_absolute_change",
            kind=ChartKind.BAR,
            title=f"{inputs.metric_name} change: {inputs.periods[0]} to {inputs.periods[-1]}",
            alt_text=(
                f"Bar chart of absolute {inputs.metric_name} change from the first to latest "
                f"period for {len(inputs.series)} companies."
            ),
            categories=labels,
            series=(
                ChartSeries(
                    key="absolute_change",
                    label="Absolute change",
                    values=tuple(row.absolute_change for row in company_statistics),
                ),
            ),
            x_axis=AxisSpec(label="Company", unit=Unit.UNITLESS),
            y_axis=value_axis,
            caption="Absolute change equals latest value minus first value.",
            assumptions=assumptions,
            warnings=warnings,
            width=1000,
            height=520,
        ),
    ]

    growth_rows = tuple(
        row for row in company_statistics if row.percentage_change is not None
    )
    if growth_rows:
        charts.append(
            VisualizationSpec(
                id="earnings_percentage_change",
                kind=ChartKind.BAR,
                title=(
                    f"{inputs.metric_name} growth: {inputs.periods[0]} to "
                    f"{inputs.periods[-1]}"
                ),
                alt_text=(
                    "Bar chart of endpoint percentage change for companies with a strictly "
                    "positive first-period value."
                ),
                categories=tuple(row.company_label for row in growth_rows),
                series=(
                    ChartSeries(
                        key="percentage_change",
                        label="Percentage change",
                        values=tuple(
                            row.percentage_change
                            for row in growth_rows
                            if row.percentage_change is not None
                        ),
                    ),
                ),
                x_axis=AxisSpec(label="Company", unit=Unit.UNITLESS),
                y_axis=AxisSpec(
                    label="Endpoint growth",
                    unit=Unit.PERCENT,
                    number_format=NumberFormat.PERCENT,
                ),
                caption=(
                    "Percentage change is (latest / first) − 1 and is omitted when the "
                    "first value is zero or negative."
                ),
                assumptions=assumptions,
                warnings=warnings,
                width=1000,
                height=520,
            )
        )

    if all(row.latest_peer_share is not None for row in company_statistics):
        charts.append(
            VisualizationSpec(
                id="latest_peer_set_share",
                kind=ChartKind.BAR,
                title=f"Share of supplied peer-set {inputs.metric_name}",
                alt_text=(
                    f"Bar chart showing each company's share of the positive {inputs.periods[-1]} "
                    "total across the supplied comparison set."
                ),
                categories=labels,
                series=(
                    ChartSeries(
                        key="peer_set_share",
                        label="Peer-set share",
                        values=tuple(
                            row.latest_peer_share
                            for row in company_statistics
                            if row.latest_peer_share is not None
                        ),
                    ),
                ),
                x_axis=AxisSpec(label="Company", unit=Unit.UNITLESS),
                y_axis=AxisSpec(
                    label="Share of supplied peer total",
                    unit=Unit.PERCENT,
                    number_format=NumberFormat.PERCENT,
                ),
                caption=(
                    "This is composition within the supplied companies, not market share."
                ),
                assumptions=assumptions,
                warnings=warnings,
                width=1000,
                height=520,
            )
        )
    return tuple(charts)


def _dashboard(
    *,
    inputs: Inputs,
    company_statistics: tuple[CompanyStatistics, ...],
    visualizations: tuple[VisualizationSpec, ...],
    warnings: tuple[str, ...],
) -> DashboardSpec:
    return DashboardSpec(
        id="earnings_comparison_dashboard",
        title=f"{inputs.metric_name} comparison",
        subtitle=(
            f"{inputs.periods[0]} to {inputs.periods[-1]} · "
            f"{inputs.reporting_currency} {inputs.value_scale.value} · "
            f"{inputs.period_basis.value}"
        ),
        alt_text=(
            f"Prescribed dashboard comparing {inputs.metric_name} for "
            f"{len(inputs.series)} companies across {len(inputs.periods)} periods, with "
            "trend, latest value, change, growth, peer composition, and statistics table."
        ),
        primary_chart_id="earnings_trend",
        secondary_chart_ids=tuple(
            visualization.id
            for visualization in visualizations
            if visualization.id != "earnings_trend"
        ),
        table=TableSpec(
            id="company_statistics",
            title="Company statistics",
            row_label="Company",
            columns=(
                TableColumn(
                    key="latest_value",
                    label=(
                        f"{inputs.periods[-1]} "
                        f"({inputs.reporting_currency} {inputs.value_scale.value})"
                    ),
                    cell_format="decimal",
                    decimals=1,
                ),
                TableColumn(
                    key="absolute_change",
                    label="Absolute change",
                    cell_format="decimal",
                    decimals=1,
                    show_sign=True,
                ),
                TableColumn(
                    key="percentage_change",
                    label="Endpoint growth",
                    cell_format="percent",
                    decimals=1,
                    show_sign=True,
                ),
                TableColumn(
                    key="arithmetic_mean",
                    label="Mean",
                    cell_format="decimal",
                    decimals=1,
                ),
                TableColumn(
                    key="sample_standard_deviation",
                    label="Sample σ",
                    cell_format="decimal",
                    decimals=1,
                ),
                TableColumn(
                    key="latest_peer_share",
                    label="Peer-set share",
                    cell_format="percent",
                    decimals=1,
                ),
                TableColumn(
                    key="latest_rank",
                    label="Rank",
                    cell_format="integer",
                    decimals=0,
                ),
            ),
            rows=tuple(
                TableRow(
                    key=row.company_key,
                    label=row.company_label,
                    values=(
                        row.latest_value,
                        row.absolute_change,
                        row.percentage_change,
                        row.arithmetic_mean,
                        row.sample_standard_deviation,
                        row.latest_peer_share,
                        row.latest_rank,
                    ),
                )
                for row in sorted(
                    company_statistics,
                    key=lambda item: (item.latest_rank, item.company_key),
                )
            ),
            caption=(
                "Peer-set share is composition within the supplied comparison set, not "
                "market share. Percentage growth is omitted for a non-positive starting value."
            ),
        ),
        notes=(
            f"Source: {inputs.source_label}.",
            *warnings,
        ),
    )


def _view_bundle() -> ViewBundleSpec:
    return ViewBundleSpec(
        default_chat_view_id="chat_dashboard",
        fallback_view_id="portable_dashboard",
        views=(
            RenderTarget(
                id="chat_dashboard",
                media_type="text/html",
                renderer="dashboard_html",
                use_cases=("chat",),
                interactive=True,
            ),
            RenderTarget(
                id="portable_dashboard",
                media_type="image/svg+xml",
                renderer="dashboard_svg",
                use_cases=("portable", "print"),
            ),
        ),
    )


def earnings_comparison(
    *,
    metric_name: str,
    reporting_currency: str,
    value_scale: ValueScale | str,
    period_basis: FiscalPeriodBasis | str,
    periods: tuple[str, ...] | list[str],
    series: (
        tuple[EarningsSeries | dict[str, object], ...]
        | list[EarningsSeries | dict[str, object]]
    ),
    source_label: str,
) -> Output:
    """Compare one aligned monetary earnings metric across two to twelve companies."""

    inputs = Inputs.model_validate(
        {
            "metric_name": metric_name,
            "reporting_currency": reporting_currency,
            "value_scale": value_scale,
            "period_basis": period_basis,
            "periods": periods,
            "series": series,
            "source_label": source_label,
        }
    )
    contract_warnings = preflight(COMPONENT_ID, **inputs.model_dump(mode="python"))
    _validate_shape(inputs)
    company_statistics, invalid_growth_labels = _company_statistics(inputs)
    period_statistics = _period_statistics(inputs)

    warnings = [violation.message for violation in contract_warnings]
    if invalid_growth_labels:
        warnings.append(
            "non_positive_growth_base: Percentage change is omitted for "
            + ", ".join(invalid_growth_labels)
            + " because the first value is zero or negative."
        )
    if not all(row.latest_peer_share is not None for row in company_statistics):
        warnings.append(
            "peer_share_unavailable: Peer-set shares are omitted unless all latest values "
            "are non-negative and their total is positive."
        )
    warning_tuple = tuple(warnings)
    assumptions = (
        "Every series uses one metric definition, reporting currency, and declared scale.",
        "Caller-supplied period order is intentional and observations are already aligned.",
        "Sample standard deviation uses denominator n-1.",
        "No inflation, foreign-exchange, accounting-policy, or fiscal-calendar adjustment is made.",
        f"Source provenance is caller-declared as: {inputs.source_label}.",
    )
    observations = tuple(
        EarningsObservation(
            period=period,
            company_key=item.key,
            company_label=item.label,
            value=item.values[period_index],
        )
        for period_index, period in enumerate(inputs.periods)
        for item in inputs.series
    )
    latest_values = tuple(item.values[-1] for item in inputs.series)
    leading_value = max(latest_values)
    latest_leader_keys = tuple(
        item.key
        for item, latest_value in zip(inputs.series, latest_values, strict=True)
        if latest_value == leading_value
    )
    visualizations = _visualizations(
        inputs=inputs,
        company_statistics=company_statistics,
        assumptions=assumptions,
        warnings=warning_tuple,
    )
    dashboard = _dashboard(
        inputs=inputs,
        company_statistics=company_statistics,
        visualizations=visualizations,
        warnings=warning_tuple,
    )

    return Output(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        subject_hash=subject_hash(COMPONENT_ID),
        unit=Unit.CURRENCY,
        assumptions=assumptions,
        warnings=warning_tuple,
        transformations=(
            "Validated a complete rectangular company-by-period matrix without sorting.",
            "Normalized the matrix to period-company-value rows.",
            "Computed per-company endpoint, mean, median, n-1 standard deviation, range, "
            "growth, rank, and eligible peer-set share statistics.",
            "Computed cross-sectional totals and descriptive statistics for every period.",
            "Produced renderer-neutral trend, latest-value, change, growth, and eligible "
            "peer-composition chart specifications.",
        ),
        visualizations=visualizations,
        metric_name=inputs.metric_name,
        reporting_currency=inputs.reporting_currency,
        value_scale=inputs.value_scale,
        period_basis=inputs.period_basis,
        periods=inputs.periods,
        source_label=inputs.source_label,
        dashboard=dashboard,
        view_bundle=_view_bundle(),
        observations=observations,
        company_statistics=company_statistics,
        period_statistics=period_statistics,
        latest_period=inputs.periods[-1],
        latest_total=period_statistics[-1].total,
        latest_leader_keys=latest_leader_keys,
    )


__all__ = [
    "CompanyStatistics",
    "EarningsObservation",
    "EarningsSeries",
    "FiscalPeriodBasis",
    "Inputs",
    "Output",
    "PeriodStatistics",
    "ValueScale",
    "earnings_comparison",
]
