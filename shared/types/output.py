"""Canonical result envelope returned by every financial component."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .units import Unit
from .visualization import VisualizationSpec


class ComponentOutput(BaseModel):
    """Provenance and interpretation fields common to all component outputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_id: str = Field(pattern=r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: Unit
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    transformations: tuple[str, ...] = ()
    visualizations: tuple[VisualizationSpec, ...] = ()

    @field_validator("assumptions", "warnings", "transformations")
    @classmethod
    def validate_messages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("output messages must not be blank")
        return values


__all__ = ["ComponentOutput"]
