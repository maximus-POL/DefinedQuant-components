"""Deterministic, dependency-free SVG rendering for closed visualization specs."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import textwrap
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Literal

from defined_quant.types import ChartKind, NumberFormat, VisualizationSpec
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_PALETTE = (
    "#2563EB",
    "#D97706",
    "#059669",
    "#7C3AED",
    "#DC2626",
    "#0891B2",
    "#4F46E5",
    "#65A30D",
    "#C026D3",
    "#EA580C",
    "#0F766E",
    "#475569",
)
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TableColumn(BaseModel):
    """One deterministic dashboard-table column."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    cell_format: Literal["decimal", "percent", "integer", "text"]
    decimals: int = Field(default=1, ge=0, le=6)
    show_sign: bool = False

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("table column key must match ^[a-z][a-z0-9_]{0,63}$")
        return value


class TableRow(BaseModel):
    """One labeled row aligned with ``TableSpec.columns``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    values: tuple[int | float | str | None, ...] = Field(min_length=1, max_length=12)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("table row key must match ^[a-z][a-z0-9_]{0,63}$")
        return value


class TableSpec(BaseModel):
    """Closed, non-executable table specification for one dashboard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    row_label: str = Field(min_length=1, max_length=80)
    columns: tuple[TableColumn, ...] = Field(min_length=1, max_length=12)
    rows: tuple[TableRow, ...] = Field(min_length=1, max_length=50)
    caption: str | None = Field(default=None, max_length=500)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("table id must match ^[a-z][a-z0-9_]{0,63}$")
        return value

    @model_validator(mode="after")
    def validate_alignment(self) -> TableSpec:
        column_count = len(self.columns)
        if any(len(row.values) != column_count for row in self.rows):
            raise ValueError("every table row must align one-to-one with columns")
        column_keys = [column.key for column in self.columns]
        row_keys = [row.key for row in self.rows]
        if len(set(column_keys)) != len(column_keys):
            raise ValueError("table column keys must be unique")
        if len(set(row_keys)) != len(row_keys):
            raise ValueError("table row keys must be unique")
        for row in self.rows:
            for column, value in zip(self.columns, row.values, strict=True):
                if value is None:
                    continue
                if column.cell_format == "text":
                    if not isinstance(value, str):
                        raise ValueError("text table columns require string or null values")
                elif (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise ValueError("numeric table columns require finite numbers or null")
        return self


class DashboardSpec(BaseModel):
    """Prescribed chart order, table, and layout for a deterministic component view."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[0] = 0
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=160)
    subtitle: str | None = Field(default=None, max_length=240)
    alt_text: str = Field(min_length=1, max_length=500)
    primary_chart_id: str = Field(min_length=1, max_length=64)
    secondary_chart_ids: tuple[str, ...] = Field(default=(), max_length=5)
    table: TableSpec
    notes: tuple[str, ...] = Field(default=(), max_length=12)
    width: int = Field(default=1200, ge=960, le=1600)

    @field_validator("id", "primary_chart_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("dashboard and chart ids must match ^[a-z][a-z0-9_]{0,63}$")
        return value

    @field_validator("secondary_chart_ids")
    @classmethod
    def validate_secondary_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(_SAFE_ID.fullmatch(value) is None for value in values):
            raise ValueError("dashboard chart ids must match ^[a-z][a-z0-9_]{0,63}$")
        return values

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("dashboard notes must contain 1 to 500 characters")
        return values

    @model_validator(mode="after")
    def validate_chart_ids(self) -> DashboardSpec:
        chart_ids = (self.primary_chart_id, *self.secondary_chart_ids)
        if len(set(chart_ids)) != len(chart_ids):
            raise ValueError("dashboard chart ids must be unique")
        return self


class RenderTarget(BaseModel):
    """One allowlisted renderer target available for a prescribed dashboard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    media_type: Literal["text/html", "image/svg+xml"]
    renderer: Literal["dashboard_html", "dashboard_svg"]
    use_cases: tuple[Literal["chat", "portable", "print"], ...] = Field(min_length=1)
    interactive: bool = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("render target id must match ^[a-z][a-z0-9_]{0,63}$")
        return value

    @model_validator(mode="after")
    def validate_renderer_media_type(self) -> RenderTarget:
        expected = {
            "dashboard_html": "text/html",
            "dashboard_svg": "image/svg+xml",
        }[self.renderer]
        if self.media_type != expected:
            raise ValueError(f"{self.renderer} requires media type {expected}")
        if self.interactive and self.renderer != "dashboard_html":
            raise ValueError("only the trusted dashboard HTML renderer is interactive")
        if len(set(self.use_cases)) != len(self.use_cases):
            raise ValueError("render target use cases must be unique")
        return self


class ViewBundleSpec(BaseModel):
    """Closed format negotiation for one component-declared dashboard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[0] = 0
    default_chat_view_id: str = Field(min_length=1, max_length=64)
    fallback_view_id: str = Field(min_length=1, max_length=64)
    views: tuple[RenderTarget, ...] = Field(min_length=1, max_length=8)

    @field_validator("default_chat_view_id", "fallback_view_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("view bundle ids must match ^[a-z][a-z0-9_]{0,63}$")
        return value

    @model_validator(mode="after")
    def validate_view_ids(self) -> ViewBundleSpec:
        ids = [view.id for view in self.views]
        if len(set(ids)) != len(ids):
            raise ValueError("render target ids must be unique")
        if self.default_chat_view_id not in ids:
            raise ValueError("default chat view id must identify a declared render target")
        if self.fallback_view_id not in ids:
            raise ValueError("fallback view id must identify a declared render target")
        default = next(view for view in self.views if view.id == self.default_chat_view_id)
        if "chat" not in default.use_cases:
            raise ValueError("default chat view must declare the chat use case")
        return self


def visualization_hash(spec: VisualizationSpec) -> str:
    """Hash the canonical, renderer-neutral chart specification."""

    payload = json.dumps(
        spec.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _format_number(value: float, number_format: NumberFormat) -> str:
    if number_format is NumberFormat.PERCENT:
        return f"{value * 100:.4g}%"
    if number_format is NumberFormat.INTEGER:
        return f"{value:.0f}"
    return f"{value:.6g}"


def _coordinate(value: float, low: float, high: float, top: float, bottom: float) -> float:
    return bottom - ((value - low) / (high - low)) * (bottom - top)


def _domain(spec: VisualizationSpec) -> tuple[float, float]:
    values = [value for series in spec.series for value in series.values]
    low = min(values)
    high = max(values)
    if spec.kind is ChartKind.BAR:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if low == high:
        padding = max(abs(low) * 0.1, 1e-9)
        low -= padding
        high += padding
    else:
        padding = (high - low) * 0.08
        low -= padding
        high += padding
    return low, high


def _footer_lines(spec: VisualizationSpec) -> tuple[str, ...]:
    notes: list[str] = []
    if spec.caption:
        notes.append(spec.caption)
    if spec.assumptions:
        notes.append("Assumptions: " + "; ".join(spec.assumptions))
    if spec.warnings:
        notes.append("Warnings: " + "; ".join(spec.warnings))
    if not notes:
        return ()
    characters = max(45, (spec.width - 144) // 7)
    lines: list[str] = []
    for note in notes:
        lines.extend(
            textwrap.wrap(
                note,
                width=characters,
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [""]
        )
    return tuple(lines)


def _category_labels(categories: tuple[str, ...]) -> tuple[str, ...]:
    parsed: list[datetime] = []
    for value in categories:
        try:
            parsed.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return tuple(
                value if len(value) <= 18 else value[:15] + "..." for value in categories
            )
    dates = [value.date().isoformat() for value in parsed]
    if len(set(dates)) == len(dates):
        return tuple(dates)
    return tuple(value.strftime("%Y-%m-%d %H:%M") for value in parsed)


def render_svg(spec: VisualizationSpec) -> str:
    """Render a safe SVG string with stable bytes for the same validated specification.

    Only escaped text and numeric geometry produced by this module enter the document.  The SVG
    has no scripts, event handlers, embedded HTML, external resources, links, or caller-supplied
    style declarations.
    """

    width = spec.width
    height = spec.height
    left = 76.0
    right = float(width - 28)
    top = 58.0
    footer_lines = _footer_lines(spec)
    footer_height = len(footer_lines) * 15.0
    bottom = float(height) - 64.0 - footer_height
    if bottom - top < 120.0:
        raise ValueError("visualization notes leave insufficient space for a chart")

    low, high = _domain(spec)
    category_count = len(spec.categories)
    plot_width = right - left
    plot_height = bottom - top
    title_id = f"{spec.id}_title"
    description_id = f"{spec.id}_description"
    description_parts = [spec.alt_text]
    description_parts.extend(spec.assumptions)
    description_parts.extend(spec.warnings)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="{title_id} {description_id}">'
        ),
        f'<title id="{title_id}">{escape(spec.title)}</title>',
        f'<desc id="{description_id}">{escape(" ".join(description_parts))}</desc>',
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        (
            f'<text x="{left:.2f}" y="29" fill="#111827" font-family="system-ui, sans-serif" '
            f'font-size="18" font-weight="600">{escape(spec.title)}</text>'
        ),
    ]

    tick_count = 5
    for index in range(tick_count):
        fraction = index / (tick_count - 1)
        value = high - fraction * (high - low)
        y = top + fraction * plot_height
        parts.append(
            f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{right:.2f}" y2="{y:.2f}" '
            'stroke="#E5E7EB" stroke-width="1"/>'
        )
        tick = escape(_format_number(value, spec.y_axis.number_format))
        parts.append(
            f'<text x="{left - 9:.2f}" y="{y + 4:.2f}" text-anchor="end" '
            f'fill="#4B5563" font-family="ui-monospace, monospace" font-size="11">{tick}</text>'
        )

    parts.extend(
        [
            (
                f'<line x1="{left:.2f}" y1="{top:.2f}" x2="{left:.2f}" y2="{bottom:.2f}" '
                'stroke="#9CA3AF" stroke-width="1"/>'
            ),
            (
                f'<line x1="{left:.2f}" y1="{bottom:.2f}" x2="{right:.2f}" '
                f'y2="{bottom:.2f}" stroke="#9CA3AF" stroke-width="1"/>'
            ),
        ]
    )

    if low <= 0.0 <= high:
        zero_y = _coordinate(0.0, low, high, top, bottom)
        parts.append(
            f'<line x1="{left:.2f}" y1="{zero_y:.2f}" x2="{right:.2f}" '
            f'y2="{zero_y:.2f}" stroke="#6B7280" stroke-width="1.2"/>'
        )

    if spec.kind is ChartKind.LINE:
        for series_index, series in enumerate(spec.series):
            color = _PALETTE[series_index]
            coordinates: list[tuple[float, float]] = []
            for index, value in enumerate(series.values):
                x = (
                    left + plot_width / 2
                    if category_count == 1
                    else left + index * plot_width / (category_count - 1)
                )
                y = _coordinate(value, low, high, top, bottom)
                coordinates.append((x, y))
            for (left_x, left_y), (right_x, right_y) in zip(
                coordinates, coordinates[1:], strict=False
            ):
                parts.append(
                    f'<line x1="{left_x:.2f}" y1="{left_y:.2f}" '
                    f'x2="{right_x:.2f}" y2="{right_y:.2f}" stroke="{color}" '
                    'stroke-width="2.25" stroke-linecap="round"/>'
                )
            for x, y in coordinates:
                parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.6" fill="{color}"/>')
    else:
        group_width = plot_width / category_count
        usable_width = group_width * 0.74
        bar_width = usable_width / len(spec.series)
        zero_y = _coordinate(0.0, low, high, top, bottom)
        for category_index in range(category_count):
            group_start = left + category_index * group_width + (group_width - usable_width) / 2
            for series_index, series in enumerate(spec.series):
                value_y = _coordinate(series.values[category_index], low, high, top, bottom)
                rectangle_y = min(value_y, zero_y)
                rectangle_height = max(abs(zero_y - value_y), 0.8)
                x = group_start + series_index * bar_width
                parts.append(
                    f'<rect x="{x:.2f}" y="{rectangle_y:.2f}" '
                    f'width="{max(bar_width - 1.5, 0.8):.2f}" height="{rectangle_height:.2f}" '
                    f'fill="{_PALETTE[series_index]}"/>'
                )

    label_step = max(1, math.ceil(category_count / 8))
    for index, label in enumerate(_category_labels(spec.categories)):
        if index % label_step != 0 and index != category_count - 1:
            continue
        x = (
            left + plot_width / 2
            if category_count == 1
            else left + index * plot_width / max(category_count - 1, 1)
        )
        parts.append(
            f'<text x="{x:.2f}" y="{bottom + 18:.2f}" text-anchor="middle" '
            f'fill="#4B5563" font-family="system-ui, sans-serif" font-size="10">'
            f"{escape(label)}</text>"
        )

    parts.extend(
        [
            (
                f'<text x="{(left + right) / 2:.2f}" y="{bottom + 39:.2f}" text-anchor="middle" '
                f'fill="#374151" font-family="system-ui, sans-serif" font-size="11">'
                f"{escape(spec.x_axis.label)}</text>"
            ),
            (
                f'<text x="17" y="{(top + bottom) / 2:.2f}" text-anchor="middle" '
                f'transform="rotate(-90 17 {(top + bottom) / 2:.2f})" fill="#374151" '
                f'font-family="system-ui, sans-serif" font-size="11">'
                f"{escape(spec.y_axis.label)} [{escape(spec.y_axis.unit.value)}]</text>"
            ),
        ]
    )

    legend_x = left
    legend_y = 47.0
    for series_index, series in enumerate(spec.series):
        if series_index:
            legend_x += 18.0
        parts.append(
            f'<rect x="{legend_x:.2f}" y="{legend_y - 8:.2f}" width="10" height="10" '
            f'fill="{_PALETTE[series_index]}"/>'
        )
        legend_x += 14.0
        parts.append(
            f'<text x="{legend_x:.2f}" y="{legend_y:.2f}" fill="#374151" '
            f'font-family="system-ui, sans-serif" font-size="10">{escape(series.label)}</text>'
        )
        legend_x += max(48.0, len(series.label) * 6.2)

    footer_y = bottom + 57.0
    for line in footer_lines:
        parts.append(
            f'<text x="{left:.2f}" y="{footer_y:.2f}" fill="#4B5563" '
            f'font-family="system-ui, sans-serif" font-size="10">{escape(line)}</text>'
        )
        footer_y += 15.0

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _dashboard_charts(
    spec: DashboardSpec,
    visualizations: tuple[VisualizationSpec, ...] | list[VisualizationSpec],
) -> tuple[VisualizationSpec, ...]:
    lookup: dict[str, VisualizationSpec] = {}
    for visualization in visualizations:
        if visualization.id in lookup:
            raise ValueError(f"duplicate visualization id: {visualization.id}")
        lookup[visualization.id] = visualization
    requested = (spec.primary_chart_id, *spec.secondary_chart_ids)
    missing = [chart_id for chart_id in requested if chart_id not in lookup]
    if missing:
        raise ValueError(
            "dashboard references unavailable visualization ids: " + ", ".join(missing)
        )
    return tuple(lookup[chart_id] for chart_id in requested)


def dashboard_hash(
    spec: DashboardSpec,
    visualizations: tuple[VisualizationSpec, ...] | list[VisualizationSpec],
) -> str:
    """Hash one dashboard specification and its selected chart specifications."""

    selected = _dashboard_charts(spec, visualizations)
    payload = {
        "dashboard": spec.model_dump(mode="json"),
        "visualizations": [
            visualization.model_dump(mode="json") for visualization in selected
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def select_view(
    bundle: ViewBundleSpec,
    *,
    use_case: Literal["chat", "portable", "print"],
    supported_media_types: tuple[str, ...] | list[str] = (),
) -> RenderTarget:
    """Select one declared renderer target with deterministic capability fallback."""

    supported = set(supported_media_types)

    def is_supported(view: RenderTarget) -> bool:
        return not supported or view.media_type in supported

    lookup = {view.id: view for view in bundle.views}
    if use_case == "chat":
        default = lookup[bundle.default_chat_view_id]
        if is_supported(default):
            return default
    for view in bundle.views:
        if use_case in view.use_cases and is_supported(view):
            return view
    fallback = lookup[bundle.fallback_view_id]
    if is_supported(fallback):
        return fallback
    raise ValueError(
        f"no declared {use_case} view supports media types: "
        + ", ".join(sorted(supported))
    )


def view_hash(
    target: RenderTarget,
    dashboard: DashboardSpec,
    visualizations: tuple[VisualizationSpec, ...] | list[VisualizationSpec],
) -> str:
    """Hash a renderer target with its dashboard and selected chart specifications."""

    selected = _dashboard_charts(dashboard, visualizations)
    payload = {
        "target": target.model_dump(mode="json"),
        "dashboard": dashboard.model_dump(mode="json"),
        "visualizations": [
            visualization.model_dump(mode="json") for visualization in selected
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _format_table_cell(value: int | float | str | None, column: TableColumn) -> str:
    if value is None:
        return "—"
    if column.cell_format == "text":
        return str(value)
    numeric = float(value)
    sign = "+" if column.show_sign and numeric > 0.0 else ""
    if column.cell_format == "percent":
        return f"{sign}{numeric * 100:,.{column.decimals}f}%"
    if column.cell_format == "integer":
        return f"{numeric:,.0f}"
    return f"{sign}{numeric:,.{column.decimals}f}"


def _svg_data_uri(spec: VisualizationSpec) -> str:
    encoded = base64.b64encode(render_svg(spec).encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _chat_series_color(index: int) -> str:
    return f"var(--viz-series-{index % 6 + 1})"


def _chat_chart_svg(
    spec: VisualizationSpec,
    *,
    primary: bool,
    label_to_key: dict[str, str],
) -> str:
    width = 760 if primary else 360
    height = 350 if primary else 270
    left = 58.0 if primary else 52.0
    right_padding = 145.0 if primary and spec.kind is ChartKind.LINE else 24.0
    right = float(width) - right_padding
    top = 22.0
    bottom = float(height) - 42.0
    plot_width = right - left
    plot_height = bottom - top
    low, high = _domain(spec)
    category_count = len(spec.categories)
    parts = [
        (
            f'<svg class="dq-chat-chart" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{escape(spec.alt_text)}">'
        )
    ]
    for index in range(5):
        fraction = index / 4
        value = high - fraction * (high - low)
        y = top + fraction * plot_height
        parts.append(
            f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{right:.2f}" y2="{y:.2f}" '
            'class="dq-grid"/>'
        )
        parts.append(
            f'<text x="{left - 8.0:.2f}" y="{y + 4.0:.2f}" text-anchor="end" '
            f'class="dq-tick">{escape(_format_number(value, spec.y_axis.number_format))}</text>'
        )
    parts.extend(
        [
            (
                f'<line x1="{left:.2f}" y1="{top:.2f}" x2="{left:.2f}" '
                f'y2="{bottom:.2f}" class="dq-axis"/>'
            ),
            (
                f'<line x1="{left:.2f}" y1="{bottom:.2f}" x2="{right:.2f}" '
                f'y2="{bottom:.2f}" class="dq-axis"/>'
            ),
        ]
    )
    if low <= 0.0 <= high:
        zero_y = _coordinate(0.0, low, high, top, bottom)
        parts.append(
            f'<line x1="{left:.2f}" y1="{zero_y:.2f}" x2="{right:.2f}" '
            f'y2="{zero_y:.2f}" class="dq-zero"/>'
        )
    if spec.kind is ChartKind.LINE:
        for series_index, series in enumerate(spec.series):
            color = _chat_series_color(series_index)
            company_key = series.key if series.key in label_to_key.values() else ""
            data_attribute = (
                f' data-dq-company-key="{escape(company_key)}"' if company_key else ""
            )
            coordinates: list[tuple[float, float]] = []
            for index, value in enumerate(series.values):
                x = (
                    left + plot_width / 2.0
                    if category_count == 1
                    else left + index * plot_width / (category_count - 1)
                )
                coordinates.append((x, _coordinate(value, low, high, top, bottom)))
            point_text = " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates)
            parts.append(
                f'<g{data_attribute}><polyline points="{point_text}" '
                f'stroke="{color}" class="dq-line"/>'
            )
            for index, ((x, y), value) in enumerate(
                zip(coordinates, series.values, strict=True)
            ):
                tooltip = (
                    f"{series.label} · {spec.categories[index]}: "
                    f"{_format_number(value, spec.y_axis.number_format)}"
                )
                parts.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}" '
                    f'class="dq-point"><title>{escape(tooltip)}</title></circle>'
                )
            parts.append("</g>")
            if primary:
                x, y = coordinates[-1]
                latest = _format_number(series.values[-1], spec.y_axis.number_format)
                parts.append(
                    f'<text x="{x + 10.0:.2f}" y="{y + 4.0:.2f}" '
                    f'class="dq-end-label">{escape(series.label)} · {escape(latest)}</text>'
                )
    else:
        group_width = plot_width / category_count
        usable_width = group_width * 0.72
        bar_width = usable_width / len(spec.series)
        zero_y = _coordinate(0.0, low, high, top, bottom)
        for category_index, category in enumerate(spec.categories):
            group_start = left + category_index * group_width + (group_width - usable_width) / 2
            company_key = label_to_key.get(category)
            data_attribute = (
                f' data-dq-company-key="{escape(company_key)}"' if company_key else ""
            )
            parts.append(f"<g{data_attribute}>")
            for series_index, series in enumerate(spec.series):
                value = series.values[category_index]
                value_y = _coordinate(value, low, high, top, bottom)
                rectangle_y = min(value_y, zero_y)
                rectangle_height = max(abs(zero_y - value_y), 0.8)
                x = group_start + series_index * bar_width
                color = _chat_series_color(series_index)
                tooltip = (
                    f"{category} · {series.label}: "
                    f"{_format_number(value, spec.y_axis.number_format)}"
                )
                parts.append(
                    f'<rect x="{x:.2f}" y="{rectangle_y:.2f}" '
                    f'width="{max(bar_width - 1.5, 0.8):.2f}" '
                    f'height="{rectangle_height:.2f}" fill="{color}">'
                    f"<title>{escape(tooltip)}</title></rect>"
                )
            parts.append("</g>")
    label_step = max(1, math.ceil(category_count / (7 if primary else 5)))
    for index, category in enumerate(spec.categories):
        if index % label_step != 0 and index != category_count - 1:
            continue
        x = (
            left + plot_width / 2.0
            if category_count == 1
            else left + index * plot_width / max(category_count - 1, 1)
        )
        label = category if len(category) <= 14 else category[:11] + "..."
        parts.append(
            f'<text x="{x:.2f}" y="{bottom + 18.0:.2f}" text-anchor="middle" '
            f'class="dq-tick">{escape(label)}</text>'
        )
    parts.append(
        f'<text x="{(left + right) / 2.0:.2f}" y="{height - 5.0:.2f}" '
        f'text-anchor="middle" class="dq-axis-label">{escape(spec.x_axis.label)}</text>'
    )
    parts.append(
        f'<text x="13" y="{(top + bottom) / 2.0:.2f}" text-anchor="middle" '
        f'transform="rotate(-90 13 {(top + bottom) / 2.0:.2f})" '
        f'class="dq-axis-label">{escape(spec.y_axis.label)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def render_dashboard_html(
    spec: DashboardSpec,
    visualizations: tuple[VisualizationSpec, ...] | list[VisualizationSpec],
) -> str:
    """Render a prescribed dashboard to a deterministic, responsive chat fragment."""

    selected = _dashboard_charts(spec, visualizations)
    root_id = f"dq-{spec.id.replace('_', '-')}"
    label_to_key = {row.label: row.key for row in spec.table.rows}
    primary = selected[0]
    secondary = selected[1:]
    parts = [f'<div id="{root_id}" class="dq-chat-dashboard">']
    if spec.subtitle:
        parts.append(f'<p class="text-muted dq-context">{escape(spec.subtitle)}</p>')
    if len(primary.series) > 1:
        parts.append(
            '<div class="viz-controls dq-series-controls" '
            'aria-label="Highlight a company">'
        )
        for index, series in enumerate(primary.series):
            if series.key not in label_to_key.values():
                continue
            parts.append(
                f'<button type="button" class="btn btn-ghost" aria-pressed="false" '
                f'data-dq-series-key="{escape(series.key)}">'
                f'<span class="dq-swatch" style="background:{_chat_series_color(index)}"></span>'
                f"{escape(series.label)}</button>"
            )
        parts.append("</div>")
        parts.append(
            '<p class="text-small text-muted dq-selection" aria-live="polite">'
            "All companies shown</p>"
        )
    parts.extend(
        [
            f'<section aria-labelledby="{root_id}-primary-title">',
            f'<h3 id="{root_id}-primary-title">{escape(primary.title)}</h3>',
            _chat_chart_svg(primary, primary=True, label_to_key=label_to_key),
            "</section>",
        ]
    )
    if secondary:
        parts.append('<div class="dq-secondary-grid">')
        for index, chart in enumerate(secondary):
            parts.extend(
                [
                    f'<section aria-labelledby="{root_id}-secondary-{index}-title">',
                    (
                        f'<h3 id="{root_id}-secondary-{index}-title">'
                        f"{escape(chart.title)}</h3>"
                    ),
                    _chat_chart_svg(chart, primary=False, label_to_key=label_to_key),
                    "</section>",
                ]
            )
        parts.append("</div>")
    parts.extend(
        [
            f'<section aria-labelledby="{root_id}-table-title">',
            f'<h3 id="{root_id}-table-title">{escape(spec.table.title)}</h3>',
            '<div class="table-responsive"><table class="table table-sm">',
            "<thead><tr>",
            f"<th>{escape(spec.table.row_label)}</th>",
        ]
    )
    for column in spec.table.columns:
        parts.append(f'<th class="text-end">{escape(column.label)}</th>')
    parts.extend(["</tr></thead>", "<tbody>"])
    for row in spec.table.rows:
        parts.append(f'<tr data-dq-row-key="{escape(row.key)}">')
        parts.append(f"<td>{escape(row.label)}</td>")
        for column, value in zip(spec.table.columns, row.values, strict=True):
            parts.append(
                f'<td class="text-end text-nowrap">'
                f"{escape(_format_table_cell(value, column))}</td>"
            )
        parts.append("</tr>")
    parts.extend(["</tbody></table></div>", "</section>"])
    if spec.table.caption:
        parts.append(
            f'<p class="text-small text-muted dq-note">{escape(spec.table.caption)}</p>'
        )
    for note in spec.notes:
        parts.append(f'<p class="text-small text-muted dq-note">{escape(note)}</p>')
    parts.append("</div>")
    selector = f"#{root_id}"
    css = """
<style>
__ROOT__ {
  width: 100%;
  color: var(--foreground);
}
__ROOT__ .dq-context,
__ROOT__ .dq-selection,
__ROOT__ .dq-note {
  margin: 0.35rem 0;
}
__ROOT__ section {
  margin-top: 1rem;
}
__ROOT__ h3 {
  margin: 0 0 0.35rem;
  font-weight: 500;
}
__ROOT__ .dq-series-controls {
  margin-top: 0.65rem;
}
__ROOT__ .dq-series-controls .btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
__ROOT__ .dq-swatch {
  width: 0.65rem;
  height: 0.65rem;
  border-radius: 50%;
  flex: 0 0 auto;
}
__ROOT__ .dq-secondary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}
__ROOT__ .dq-secondary-grid section {
  min-width: 0;
}
__ROOT__ .dq-chat-chart {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
}
__ROOT__ .dq-grid {
  stroke: var(--border);
  stroke-width: 1;
}
__ROOT__ .dq-axis,
__ROOT__ .dq-zero {
  stroke: var(--muted-foreground);
  stroke-width: 1;
}
__ROOT__ .dq-tick,
__ROOT__ .dq-axis-label {
  fill: var(--muted-foreground);
  font-family: inherit;
  font-size: 11px;
}
__ROOT__ .dq-end-label {
  fill: var(--foreground);
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
}
__ROOT__ .dq-line {
  fill: none;
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}
__ROOT__ .dq-point {
  stroke: var(--background);
  stroke-width: 1.5;
}
__ROOT__ [data-dq-company-key] {
  transition: opacity 160ms ease;
}
__ROOT__ .dq-dimmed {
  opacity: 0.14;
}
__ROOT__ tr.dq-selected {
  background: var(--accent);
  color: var(--accent-foreground);
}
@media (max-width: 560px) {
  __ROOT__ .dq-secondary-grid {
    grid-template-columns: 1fr;
  }
}
@media (prefers-reduced-motion: reduce) {
  __ROOT__ [data-dq-company-key] {
    transition: none;
  }
}
</style>
""".replace("__ROOT__", selector)
    script = f"""
