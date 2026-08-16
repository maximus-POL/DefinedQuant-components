"""Transport-neutral host service for Defined Quant operations."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
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
from defined_quant.local_host_platform import local_host_platform
from defined_quant.operation_records import (
    build_operation_record,
    reconciled_bundle_bytes,
)
from defined_quant.record_views import describe_dataset, get_operation, operation_summary
from defined_quant.session_cas import SessionCas, StoredOperation
from defined_quant.worker_runtime import WorkerController
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    OperationFailure,
    OperationRequest,
    OperationResult,
    OperationSuccess,
    SvgArtifactRequest,
)
from pydantic import ValidationError


@dataclass(frozen=True, slots=True)
class OperationArtifact:
    """One service-verified artifact payload without transport-specific encoding."""

    content: bytes
    media_type: str
    sha256: str


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
        "_worker_controller",
    )

    def __init__(
        self,
        *,
        catalog_root: Path | None = None,
        contract_index: ContractIndex | None = None,
        data_roots: Sequence[Path] = (),
        session_state_root: Path | None = None,
        session_cas: SessionCas | None = None,
        worker_controller: WorkerController | None = None,
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
            / f"defined-quant-local-mcp-{local_host_platform().state_namespace}"
        )
        self._session_cas = session_cas
        self._session_lock = Lock()
        self._worker_controller = worker_controller or WorkerController()

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

    def inspect_component(
        self,
        component_id: str,
        *,
        expected_version: str | None = None,
        expected_subject_hash: str | None = None,
        view: str = "compact",
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        """Inspect one selected installed subject through the bounded worker."""

        arguments: dict[str, object] = {
            "component_id": component_id,
            "view": view,
        }
        if expected_version is not None:
            arguments["expected_version"] = expected_version
        if expected_subject_hash is not None:
            arguments["expected_subject_hash"] = expected_subject_hash
        return self._worker_controller.inspect_component(
            arguments,
            catalog_root=self._catalog_root,
            cancel_event=cancel_event,
        )

    def compare_ports(
        self,
        producer: Mapping[str, object],
        consumer: Mapping[str, object],
        *,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        """Compare two selected component fields through the bounded worker."""

        return self._worker_controller.compare_ports(
            {"producer": dict(producer), "consumer": dict(consumer)},
            catalog_root=self._catalog_root,
            cancel_event=cancel_event,
        )

    def execute_operation(
        self,
        request: OperationRequest,
        *,
        output_dir: Path,
    ) -> OperationResult:
        """Execute one exact host request through the bounded worker boundary."""

        return self._worker_controller.execute_operation(
            request,
            output_dir=output_dir,
            catalog_root=self._catalog_root,
        ).result

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
        cancel_event: Event | None = None,
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
                source_reference = source_value.get("ref")
                if isinstance(source_reference, str):
                    reference_match = re.fullmatch(
                        r"dqds:v([0-9]{1,3}):[0-9a-f]{64}",
                        source_reference,
                    )
                    if reference_match is not None and reference_match.group(1) != "1":
                        raise HostFailureException(
                            HostFailureCode.UNSUPPORTED_REFERENCE_VERSION,
                            details={
                                "requested_version": f"v{reference_match.group(1)}"
                            },
                        )
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

        execution = self._worker_controller.execute_operation(
            operation_request,
            output_dir=output_dir,
            catalog_root=self._catalog_root,
            cancel_event=cancel_event,
        )
        outcome = execution.result
        if isinstance(outcome, OperationFailure):
            mapped = map_operation_failure(
                outcome,
                validated_request=operation_request,
                component_input_fields=execution.component_input_fields,
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

    def execute_component(
        self,
        component: ComponentRef | Mapping[str, Any],
        *,
        literals: Mapping[str, Any] | None = None,
        sources: Sequence[Mapping[str, Any]] = (),
        provenance: CallerProvenance | Mapping[str, Any] | None = None,
        artifacts: str = "none",
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Execute and publish without exposing a transport-owned filesystem location."""

        with self.session_cas.temporary_operation_output() as output_dir:
            return self.execute_recorded_operation(
                component,
                output_dir=output_dir,
                literals=literals,
                sources=sources,
                provenance=provenance,
                artifacts=artifacts,
                cancel_event=cancel_event,
            )

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

    def read_operation_artifact(
        self,
        reference: str,
        artifact_id: str,
    ) -> OperationArtifact:
        """Return one declared artifact only after full record and digest reconciliation."""

        stored = self.session_cas.load_operation(reference)
        artifact = next(
            (
                candidate
                for candidate in stored.manifest.artifacts
                if candidate.visualization_id == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise HostFailureException(
                HostFailureCode.ARTIFACT_NOT_FOUND,
                details={"artifact_id": artifact_id},
            )
        content = stored.members.get(artifact.path)
        if content is None:
            raise HostFailureException(HostFailureCode.RECORD_CORRUPT)
        return OperationArtifact(
            content=content,
            media_type=artifact.media_type,
            sha256=artifact.sha256,
        )

    def close(self) -> None:
        """Expire and remove this service's Phase-3 session state, if opened."""

        self._worker_controller.close()
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


__all__ = ["DefinedQuantService", "OperationArtifact"]
