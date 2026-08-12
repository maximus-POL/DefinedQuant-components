from __future__ import annotations

import pytest
from defined_quant import render_svg
from defined_quant.types import (
    AxisSpec,
    ChartKind,
    ChartSeries,
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
