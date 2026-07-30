"""Evidence and behavior tests for the earnings comparison component."""

from __future__ import annotations

import math

import pytest
from defined_quant import preflight, subject_hash
from defined_quant.charts import (
    dashboard_hash,
    render_dashboard_html,
    render_dashboard_svg,
    render_svg,
    select_view,
    view_hash,
)
from defined_quant.financial_analysis.earnings_comparison.component import (
    COMPONENT_ID,
    CompanyStatistics,
    EarningsSeries,
    FiscalPeriodBasis,
    Inputs,
    Output,
    ValueScale,
    earnings_comparison,
)
from defined_quant.types import AmbiguousInput, DomainError, Unit
from pydantic import ValidationError

SYNTHETIC_HYPERSCALER_SAMPLE: dict[str, object] = {
    "metric_name": "Revenue",
    "reporting_currency": "USD",
    "value_scale": "millions",
    "period_basis": "calendar_aligned",
    "periods": ("2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"),
    "series": (
        {
            "key": "microsoft",
            "label": "Microsoft",
            "values": (61_800.0, 64_700.0, 65_600.0, 69_600.0),
        },
        {
            "key": "amazon",
            "label": "Amazon",
            "values": (143_300.0, 148_000.0, 158_900.0, 170_000.0),
        },
        {
            "key": "meta",
            "label": "Meta",
            "values": (36_500.0, 39_000.0, 40_500.0, 44_000.0),
        },
        {
            "key": "alphabet",
            "label": "Alphabet",
            "values": (80_500.0, 84_500.0, 88_000.0, 93_000.0),
        },
        {
            "key": "other_hyperscaler",
            "label": "Other hyperscaler",
            "values": (13_300.0, 13_800.0, 14_200.0, 14_600.0),
        },
    ),
    "source_label": "Synthetic illustrative fixture; not company filings",
}


def _rules(error: DomainError) -> set[str]:
    violations = error.details.get("violations", [])
    assert isinstance(violations, list)
    return {
        str(item["rule"])
        for item in violations
        if isinstance(item, dict) and "rule" in item
    }


def _small_result() -> Output:
    return earnings_comparison(
        metric_name="Net income",
        reporting_currency="USD",
        value_scale="millions",
        period_basis="calendar_aligned",
        periods=("P1", "P2", "P3"),
        series=(
            {"key": "alpha", "label": "Alpha", "values": (10.0, 20.0, 30.0)},
            {"key": "beta", "label": "Beta", "values": (5.0, 10.0, 10.0)},
        ),
        source_label="Synthetic hand-checkable fixture",
    )


def _company(result: Output, key: str) -> CompanyStatistics:
    return next(row for row in result.company_statistics if row.company_key == key)


def test_contract_exports_are_present() -> None:
    assert Inputs is not None
    assert Output is not None
    assert EarningsSeries is not None
    assert callable(earnings_comparison)


def test_evidence_ka_001() -> None:
    result = _small_result()
    alpha = _company(result, "alpha")
    beta = _company(result, "beta")

    assert alpha.arithmetic_mean == pytest.approx(20.0)
    assert alpha.median == pytest.approx(20.0)
    assert alpha.sample_standard_deviation == pytest.approx(10.0)
    assert alpha.absolute_change == pytest.approx(20.0)
    assert alpha.percentage_change == pytest.approx(2.0)
    assert alpha.latest_rank == 1
    assert alpha.latest_peer_share == pytest.approx(0.75)

    assert beta.arithmetic_mean == pytest.approx(25.0 / 3.0)
    assert beta.median == pytest.approx(10.0)
    assert beta.sample_standard_deviation == pytest.approx(math.sqrt(25.0 / 3.0))
    assert beta.absolute_change == pytest.approx(5.0)
    assert beta.percentage_change == pytest.approx(1.0)
    assert beta.latest_rank == 2
    assert beta.latest_peer_share == pytest.approx(0.25)

    assert result.latest_period == "P3"
    assert result.latest_total == pytest.approx(40.0)
    assert result.latest_leader_keys == ("alpha",)
    assert result.period_statistics[0].total == pytest.approx(15.0)
    assert result.period_statistics[0].arithmetic_mean == pytest.approx(7.5)
    assert result.period_statistics[0].median == pytest.approx(7.5)
    assert result.period_statistics[0].sample_standard_deviation == pytest.approx(
        math.sqrt(12.5)
    )


