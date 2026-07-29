"""Minimal canonical money value."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Money(BaseModel):
    """A finite amount paired with an explicit ISO-style currency code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: float
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("amount")
    @classmethod
    def amount_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("money amount must be finite")
        return value


__all__ = ["Money"]
