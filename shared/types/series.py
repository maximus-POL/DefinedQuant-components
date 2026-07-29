"""Small canonical numeric-series container for cross-component contracts."""

from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .units import Frequency, Unit


class NumericSeries(BaseModel):
    """Finite values with optional aligned, timezone-aware timestamps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[float, ...]
    unit: Unit
    timestamps: tuple[datetime, ...] | None = None
    declared_frequency: Frequency | None = None

    @field_validator("values")
    @classmethod
    def values_must_be_finite(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(value) for value in values):
            raise ValueError("series values must all be finite")
        return values

    @field_validator("timestamps")
    @classmethod
    def timestamps_must_be_aware(
        cls, timestamps: tuple[datetime, ...] | None
    ) -> tuple[datetime, ...] | None:
        if timestamps is not None and any(
            value.tzinfo is None or value.utcoffset() is None for value in timestamps
        ):
            raise ValueError("series timestamps must be timezone-aware")
        return timestamps

    @model_validator(mode="after")
    def validate_alignment(self) -> NumericSeries:
        if self.timestamps is not None and len(self.timestamps) != len(self.values):
            raise ValueError("timestamps must align one-to-one with values")
        if self.timestamps is None and self.declared_frequency is not None:
            raise ValueError("declared_frequency is meaningful only when timestamps are supplied")
        return self


__all__ = ["NumericSeries"]
