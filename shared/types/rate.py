"""Minimal canonical interest-rate value."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, field_validator

from .units import Compounding, Frequency


class Rate(BaseModel):
    """A decimal rate with explicit compounding and observation frequency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    compounding: Compounding
    frequency: Frequency

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("rate must be finite")
        return value


__all__ = ["Rate"]
