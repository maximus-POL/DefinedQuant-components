"""Transport-neutral host service for Defined Quant operations."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Any

from defined_quant.catalog import ComponentRecord
from defined_quant.data_records import (
    DatasetOperationSourceV1,
    DatasetRegistrationRequest,
    OperationBindingV1,
)
from defined_quant.dataset_registry import (
    ConfiguredFileRoots,
    normalize_dataset,
    resolve_dataset_fields,
)
from defined_quant.discovery import (
    DEFAULT_LIMIT,
    ContractIndex,
    DiscoveryFilters,
    SearchResults,
)
from defined_quant.host_failures import (
    HostFailureCode,
    HostFailureException,
    map_operation_failure,
)
from defined_quant.operation_records import (
    build_operation_record,
    reconciled_bundle_bytes,
)
from defined_quant.operation_runtime import execute_operation
from defined_quant.record_views import describe_dataset, get_operation, operation_summary
from defined_quant.session_cas import SessionCas, StoredOperation
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    InterpretationMethod,
    OperationFailure,
    OperationRequest,
    OperationResult,
    OperationSuccess,
    SourceKind,
    SvgArtifactRequest,
)
from pydantic import ValidationError


class DefinedQuantService:
    """Transport-neutral host API for indexed discovery and canonical execution."""

    __slots__ = (
        "_catalog_root",
        "_configured_roots",
        "_contract_index",
        "_contract_index_lock",
        "_session_cas",
        "_session_lock",
        "_session_state_root",
    )

    def __init__(
        self,
        *,
        catalog_root: Path | None = None,
        contract_index: ContractIndex | None = None,
        data_roots: Sequence[Path] = (),
        session_state_root: Path | None = None,
        session_cas: SessionCas | None = None,
    ) -> None:
        if catalog_root is not None and contract_index is not None:
            raise ValueError("catalog_root cannot be combined with contract_index")
        self._catalog_root = catalog_root
        self._contract_index = contract_index
        self._contract_index_lock = Lock()
        self._configured_roots = ConfiguredFileRoots(data_roots)
        self._session_state_root = (
            session_state_root
            if session_state_root is not None
            else Path(tempfile.gettempdir()).resolve()
            / f"defined-quant-local-mcp-{getattr(os, 'getuid', lambda: 0)()}"
        )
        self._session_cas = session_cas
        self._session_lock = Lock()

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

    @property
    def session_cas(self) -> SessionCas:
        """Return the lazy ephemeral CAS without affecting Phase-1/2-only callers."""

        cas = self._session_cas
        if cas is not None:
            return cas
        with self._session_lock:
            cas = self._session_cas
            if cas is None:
                cas = SessionCas(self._session_state_root)
                self._session_cas = cas
            return cas

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

    def register_dataset(
        self,
        request: DatasetRegistrationRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize, publish, and return bounded registration metadata."""

        normalized = normalize_dataset(
            request,
            configured_roots=self._configured_roots,
        )
        reference = self.session_cas.publish_dataset(
            normalized.payload,
            normalized.record,
        )
        return describe_dataset(self.session_cas, reference)

    def describe_dataset(
        self,
        reference: str,
        *,
        view: str = "metadata",
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return a bounded verified dataset projection."""

        return describe_dataset(
            self.session_cas,
            reference,
            view=view,
            cursor=cursor,
            limit=limit,
        )

    def execute_recorded_operation(
        self,
        component: ComponentRef | Mapping[str, Any],
        *,
        output_dir: Path,
        literals: Mapping[str, Any] | None = None,
        sources: Sequence[Mapping[str, Any]] = (),
        provenance: CallerProvenance | Mapping[str, Any] | None = None,
        artifacts: str = "none",
    ) -> dict[str, Any]:
        """Resolve one source, run Phase-1 execution, and publish its receipt record."""

        if len(sources) > 1:
            raise HostFailureException(HostFailureCode.MULTIPLE_NON_LITERAL_SOURCES)
        if artifacts not in {"none", "svg_all"}:
            raise HostFailureException(
                HostFailureCode.INVALID_TOOL_REQUEST,
                details={"fields": ["artifacts"]},
            )
        try:
            component_ref = (
                component
                if isinstance(component, ComponentRef)
                else ComponentRef.model_validate(component)
            )
            if len(component_ref.id.encode("ascii")) > 240:
                raise HostFailureException(
                    HostFailureCode.INVALID_TOOL_REQUEST,
                    details={"fields": ["component"]},
                )
            binding_value: dict[str, Any] = {"literals": dict(literals or {})}
            stored_dataset = None
            if sources:
                if provenance is not None:
                    raise HostFailureException(
                        HostFailureCode.INVALID_TOOL_REQUEST,
                        details={"fields": ["provenance"]},
                    )
                source_value = sources[0]
                if source_value.get("kind") == "operation":
                    raise HostFailureException(HostFailureCode.UNSUPPORTED_BINDING)
                source = DatasetOperationSourceV1.model_validate(source_value)
                stored_dataset = self.session_cas.load_dataset(source.ref)
                binding_value["source"] = source.model_dump(mode="json")
                binding = OperationBindingV1.model_validate(binding_value)
                resolved_input = resolve_dataset_fields(
                    stored_dataset.payload,
                    source.mappings,
                    literals=binding.literals,
                )
                operation_provenance = CallerProvenance.model_validate(
                    stored_dataset.record.source.provenance.model_dump(mode="json")
                )
            else:
                if provenance is None:
                    raise HostFailureException(
                        HostFailureCode.INVALID_TOOL_REQUEST,
                        details={"fields": ["provenance"]},
                    )
                binding = OperationBindingV1.model_validate(binding_value)
                resolved_input = dict(binding.literals)
                operation_provenance = (
                    provenance
                    if isinstance(provenance, CallerProvenance)
                    else CallerProvenance.model_validate(provenance)
                )
            operation_request = OperationRequest(
                component=component_ref,
                input=resolved_input,
                provenance=operation_provenance,
                artifacts=(SvgArtifactRequest() if artifacts == "svg_all" else None),
            )
        except HostFailureException:
            raise
        except ValidationError as exc:
            raise HostFailureException(
                HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []}
            ) from exc
        except ValueError as exc:
            raise HostFailureException(
                HostFailureCode.INVALID_FIELD_MAPPING, details={"fields": []}
            ) from exc

        outcome = self.execute_operation(operation_request, output_dir=output_dir)
        if isinstance(outcome, OperationFailure):
            mapped = map_operation_failure(
                outcome,
                validated_request=operation_request,
                component_input_fields=self._component_input_fields(component_ref),
            )
            raise HostFailureException(
                mapped.error.code,
                details=mapped.error.details,
            )
        assert isinstance(outcome, OperationSuccess)
        record = build_operation_record(
            outcome.manifest,
            output_dir,
            binding,
            dataset_record=(None if stored_dataset is None else stored_dataset.record),
            dataset_payload=(None if stored_dataset is None else stored_dataset.payload),
        )
        manifest_bytes, members = reconciled_bundle_bytes(
            record,
            output_dir,
            dataset_record=(None if stored_dataset is None else stored_dataset.record),
            dataset_payload=(None if stored_dataset is None else stored_dataset.payload),
        )
        candidate = StoredOperation(
            reference=record.reference,
            record=record,
            manifest=outcome.manifest,
            manifest_bytes=manifest_bytes,
            members=MappingProxyType(dict(members)),
        )
        # The complete response limit is part of the publication transaction: an operation whose
        # required summary cannot be returned must not become a live session reference.
        summary = operation_summary(
            self.session_cas,
            candidate,
            cursor_encoder=self.session_cas._encode_pending_cursor,
        )
        reference = self.session_cas.publish_operation(
            record,
            manifest_bytes=manifest_bytes,
            members=members,
        )
        assert reference == record.reference
        return summary

    def _component_input_fields(self, component: ComponentRef) -> tuple[str, ...]:
        try:
            from defined_quant.operation_runtime import (
                component_execution_models,
                component_record_for_request,
            )

            probe = OperationRequest(
                component=component,
                input={},
                provenance=CallerProvenance(
                    source_kind=SourceKind.SYNTHETIC,
                    interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
                    label="Host failure field projection only.",
                ),
            )
            record = component_record_for_request(probe, catalog_root=self._catalog_root)
            input_model, _ = component_execution_models(record, component=component)
            return tuple(input_model.model_fields)
        except Exception:
            return ()

    def get_operation(
        self,
        reference: str,
        *,
        view: str = "summary",
        field: str | None = None,
        artifact_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return one bounded verified operation projection."""

        return get_operation(
            self.session_cas,
            reference,
            view=view,
            field=field,
            artifact_id=artifact_id,
            cursor=cursor,
            limit=limit,
        )

    def close(self) -> None:
        """Expire and remove this service's Phase-3 session state, if opened."""

        with self._session_lock:
            cas = self._session_cas
            self._session_cas = None
        if cas is not None:
            cas.close()
        self._configured_roots.close()

    def __enter__(self) -> DefinedQuantService:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


__all__ = ["DefinedQuantService"]
