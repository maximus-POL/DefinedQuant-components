"""Byte ceilings shared by the worker controller and import-safe child entry point."""

from typing import Final

MAX_WORKER_REQUEST_BYTES: Final = 2 * 1024 * 1024
MAX_WORKER_CONTROL_BYTES: Final = 4 * 1024 * 1024

__all__ = ["MAX_WORKER_CONTROL_BYTES", "MAX_WORKER_REQUEST_BYTES"]