def test_evidence_inv_001() -> None:
    original = _small_result()
    scaled = earnings_comparison(
        metric_name="Net income",
        reporting_currency="USD",
        value_scale="thousands",
        period_basis="calendar_aligned",
        periods=("P1", "P2", "P3"),
        series=(
            {"key": "alpha", "label": "Alpha", "values": (100.0, 200.0, 300.0)},
            {"key": "beta", "label": "Beta", "values": (50.0, 100.0, 100.0)},
        ),
        source_label="Synthetic scaled fixture",
    )

    for key in ("alpha", "beta"):
        first = _company(original, key)
        second = _company(scaled, key)
        assert second.latest_value == pytest.approx(10.0 * first.latest_value)
        assert second.arithmetic_mean == pytest.approx(10.0 * first.arithmetic_mean)
        assert second.sample_standard_deviation == pytest.approx(
            10.0 * first.sample_standard_deviation
        )
        assert second.absolute_change == pytest.approx(10.0 * first.absolute_change)
        assert second.percentage_change == pytest.approx(first.percentage_change)
        assert second.latest_peer_share == pytest.approx(first.latest_peer_share)
        assert second.latest_rank == first.latest_rank


def test_evidence_inv_002() -> None:
    original = _small_result()
    permuted = earnings_comparison(
        metric_name="Net income",
        reporting_currency="USD",
        value_scale="millions",
        period_basis="calendar_aligned",
        periods=("P1", "P2", "P3"),
        series=(
            {"key": "beta", "label": "Beta", "values": (5.0, 10.0, 10.0)},
            {"key": "alpha", "label": "Alpha", "values": (10.0, 20.0, 30.0)},
        ),
        source_label="Synthetic permuted fixture",
    )

    for key in ("alpha", "beta"):
        assert _company(permuted, key) == _company(original, key)
    assert permuted.period_statistics == original.period_statistics
    assert permuted.latest_leader_keys == original.latest_leader_keys


@pytest.mark.parametrize(
    ("periods", "series", "rule"),
    [
        (
            ("P1",),
            (
                {"key": "alpha", "label": "Alpha", "values": (1.0,)},
                {"key": "beta", "label": "Beta", "values": (2.0,)},
            ),
            "insufficient_periods",
        ),
        (
            ("P1", "P2"),
            ({"key": "alpha", "label": "Alpha", "values": (1.0, 2.0)},),
            "insufficient_companies",
        ),
        (
            ("P1", "P2"),
            (
                {"key": "alpha", "label": "Alpha", "values": (1.0, 2.0)},
                {"key": "beta", "label": "Beta", "values": (2.0,)},
            ),
            "misaligned_company_series",
        ),
        (
            ("P1", "P1"),
            (
                {"key": "alpha", "label": "Alpha", "values": (1.0, 2.0)},
                {"key": "beta", "label": "Beta", "values": (2.0, 3.0)},
            ),
            "duplicate_periods",
        ),
        (
            ("P1", "P2"),
            (
                {"key": "alpha", "label": "Alpha", "values": (1.0, 2.0)},
                {"key": "alpha", "label": "Beta", "values": (2.0, 3.0)},
            ),
            "duplicate_company_keys",
        ),
        (
            ("P1", "P2"),
            (
                {"key": "alpha", "label": "Same", "values": (1.0, 2.0)},
                {"key": "beta", "label": "Same", "values": (2.0, 3.0)},
            ),
            "duplicate_company_labels",
        ),
        (
            ("P1", "P2"),
            (
                {"key": "alpha", "label": "Alpha", "values": (1.0, math.inf)},
                {"key": "beta", "label": "Beta", "values": (2.0, 3.0)},
            ),
            "non_finite_values",
        ),
    ],
)
def test_evidence_bc_001(
    periods: tuple[str, ...],
    series: tuple[dict[str, object], ...],
    rule: str,
) -> None:
    with pytest.raises(DomainError) as caught:
        earnings_comparison(
            metric_name="Revenue",
            reporting_currency="USD",
            value_scale="millions",
            period_basis="calendar_aligned",
            periods=periods,
            series=series,
            source_label="Synthetic boundary fixture",
        )

    assert rule in _rules(caught.value)


