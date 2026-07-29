"""Structured findings used by preflight and future data-quality components."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .output import ComponentOutput


class Violation(BaseModel):
    """One evaluated declarative constraint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str = Field(min_length=1, max_length=120)
    severity: Literal["warning", "blocking"]
    message: str = Field(min_length=1, max_length=1000)
    context: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """One diagnostic finding at concrete input locations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: str = Field(min_length=1, max_length=120)
    severity: Literal["info", "warning", "blocking"]
    message: str = Field(min_length=1, max_length=1000)
    locations: tuple[int | str, ...] = ()
    count: int = Field(ge=0)


class DiagnosticOutput(ComponentOutput):
    """Base output for diagnostics, including explicit assessment coverage."""

    passed: bool | None
    findings: tuple[Finding, ...]
    coverage: dict[str, float]

    @field_validator("coverage")
    @classmethod
    def validate_coverage(cls, values: dict[str, float]) -> dict[str, float]:
        if any(value < 0.0 or value > 1.0 for value in values.values()):
            raise ValueError("coverage values must be between 0 and 1")
        return values


__all__ = ["DiagnosticOutput", "Finding", "Violation"]
