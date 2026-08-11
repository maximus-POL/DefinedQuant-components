"""Canonical result envelope returned by every financial component."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .lineage import Derivation
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
    disclosures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    transformations: tuple[str, ...] = ()
    derivations: tuple[Derivation, ...] = ()
    visualizations: tuple[VisualizationSpec, ...] = ()

    @field_validator("assumptions", "disclosures", "warnings", "transformations")
    @classmethod
    def validate_messages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("output messages must not be blank")
        return values

    @model_validator(mode="after")
    def validate_derivation_targets(self) -> ComponentOutput:
        output_locations = [
            (derivation.output.field, derivation.output.index)
            for derivation in self.derivations
        ]
        if len(set(output_locations)) != len(output_locations):
            raise ValueError("derivation output locations must be unique")

        for derivation in self.derivations:
            output_ref = derivation.output
            if output_ref.field not in type(self).model_fields:
                raise ValueError(
                    f"derivation output field {output_ref.field!r} does not exist"
                )
            values = getattr(self, output_ref.field)
            if isinstance(values, tuple):
                if output_ref.index >= len(values):
                    raise ValueError(
                        f"derivation output index {output_ref.index} is out of range for "
                        f"{output_ref.field!r}"
                    )
                referenced_value = values[output_ref.index]
            else:
                if output_ref.index != 0:
                    raise ValueError(
                        f"scalar derivation output {output_ref.field!r} must use index 0"
                    )
                referenced_value = values
            if isinstance(referenced_value, bool) or not isinstance(
                referenced_value, (int, float)
            ):
                raise ValueError(
                    f"derivation output {output_ref.field!r} must reference a numeric value"
                )
            if referenced_value != derivation.value:
                raise ValueError("derivation value must equal its referenced output value")
        return self


__all__ = ["ComponentOutput"]