def test_evidence_bc_002() -> None:
    result = earnings_comparison(
        metric_name="Net income",
        reporting_currency="USD",
        value_scale="millions",
        period_basis="calendar_aligned",
        periods=("P1", "P2"),
        series=(
            {"key": "lossco", "label": "LossCo", "values": (-10.0, 5.0)},
            {"key": "flatco", "label": "FlatCo", "values": (0.0, 2.0)},
        ),
        source_label="Synthetic loss fixture",
    )

    assert all(row.percentage_change is None for row in result.company_statistics)
    assert all(row.latest_peer_share is not None for row in result.company_statistics)
    assert any("non_positive_growth_base" in warning for warning in result.warnings)
    assert {chart.id for chart in result.visualizations} == {
        "earnings_trend",
        "latest_earnings_comparison",
        "earnings_absolute_change",
        "latest_peer_set_share",
    }


def test_evidence_bc_003() -> None:
    result = earnings_comparison(
        metric_name="Net income",
        reporting_currency="USD",
        value_scale="millions",
        period_basis="calendar_aligned",
        periods=("P1", "P2"),
        series=(
            {"key": "alpha", "label": "Alpha", "values": (10.0, -2.0)},
            {"key": "beta", "label": "Beta", "values": (5.0, 3.0)},
        ),
        source_label="Synthetic negative-latest fixture",
    )

    assert all(row.latest_peer_share is None for row in result.company_statistics)
    assert any("peer_share_unavailable" in warning for warning in result.warnings)
    assert "latest_peer_set_share" not in {
        chart.id for chart in result.visualizations
    }


def test_evidence_bc_004() -> None:
    result = earnings_comparison(
        metric_name="Revenue",
        reporting_currency="USD",
        value_scale="millions",
        period_basis="company_reported",
        periods=("FY-Q1", "FY-Q2"),
        series=(
            {"key": "alpha", "label": "Alpha", "values": (10.0, 12.0)},
            {"key": "beta", "label": "Beta", "values": (8.0, 9.0)},
        ),
        source_label="Synthetic fiscal-period fixture",
    )

    assert any("company_reported_periods" in warning for warning in result.warnings)


def test_evidence_cc_001() -> None:
    result = _small_result()
    for index, period in enumerate(result.periods):
        independently_selected = tuple(
            row.value for row in result.observations if row.period == period
        )
        period_row = result.period_statistics[index]
        assert period_row.total == pytest.approx(math.fsum(independently_selected))
        assert period_row.arithmetic_mean == pytest.approx(
            math.fsum(independently_selected) / len(independently_selected)
        )


def test_synthetic_hyperscaler_fixture_and_charts() -> None:
    result = earnings_comparison(**SYNTHETIC_HYPERSCALER_SAMPLE)  # type: ignore[arg-type]

    assert result.latest_leader_keys == ("amazon",)
    assert len(result.observations) == 20
    assert len(result.company_statistics) == 5
    assert len(result.period_statistics) == 4
    assert len(result.visualizations) == 5
    assert result.dashboard.primary_chart_id == "earnings_trend"
    assert result.dashboard.secondary_chart_ids == (
        "latest_earnings_comparison",
        "earnings_absolute_change",
        "earnings_percentage_change",
        "latest_peer_set_share",
    )
    assert result.view_bundle.default_chat_view_id == "chat_dashboard"
    assert result.view_bundle.fallback_view_id == "portable_dashboard"
    assert result.visualizations[0].kind.value == "line"
    assert tuple(series.key for series in result.visualizations[0].series) == (
        "microsoft",
        "amazon",
        "meta",
        "alphabet",
        "other_hyperscaler",
    )


def test_visualizations_are_deterministic_and_renderable() -> None:
    first = _small_result()
    second = _small_result()

    assert first.visualizations == second.visualizations
    for first_chart, second_chart in zip(
        first.visualizations, second.visualizations, strict=True
    ):
        svg = render_svg(first_chart)
        assert svg == render_svg(second_chart)
        assert "<script" not in svg


