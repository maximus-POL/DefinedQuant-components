"""Deterministic implementation for ``dq.{{CATEGORY}}.{{SLUG}}``."""

from __future__ import annotations

from defined_quant.types import ComponentOutput
from pydantic import BaseModel, ConfigDict, Field

FORMULA = "TODO: provide the exact formula executed by this component."


class Inputs(BaseModel):
    """Canonical inputs. Replace the scaffold field with the real contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    values: tuple[float, ...] = Field(min_length=1)


class Output(ComponentOutput):
    """Canonical output. Replace the scaffold field with the real contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    values: tuple[float, ...]


# ``COMPONENT_FUNCTION`` is replaced with the requested slug by create_component.py.
def COMPONENT_FUNCTION(*, values: tuple[float, ...]) -> Output:
    """Compute deterministically; keep every ``Inputs`` field keyword-callable."""

    Inputs(values=values)
    raise NotImplementedError("Replace the canonical component scaffold.")


__all__ = ["FORMULA", "Inputs", "Output", "COMPONENT_FUNCTION"]