<script>
(() => {{
  const root = document.getElementById({json.dumps(root_id)});
  if (!root) return;
  const buttons = Array.from(root.querySelectorAll("[data-dq-series-key]"));
  const selection = root.querySelector(".dq-selection");
  buttons.forEach((button) => {{
    button.addEventListener("click", () => {{
      const key = button.dataset.dqSeriesKey;
      const activate = button.getAttribute("aria-pressed") !== "true";
      buttons.forEach((candidate) => {{
        candidate.setAttribute(
          "aria-pressed",
          String(activate && candidate.dataset.dqSeriesKey === key)
        );
      }});
      root.querySelectorAll("[data-dq-company-key]").forEach((element) => {{
        const matches = element.dataset.dqCompanyKey === key;
        element.classList.toggle("dq-dimmed", activate && !matches);
      }});
      root.querySelectorAll("[data-dq-row-key]").forEach((row) => {{
        row.classList.toggle(
          "dq-selected",
          activate && row.dataset.dqRowKey === key
        );
      }});
      if (selection) {{
        selection.textContent = activate
          ? `${{button.textContent.trim()}} highlighted`
          : "All companies shown";
      }}
    }});
  }});
}})();
</script>
"""
    return "\n".join(parts) + css + script


def render_dashboard_svg(
    spec: DashboardSpec,
    visualizations: tuple[VisualizationSpec, ...] | list[VisualizationSpec],
) -> str:
    """Render a prescribed dashboard to deterministic, non-executable SVG."""

    selected = _dashboard_charts(spec, visualizations)
    primary = selected[0]
    secondary = selected[1:]
    width = spec.width
    margin = 40.0
    content_width = float(width) - 2.0 * margin
    header_height = 92.0
    gap = 18.0
    primary_height = min(
        620.0,
        max(420.0, content_width * primary.height / primary.width),
    )
    secondary_columns = 2
    secondary_width = (content_width - gap) / secondary_columns
    secondary_heights = [
        min(360.0, max(280.0, secondary_width * chart.height / chart.width))
        for chart in secondary
    ]
    secondary_rows: list[float] = []
    for index in range(0, len(secondary_heights), secondary_columns):
        secondary_rows.append(max(secondary_heights[index : index + secondary_columns]))
    secondary_height = (
        math.fsum(secondary_rows) + gap * max(len(secondary_rows) - 1, 0)
        if secondary_rows
        else 0.0
    )
    charts_bottom = header_height + primary_height
    if secondary_rows:
        charts_bottom += gap + secondary_height

    table_top = charts_bottom + 42.0
    table_title_height = 34.0
    table_header_height = 34.0
    table_row_height = 30.0
    table_body_height = table_row_height * len(spec.table.rows)
    table_bottom = (
        table_top + table_title_height + table_header_height + table_body_height
    )
    caption_lines = (
        textwrap.wrap(
            spec.table.caption,
            width=max(50, int(content_width // 7)),
            break_long_words=False,
            break_on_hyphens=False,
        )
        if spec.table.caption
        else []
    )
    note_lines: list[str] = []
    for note in spec.notes:
        note_lines.extend(
            textwrap.wrap(
                note,
                width=max(50, int(content_width // 7)),
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [""]
        )
    footer_lines = [*caption_lines, *note_lines]
    footer_height = 24.0 + 16.0 * len(footer_lines) if footer_lines else 20.0
    height = int(math.ceil(table_bottom + footer_height + 24.0))

    title_id = f"{spec.id}_title"
    description_id = f"{spec.id}_description"
    description = " ".join((spec.alt_text, *spec.notes))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="{title_id} {description_id}">'
        ),
        f'<title id="{title_id}">{escape(spec.title)}</title>',
        f'<desc id="{description_id}">{escape(description)}</desc>',
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        (
            f'<text x="{margin:.2f}" y="38" fill="#111827" '
            'font-family="system-ui, sans-serif" font-size="24" font-weight="600">'
            f"{escape(spec.title)}</text>"
        ),
    ]
    if spec.subtitle:
        parts.append(
            f'<text x="{margin:.2f}" y="64" fill="#4B5563" '
            'font-family="system-ui, sans-serif" font-size="13">'
            f"{escape(spec.subtitle)}</text>"
        )

    parts.append(
        f'<image x="{margin:.2f}" y="{header_height:.2f}" '
        f'width="{content_width:.2f}" height="{primary_height:.2f}" '
        f'href="{_svg_data_uri(primary)}" preserveAspectRatio="xMidYMid meet" '
        'aria-hidden="true"/>'
    )
    secondary_top = header_height + primary_height + gap
    for index, chart in enumerate(secondary):
        row = index // secondary_columns
        column = index % secondary_columns
        row_y = secondary_top + math.fsum(secondary_rows[:row]) + gap * row
        x = margin + column * (secondary_width + gap)
        chart_height = secondary_heights[index]
        parts.append(
            f'<image x="{x:.2f}" y="{row_y:.2f}" '
            f'width="{secondary_width:.2f}" height="{chart_height:.2f}" '
            f'href="{_svg_data_uri(chart)}" preserveAspectRatio="xMidYMid meet" '
            'aria-hidden="true"/>'
        )

    parts.append(
        f'<text x="{margin:.2f}" y="{table_top + 22.0:.2f}" fill="#111827" '
        'font-family="system-ui, sans-serif" font-size="18" font-weight="600">'
        f"{escape(spec.table.title)}</text>"
    )
    table_header_y = table_top + table_title_height
    row_label_width = min(240.0, content_width * 0.24)
    value_column_width = (content_width - row_label_width) / len(spec.table.columns)
    parts.extend(
        [
            (
                f'<line x1="{margin:.2f}" y1="{table_header_y:.2f}" '
                f'x2="{margin + content_width:.2f}" y2="{table_header_y:.2f}" '
                'stroke="#9CA3AF" stroke-width="1"/>'
            ),
            (
                f'<text x="{margin + 8.0:.2f}" y="{table_header_y + 22.0:.2f}" '
                'fill="#374151" font-family="system-ui, sans-serif" '
                f'font-size="11" font-weight="600">{escape(spec.table.row_label)}</text>'
            ),
        ]
    )
    for index, column in enumerate(spec.table.columns):
        right_x = margin + row_label_width + (index + 1) * value_column_width - 8.0
        parts.append(
            f'<text x="{right_x:.2f}" y="{table_header_y + 22.0:.2f}" '
            'text-anchor="end" fill="#374151" font-family="system-ui, sans-serif" '
            f'font-size="11" font-weight="600">{escape(column.label)}</text>'
        )
    header_bottom = table_header_y + table_header_height
    parts.append(
        f'<line x1="{margin:.2f}" y1="{header_bottom:.2f}" '
        f'x2="{margin + content_width:.2f}" y2="{header_bottom:.2f}" '
        'stroke="#D1D5DB" stroke-width="1"/>'
    )
    for row_index, row in enumerate(spec.table.rows):
        row_top = header_bottom + row_index * table_row_height
        text_y = row_top + 20.0
        parts.append(
            f'<text x="{margin + 8.0:.2f}" y="{text_y:.2f}" fill="#111827" '
            f'font-family="system-ui, sans-serif" font-size="11">{escape(row.label)}</text>'
        )
        for column_index, (column, value) in enumerate(
            zip(spec.table.columns, row.values, strict=True)
        ):
            right_x = (
                margin
                + row_label_width
                + (column_index + 1) * value_column_width
                - 8.0
            )
            formatted = escape(_format_table_cell(value, column))
            parts.append(
                f'<text x="{right_x:.2f}" y="{text_y:.2f}" text-anchor="end" '
                'fill="#111827" font-family="ui-monospace, monospace" '
                f'font-size="11">{formatted}</text>'
            )
        parts.append(
            f'<line x1="{margin:.2f}" y1="{row_top + table_row_height:.2f}" '
            f'x2="{margin + content_width:.2f}" '
            f'y2="{row_top + table_row_height:.2f}" stroke="#E5E7EB" stroke-width="1"/>'
        )

    footer_y = table_bottom + 24.0
    for line in footer_lines:
        parts.append(
            f'<text x="{margin:.2f}" y="{footer_y:.2f}" fill="#4B5563" '
            f'font-family="system-ui, sans-serif" font-size="10">{escape(line)}</text>'
        )
        footer_y += 16.0
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def save_svg(
    spec: VisualizationSpec,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Render to a caller-selected ``.svg`` path and return its absolute path."""

    destination = Path(path).expanduser().resolve()
    if destination.suffix.lower() != ".svg":
        raise ValueError("SVG output path must end in .svg")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"SVG output directory does not exist: {destination.parent}")
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(render_svg(spec))
    return destination


