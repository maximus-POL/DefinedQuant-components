"""Transport-neutral host service for Defined Quant operations."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from defined_quant.catalog import ComponentRecord
from defined_quant.discovery import (
    DEFAULT_LIMIT,
    ContractIndex,
    DiscoveryFilters,
    SearchResults,
)
from defined_quant.operation_runtime import execute_operation
from defined_quant_protocol import OperationRequest, OperationResult


class DefinedQuantService:
    """Transport-neutral host API for indexed discovery and canonical execution."""

    __slots__ = ("_catalog_root", "_contract_index", "_contract_index_lock")

    def __init__(
        self,
        *,
        catalog_root: Path | None = None,
        contract_index: ContractIndex | None = None,
    ) -> None:
        if catalog_root is not None and contract_index is not None:
            raise ValueError("catalog_root cannot be combined with contract_index")
        self._catalog_root = catalog_root
        self._contract_index = contract_index
        self._contract_index_lock = Lock()

    @property
    def catalog_root(self) -> Path | None:
        """Return the configured catalog boundary without resolving request data."""

        return self._catalog_root

    @property
    def contract_index(self) -> ContractIndex:
        """Return this service's immutable process-lifetime discovery snapshot."""

        index = self._contract_index
        if index is not None:
            return index
        with self._contract_index_lock:
            index = self._contract_index
            if index is None:
                index = ContractIndex.from_catalog(root=self._catalog_root)
                self._contract_index = index
            return index

    def component_record(self, component_id: str) -> ComponentRecord:
        """Resolve one stable ID from the discovery snapshot without importing it."""

        return self.contract_index.get(component_id)

    def search_components(
        self,
        query: str,
        *,
        filters: DiscoveryFilters | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> SearchResults:
        """Search the immutable contract snapshot without importing component code."""

        return self.contract_index.search(query, filters=filters, limit=limit)

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
