"""DQ-native realization of the log-return capability."""

from .adapter import execute
from .kernel import calculate_log_returns

__all__ = ["calculate_log_returns", "execute"]