def save_dashboard_svg(
    spec: DashboardSpec,
    visualizations: tuple[VisualizationSpec, ...] | list[VisualizationSpec],
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Render one prescribed dashboard to a caller-selected ``.svg`` path."""

    destination = Path(path).expanduser().resolve()
    if destination.suffix.lower() != ".svg":
        raise ValueError("SVG output path must end in .svg")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"SVG output directory does not exist: {destination.parent}")
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(render_dashboard_svg(spec, visualizations))
    return destination


def save_dashboard_html(
    spec: DashboardSpec,
    visualizations: tuple[VisualizationSpec, ...] | list[VisualizationSpec],
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Render one prescribed dashboard to a caller-selected HTML fragment path."""

    destination = Path(path).expanduser().resolve()
    if destination.suffix.lower() != ".html":
        raise ValueError("HTML output path must end in .html")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"HTML output directory does not exist: {destination.parent}")
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(render_dashboard_html(spec, visualizations))
    return destination


__all__ = [
    "DashboardSpec",
    "RenderTarget",
    "TableColumn",
    "TableRow",
    "TableSpec",
    "ViewBundleSpec",
    "dashboard_hash",
    "render_dashboard_html",
    "render_dashboard_svg",
    "render_svg",
    "save_dashboard_html",
    "save_dashboard_svg",
    "save_svg",
    "select_view",
    "view_hash",
    "visualization_hash",
]
