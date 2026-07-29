"""Closed vocabularies for financial units and conventions.

These enums are intentionally small.  Adding a value is a contract change because component
JSON Schemas and subject hashes include these definitions.
"""

from __future__ import annotations

from enum import StrEnum


class Unit(StrEnum):
    """Units that may be attached to component and visualization outputs."""

    DECIMAL = "decimal"
    PERCENT = "percent"
    PRICE = "price"
    CURRENCY = "currency"
    YEARS = "years"
    DAYS = "days"
    COUNT = "count"
    VOLATILITY = "volatility"
    UNITLESS = "unitless"


class Compounding(StrEnum):
    """Supported rate-compounding conventions."""

    SIMPLE = "simple"
    DISCRETE = "discrete"
    CONTINUOUS = "continuous"


class Frequency(StrEnum):
    """Declared observation frequencies.

    ``IRREGULAR`` is an explicit declaration, not an inferred fallback.
    """

    INTRADAY = "intraday"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    IRREGULAR = "irregular"


class ReturnKind(StrEnum):
    """Return convention."""

    SIMPLE = "simple"
    LOG = "log"


class PriceKind(StrEnum):
    """Whether a price series includes a vendor's corporate-action adjustments."""

    ADJUSTED = "adjusted"
    UNADJUSTED = "unadjusted"


__all__ = [
    "Compounding",
    "Frequency",
    "PriceKind",
    "ReturnKind",
    "Unit",
]
