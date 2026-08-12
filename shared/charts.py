"""Deterministic, dependency-free SVG rendering for closed visualization specs."""

from __future__ import annotations

import hashlib
import json
import math
import textwrap
from datetime import datetime
from html import escape
from pathlib import Path

from defined_quant.types import ChartKind, NumberFormat, VisualizationSpec

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
_MIN_MARKER_SPACING = 7.0


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
    low = math.inf
    high = -math.inf
    for series in spec.series:
        for value in series.values:
            low = min(low, value)
            high = max(high, value)
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


def _category_label_indexes(category_count: int) -> tuple[int, ...]:
    step = max(1, math.ceil(category_count / 8))
    indexes = list(range(0, category_count, step))
    if indexes[-1] != category_count - 1:
        indexes.append(category_count - 1)
    return tuple(indexes)


def _category_labels(
    categories: tuple[str, ...], indexes: tuple[int, ...]
) -> tuple[str, ...]:
    selected = tuple(categories[index] for index in indexes)
    parsed: list[datetime] = []
    for value in selected:
        try:
            parsed.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return tuple(
                value if len(value) <= 18 else value[:15] + "..." for value in selected
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
            path_commands: list[str] = []
            show_markers = (
                category_count == 1
                or plot_width / (category_count - 1) >= _MIN_MARKER_SPACING
            )
            markers: list[str] = []
            for index, value in enumerate(series.values):
                x = (
                    left + plot_width / 2
                    if category_count == 1
                    else left + index * plot_width / (category_count - 1)
                )
                y = _coordinate(value, low, high, top, bottom)
                command = "M" if index == 0 else "L"
                path_commands.append(f"{command}{x:.2f},{y:.2f}")
                if show_markers:
                    markers.append(
                        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.6" fill="{color}"/>'
                    )
            parts.append(
                f'<path d="{" ".join(path_commands)}" fill="none" stroke="{color}" '
                'stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>'
            )
            parts.extend(markers)
    else:
        group_width = plot_width / category_count
        usable_width = group_width * 0.74
        bar_width = usable_width / len(spec.series)
        zero_y = _coordinate(0.0, low, high, top, bottom)
        rectangle_width = max(bar_width - 1.5, 0.8)
        for series_index, series in enumerate(spec.series):
            path_commands = []
            for category_index in range(category_count):
                group_start = (
                    left
                    + category_index * group_width
                    + (group_width - usable_width) / 2
                )
                value_y = _coordinate(series.values[category_index], low, high, top, bottom)
                rectangle_y = min(value_y, zero_y)
                rectangle_height = max(abs(zero_y - value_y), 0.8)
                x = group_start + series_index * bar_width
                path_commands.append(
                    f"M{x:.2f},{rectangle_y:.2f}h{rectangle_width:.2f}"
                    f"v{rectangle_height:.2f}h-{rectangle_width:.2f}Z"
                )
            parts.append(
                f'<path d="{" ".join(path_commands)}" fill="{_PALETTE[series_index]}"/>'
            )

    label_indexes = _category_label_indexes(category_count)
    for index, label in zip(
        label_indexes,
        _category_labels(spec.categories, label_indexes),
        strict=True,
    ):
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


__all__ = ["render_svg", "save_svg", "visualization_hash"]
