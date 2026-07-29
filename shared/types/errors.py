"""Structured exception hierarchy for Defined Quant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DQError(Exception):
    """Base error carrying stable machine-readable context."""

    code = "defined_quant_error"

    def __init__(
        self,
        message: str,
        *,
        component_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.component_id = component_id
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible error envelope."""

        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
        if self.component_id is not None:
            result["component_id"] = self.component_id
        return result


class AmbiguousInput(DQError):
    """A convention-bearing input is missing and no valid default is declared."""

    code = "ambiguous_input"


class UnsupportedScope(DQError):
    """The requested calculation is outside a component's enforceable scope."""

    code = "unsupported_scope"


class DomainError(DQError):
    """Inputs violate a blocking financial or numerical domain constraint."""

    code = "domain_error"


class ContractEvaluationError(DomainError):
    """A declared constraint cannot be evaluated for the supplied value."""

    code = "contract_evaluation_error"


class ComponentNotFound(DQError):
    """No installed component has the requested stable ID."""

    code = "component_not_found"


class ComponentContractError(DQError):
    """An installed component contract is missing, malformed, or internally inconsistent."""

    code = "component_contract_error"


class ComponentLoadError(DQError):
    """A discovered component callable cannot be imported safely."""

    code = "component_load_error"


__all__ = [
    "AmbiguousInput",
    "ComponentContractError",
    "ComponentLoadError",
    "ComponentNotFound",
    "ContractEvaluationError",
    "DQError",
    "DomainError",
    "UnsupportedScope",
]
