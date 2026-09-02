"""DQ-native realization of monthly return calendar alignment."""

from .adapter import execute
from .kernel import align_monthly_returns

__all__ = ["align_monthly_returns", "execute"]
