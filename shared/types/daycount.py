"""Canonical day-count convention identifiers."""

from __future__ import annotations

from enum import StrEnum


class DayCountConvention(StrEnum):
    """Initial, fully specified day-count conventions.

    A generic ``30/360`` value is deliberately absent because its variants differ materially.
    """

    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"


__all__ = ["DayCountConvention"]
