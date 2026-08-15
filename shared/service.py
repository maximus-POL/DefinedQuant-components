"""Transport-neutral host service for Defined Quant operations."""

from __future__ import annotations

from pathlib import Path

from defined_quant.operation_runtime import execute_operation
from defined_quant_protocol import OperationRequest, OperationResult


class DefinedQuantService:
    """Phase-1 host API for canonical unmanaged component execution."""

    __slots__ = ("_catalog_root",)

    def __init__(self, *, catalog_root: Path | None = None) -> None:
        self._catalog_root = catalog_root

    @property
    def catalog_root(self) -> Path | None:
        """Return the configured catalog boundary without resolving request data."""

        return self._catalog_root

    def execute_operation(
        self,
        request: OperationRequest,
        *,
        output_dir: Path,
    ) -> OperationResult:
        """Execute one exact request through the shared canonical runtime."""

        return execute_operation(
            request,
            output_dir=output_dir,
            catalog_root=self._catalog_root,
        )


__all__ = ["DefinedQuantService"]
