"""Renderer-neutral, deterministic visualization contracts."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .units import Unit

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_VISUALIZATION_POINTS: Final = 500


class ChartKind(StrEnum):
    """Chart shapes supported by the preview renderer."""

    LINE = "line"
    BAR = "bar"


class NumberFormat(StrEnum):
    """Display formatting; it never changes the underlying numeric unit."""

    DECIMAL = "decimal"
    PERCENT = "percent"
    INTEGER = "integer"


class AxisSpec(BaseModel):
    """One chart axis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    unit: Unit = Unit.UNITLESS
    number_format: NumberFormat = NumberFormat.DECIMAL


class ChartSeries(BaseModel):
    """A named finite numeric series aligned with ``VisualizationSpec.categories``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    values: tuple[float, ...] = Field(
        min_length=1,
        max_length=MAX_VISUALIZATION_POINTS,
    )

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("series key must match ^[a-z][a-z0-9_]{0,63}$")
        return value

    @field_validator("values")
    @classmethod
    def values_must_be_finite(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(value) for value in values):
            raise ValueError("chart values must all be finite")
        return values


class VisualizationSpec(BaseModel):
    """Closed, non-executable chart description.

    The model accepts no HTML, SVG fragments, paths, URLs, CSS, or JavaScript.  Text is escaped by
    the central renderer, and styling comes from a fixed internal palette.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[0] = 0
    id: str = Field(min_length=1, max_length=64)
    kind: ChartKind
    title: str = Field(min_length=1, max_length=160)
    alt_text: str = Field(min_length=1, max_length=500)
    categories: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_VISUALIZATION_POINTS,
    )
    series: tuple[ChartSeries, ...] = Field(min_length=1, max_length=12)
    x_axis: AxisSpec
    y_axis: AxisSpec
    caption: str | None = Field(default=None, max_length=500)
    assumptions: tuple[str, ...] = Field(default=(), max_length=20)
    warnings: tuple[str, ...] = Field(default=(), max_length=20)
    width: int = Field(default=800, ge=640, le=1600)
    height: int = Field(default=440, ge=320, le=1000)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _SAFE_ID.fullmatch(value) is None:
            raise ValueError("visualization id must match ^[a-z][a-z0-9_]{0,63}$")
        return value

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 80 for value in values):
            raise ValueError("chart category labels must contain 1 to 80 characters")
        return values

    @field_validator("assumptions", "warnings")
    @classmethod
    def validate_notes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("visualization notes must contain 1 to 500 characters")
        return values

    @model_validator(mode="after")
    def validate_alignment(self) -> VisualizationSpec:
        category_count = len(self.categories)
        if any(len(series.values) != category_count for series in self.series):
            raise ValueError("every chart series must align one-to-one with categories")
        keys = [series.key for series in self.series]
        if len(set(keys)) != len(keys):
            raise ValueError("chart series keys must be unique")
        return self


__all__ = [
    "AxisSpec",
    "ChartKind",
    "ChartSeries",
    "MAX_VISUALIZATION_POINTS",
    "NumberFormat",
    "VisualizationSpec",
]
