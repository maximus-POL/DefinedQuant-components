"""Canonical, frozen financial contract types."""

from .daycount import DayCountConvention
from .diagnostics import DiagnosticOutput, Finding, Violation
from .errors import (
    AmbiguousInput,
    ComponentContractError,
    ComponentLoadError,
    ComponentNotFound,
    ContractEvaluationError,
    DomainError,
    DQError,
    UnsupportedScope,
)
from .lineage import Derivation, InputRef, OutputRef
from .money import Money
from .output import ComponentOutput
from .rate import Rate
from .series import NumericSeries
from .units import Compounding, Frequency, PriceKind, ReturnKind, Unit
from .visualization import (
    MAX_VISUALIZATION_POINTS,
    AxisSpec,
    ChartKind,
    ChartSeries,
    NumberFormat,
    VisualizationSpec,
)

__all__ = [
    "AmbiguousInput",
    "AxisSpec",
    "ChartKind",
    "ChartSeries",
    "Compounding",
    "ComponentContractError",
    "ComponentLoadError",
    "ComponentNotFound",
    "ComponentOutput",
    "ContractEvaluationError",
    "DQError",
    "DayCountConvention",
    "DiagnosticOutput",
    "Derivation",
    "DomainError",
    "Finding",
    "Frequency",
    "InputRef",
    "MAX_VISUALIZATION_POINTS",
    "Money",
    "NumberFormat",
    "NumericSeries",
    "PriceKind",
    "OutputRef",
    "Rate",
    "ReturnKind",
    "Unit",
    "UnsupportedScope",
    "Violation",
    "VisualizationSpec",
]
