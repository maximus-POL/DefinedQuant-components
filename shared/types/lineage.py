"""Closed datapoint-level derivation references for component outputs."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SAFE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class OutputRef(BaseModel):
    """One indexed value in a component output field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(pattern=_SAFE_ID_PATTERN)
    index: int = Field(ge=0)


class InputRef(BaseModel):
    """One indexed source input, with an optional future citation join key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(pattern=_SAFE_ID_PATTERN)
    index: int = Field(ge=0)
    citation_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)


class Derivation(BaseModel):
    """Inspectable metadata connecting one output datapoint to its source inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: OutputRef
    inputs: tuple[InputRef, ...] = Field(min_length=1)
    expression: str = Field(min_length=1, max_length=500)
    value: float = Field(allow_inf_nan=False)

    @field_validator("expression")
    @classmethod
    def expression_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("derivation expression must not be blank")
        return value

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("derivation value must be finite")
        return value

    @model_validator(mode="after")
    def input_locations_must_be_unique(self) -> Derivation:
        locations = [(item.field, item.index) for item in self.inputs]
        if len(set(locations)) != len(locations):
            raise ValueError("derivation input locations must be unique")
        return self


__all__ = ["Derivation", "InputRef", "OutputRef"]