def test_prescribed_dashboard_is_deterministic_and_bound_to_component_data() -> None:
    first = _small_result()
    second = _small_result()

    assert first.dashboard == second.dashboard
    assert tuple(row.key for row in first.dashboard.table.rows) == ("alpha", "beta")
    assert first.dashboard.table.rows[0].values == (
        30.0,
        20.0,
        2.0,
        20.0,
        10.0,
        0.75,
        1,
    )
    first_svg = render_dashboard_svg(first.dashboard, first.visualizations)
    second_svg = render_dashboard_svg(second.dashboard, second.visualizations)
    assert first_svg == second_svg
    assert dashboard_hash(
        first.dashboard,
        first.visualizations,
    ) == dashboard_hash(second.dashboard, second.visualizations)
    assert first_svg.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert 'data:image/svg+xml;base64,' in first_svg
    assert "<script" not in first_svg
    assert "Company statistics" in first_svg


def test_default_chat_view_is_deterministic_interactive_html_with_svg_fallback() -> None:
    first = _small_result()
    second = _small_result()
    chat = select_view(first.view_bundle, use_case="chat")
    fallback = select_view(
        first.view_bundle,
        use_case="chat",
        supported_media_types=("image/svg+xml",),
    )

    assert chat.id == "chat_dashboard"
    assert chat.media_type == "text/html"
    assert chat.interactive is True
    assert fallback.id == "portable_dashboard"
    assert fallback.media_type == "image/svg+xml"
    first_html = render_dashboard_html(first.dashboard, first.visualizations)
    second_html = render_dashboard_html(second.dashboard, second.visualizations)
    assert first_html == second_html
    assert view_hash(
        chat,
        first.dashboard,
        first.visualizations,
    ) == view_hash(chat, second.dashboard, second.visualizations)
    assert first_html.startswith('<div id="dq-earnings-comparison-dashboard"')
    assert "<!doctype" not in first_html.casefold()
    assert "<html" not in first_html.casefold()
    assert "<body" not in first_html.casefold()
    assert "data-dq-series-key" in first_html
    assert "Company statistics" in first_html
    assert "fetch(" not in first_html
    assert "http://" not in first_html
    assert "https://" not in first_html


def test_required_conventions_have_no_implicit_defaults() -> None:
    with pytest.raises(AmbiguousInput) as caught:
        preflight(
            COMPONENT_ID,
            periods=("P1", "P2"),
            series=(
                {"key": "alpha", "label": "Alpha", "values": (1.0, 2.0)},
                {"key": "beta", "label": "Beta", "values": (2.0, 3.0)},
            ),
        )

    assert {item["field"] for item in caught.value.details["questions"]} == {
        "metric_name",
        "reporting_currency",
        "value_scale",
        "period_basis",
        "source_label",
    }


def test_models_are_frozen_and_reject_extra_fields() -> None:
    inputs = Inputs(
        metric_name="Revenue",
        reporting_currency="USD",
        value_scale=ValueScale.MILLIONS,
        period_basis=FiscalPeriodBasis.CALENDAR_ALIGNED,
        periods=("P1", "P2"),
        series=(
            EarningsSeries(key="alpha", label="Alpha", values=(1.0, 2.0)),
            EarningsSeries(key="beta", label="Beta", values=(2.0, 3.0)),
        ),
        source_label="Synthetic model fixture",
    )

    with pytest.raises(ValidationError):
        inputs.__setattr__("metric_name", "Net income")
    with pytest.raises(ValidationError):
        Inputs.model_validate(
            {
                **inputs.model_dump(mode="python"),
                "unexpected": True,
            }
        )


def test_output_provenance_and_declared_units() -> None:
    result = _small_result()

    assert result.component_id == COMPONENT_ID
    assert result.version == "0.1.0"
    assert result.subject_hash == subject_hash(COMPONENT_ID)
    assert result.unit is Unit.CURRENCY
    assert result.reporting_currency == "USD"
    assert result.value_scale is ValueScale.MILLIONS
    assert result.source_label == "Synthetic hand-checkable fixture"


def test_tied_latest_leaders_are_all_reported() -> None:
    result = earnings_comparison(
        metric_name="Revenue",
        reporting_currency="USD",
        value_scale="millions",
        period_basis="calendar_aligned",
        periods=("P1", "P2"),
        series=(
            {"key": "alpha", "label": "Alpha", "values": (8.0, 10.0)},
            {"key": "beta", "label": "Beta", "values": (9.0, 10.0)},
        ),
        source_label="Synthetic tie fixture",
    )

    assert result.latest_leader_keys == ("alpha", "beta")
    assert tuple(row.latest_rank for row in result.company_statistics) == (1, 1)
