from __future__ import annotations

import pytest
from defined_quant import render_svg
from defined_quant.types import (
    AxisSpec,
    ChartKind,
    ChartSeries,
    NumberFormat,
    Unit,
    VisualizationSpec,
)


def _spec(kind: ChartKind, *, point_count: int = 10_000) -> VisualizationSpec:
    values = tuple(((index % 41) - 20) / 100 for index in range(point_count))
    return VisualizationSpec(
        id=f"large_{kind.value}_chart",
        kind=kind,
        title=f"Large {kind.value} chart",
        alt_text=f"{kind.value.title()} chart containing {point_count} points.",
        categories=tuple(str(index) for index in range(point_count)),
        series=(ChartSeries(key="values", label="Values", values=values),),
        x_axis=AxisSpec(label="Observation"),
        y_axis=AxisSpec(label="Value", unit=Unit.DECIMAL),
    )


@pytest.mark.parametrize("kind", [ChartKind.LINE, ChartKind.BAR])
def test_large_chart_preserves_every_point_with_bounded_svg_elements(kind: ChartKind) -> None:
    spec = _spec(kind)

    svg = render_svg(spec)

    assert len(spec.categories) == 10_000
    assert len(spec.series[0].values) == 10_000
    assert svg.count("<path ") == 1
    assert "<circle " not in svg
    assert svg.count("<text ") <= 25
    assert svg == render_svg(spec)


def test_visualization_schema_has_no_point_count_ceiling() -> None:
    series_schema = ChartSeries.model_json_schema()["properties"]["values"]
    visualization_schema = VisualizationSpec.model_json_schema()["properties"]["categories"]

    assert "maxItems" not in series_schema
    assert "maxItems" not in visualization_schema


def test_heatmap_renders_calendar_grid_deterministically() -> None:
    spec = VisualizationSpec(
        schema_version=1,
        id="monthly_returns",
        kind=ChartKind.HEATMAP,
        title="Monthly returns",
        alt_text="Calendar heatmap of monthly simple returns.",
        categories=("2022-11", "2022-12", "2023-01"),
        series=(
            ChartSeries(
                key="monthly_returns",
                label="Monthly returns",
                values=(-0.15, 0.02, 0.11),
            ),
        ),
        x_axis=AxisSpec(label="Calendar month"),
        y_axis=AxisSpec(
            label="Simple return",
            unit=Unit.DECIMAL,
            number_format=NumberFormat.PERCENT,
        ),
    )

    svg = render_svg(spec)

    assert svg.count("<rect ") == 1 + 24 + 9
    assert "2022" in svg
    assert "2023" in svg
    assert "-15%" in svg
    assert svg == render_svg(spec)


def test_heatmap_legend_handles_large_finite_scale() -> None:
    spec = VisualizationSpec(
        schema_version=1,
        id="extreme_heatmap",
        kind=ChartKind.HEATMAP,
        title="Extreme finite heatmap",
        alt_text="Calendar heatmap containing one large finite value.",
        categories=("2023-01",),
        series=(
            ChartSeries(
                key="values",
                label="Values",
                values=(1e308,),
            ),
        ),
        x_axis=AxisSpec(label="Calendar month"),
        y_axis=AxisSpec(label="Value", unit=Unit.DECIMAL),
    )

    svg = render_svg(spec)

    assert "#B91C1C" in svg
    assert "#F9FAFB" in svg
    assert "#047857" in svg
    assert "nan" not in svg.lower()
    assert "inf" not in svg.lower()


def test_heatmap_percentage_labels_do_not_overflow() -> None:
    spec = VisualizationSpec(
        schema_version=1,
        id="extreme_percentage_heatmap",
        kind=ChartKind.HEATMAP,
        title="Extreme percentage heatmap",
        alt_text="Calendar heatmap containing one large finite percentage.",
        categories=("2023-01",),
        series=(
            ChartSeries(
                key="values",
                label="Values",
                values=(1e307,),
            ),
        ),
        x_axis=AxisSpec(label="Calendar month"),
        y_axis=AxisSpec(
            label="Return",
            unit=Unit.DECIMAL,
            number_format=NumberFormat.PERCENT,
        ),
    )

    svg = render_svg(spec)

    assert "1.000e+309%" in svg
    assert "inf" not in svg.lower()
    assert "nan" not in svg.lower()


def test_heatmap_uses_actual_nonzero_scale_for_cell_contrast() -> None:
    spec = VisualizationSpec(
        schema_version=1,
        id="tiny_heatmap",
        kind=ChartKind.HEATMAP,
        title="Tiny heatmap",
        alt_text="Calendar heatmap containing one small finite value.",
        categories=("2023-01",),
        series=(
            ChartSeries(
                key="values",
                label="Values",
                values=(1e-13,),
            ),
        ),
        x_axis=AxisSpec(label="Calendar month"),
        y_axis=AxisSpec(
            label="Return",
            unit=Unit.DECIMAL,
            number_format=NumberFormat.PERCENT,
        ),
    )

    svg = render_svg(spec)
    cell_labels = [line for line in svg.splitlines() if ">1e-11%</text>" in line]

    assert cell_labels
    assert any('fill="#FFFFFF"' in line for line in cell_labels)


def test_chart_kind_requires_its_visualization_schema_version() -> None:
    line = _spec(ChartKind.LINE, point_count=2)
    assert line.schema_version == 0

    line_payload = line.model_dump(mode="python")
    line_payload["schema_version"] = 1
    with pytest.raises(ValueError, match="line and bar charts require.*version 0"):
        VisualizationSpec.model_validate(line_payload)

    with pytest.raises(ValueError, match="heatmaps require.*version 1"):
        VisualizationSpec(
            id="unversioned_heatmap",
            kind=ChartKind.HEATMAP,
            title="Unversioned heatmap",
            alt_text="Calendar heatmap with the legacy default schema version.",
            categories=("2023-01",),
            series=(ChartSeries(key="values", label="Values", values=(0.1,)),),
            x_axis=AxisSpec(label="Calendar month"),
            y_axis=AxisSpec(label="Return", unit=Unit.DECIMAL),
        )

    schema = VisualizationSpec.model_json_schema()["properties"]["schema_version"]
    assert schema["enum"] == [0, 1]
    assert schema["default"] == 0


def test_heatmap_rejects_non_calendar_or_unsorted_categories() -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        VisualizationSpec(
            schema_version=1,
            id="invalid_heatmap",
            kind=ChartKind.HEATMAP,
            title="Invalid heatmap",
            alt_text="An invalid heatmap.",
            categories=("January",),
            series=(ChartSeries(key="values", label="Values", values=(0.1,)),),
            x_axis=AxisSpec(label="Calendar month"),
            y_axis=AxisSpec(label="Return", unit=Unit.DECIMAL),
        )

    with pytest.raises(ValueError, match="strictly increasing"):
        VisualizationSpec(
            schema_version=1,
            id="unsorted_heatmap",
            kind=ChartKind.HEATMAP,
            title="Unsorted heatmap",
            alt_text="An unsorted heatmap.",
            categories=("2023-02", "2023-01"),
            series=(
                ChartSeries(key="values", label="Values", values=(0.1, -0.1)),
            ),
            x_axis=AxisSpec(label="Calendar month"),
            y_axis=AxisSpec(label="Return", unit=Unit.DECIMAL),
        )
