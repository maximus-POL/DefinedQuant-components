"""Transport-neutral service surface for the canonical methods-first runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from defined_quant.execution import ExactAdapterCatalog
from defined_quant.execution import execute_plan as execute_compiled_plan
from defined_quant.method_records import (
    MethodRecordStore,
    MethodRecordStoreError,
    MethodRecordStoreErrorCode,
)
from defined_quant.planning import compile_plan as compile_method_plan
from defined_quant.registry import (
    MethodFilters,
    MethodInspection,
    MethodSearchResults,
    Registry,
    inspect_method,
    load_registry,
    search_methods,
)
from defined_quant_protocol.execution import RunRecord, RunRef
from defined_quant_protocol.registry import RuntimeIdentity
from defined_quant_protocol.resolution import (
    AvailabilitySnapshot,
    CompilationOutcome,
    CompiledPlan,
    OriginReceiptSet,
    PlanProposal,
    PlanRecord,
    PlanRef,
    ResolutionConstraintSet,
    ResolutionPolicy,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MethodsRuntime:
    """One explicit policy, availability, trust, and record session.

    Policy and availability are constructor requirements so the service cannot invent provider or
    implementation preferences.  The trusted adapter catalog is a separate host-owned boundary;
    installing an entry point alone never adds it to this runtime.
    """

    def __init__(
        self,
        *,
        registry: Registry | None = None,
        policy: ResolutionPolicy,
        availability: AvailabilitySnapshot,
        adapter_catalog: ExactAdapterCatalog,
        runtime_identity: RuntimeIdentity,
        record_store: MethodRecordStore | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._registry = registry or load_registry()
        self._protocol_registry = self._registry.as_protocol_registry()
        self._policy = policy
        self._availability = availability
        self._adapter_catalog = adapter_catalog
        self._runtime_identity = runtime_identity
        self._record_store = record_store or MethodRecordStore()
        self._clock = clock
        self._lock = RLock()

    @property
    def registry(self) -> Registry:
        return self._registry

    def search_methods(
        self,
        query: str,
        *,
        filters: MethodFilters = MethodFilters(),
        limit: int = 20,
    ) -> MethodSearchResults:
        """Search inert Method registry metadata without importing adapter code."""

        return search_methods(
            query,
            filters=filters,
            limit=limit,
            registry=self._registry,
        )

    def inspect_method(
        self,
        method_id: str,
        *,
        version: str | None = None,
    ) -> MethodInspection:
        """Inspect a Method and its registry joins without probing installation state."""

        return inspect_method(
            method_id,
            version=version,
            registry=self._registry,
        )

    def compile_plan(
        self,
        proposal: PlanProposal | Mapping[str, Any],
        *,
        origin_receipts: OriginReceiptSet | None = None,
        host_constraints: ResolutionConstraintSet = ResolutionConstraintSet(),
    ) -> CompilationOutcome:
        """Compile and retain one plan; no adapter is loaded or executed."""

        validated = (
            proposal
            if isinstance(proposal, PlanProposal)
            else PlanProposal.model_validate(proposal)
        )
        with self._lock:
            outcome = compile_method_plan(
                validated,
                registry=self._protocol_registry,
                policy=self._policy,
                availability=self._availability,
                origin_receipts=origin_receipts,
                expected_origin_session_binding=self._record_store.origin_binding_hash,
                trusted_origin_issuers=(self._runtime_identity,),
                host_constraints=host_constraints,
            )
            if isinstance(outcome, CompiledPlan):
                try:
                    self._record_store.get_plan(outcome.ref)
                except MethodRecordStoreError as exc:
                    if exc.code != MethodRecordStoreErrorCode.REFERENCE_NOT_FOUND:
                        raise
                    self._record_store.publish_plan(
                        PlanRecord(
                            plan=outcome,
                            compiler=self._runtime_identity,
                            compiled_at=self._clock(),
                        )
                    )
            return outcome

    def execute_plan(self, reference: str | PlanRef) -> RunRecord:
        """Execute only the exact retained plan under current bound host state."""

        with self._lock:
            plan_record = self._record_store.get_plan(reference)
            return execute_compiled_plan(
                plan_record,
                registry=self._protocol_registry,
                policy=self._policy,
                availability=self._availability,
                adapter_catalog=self._adapter_catalog,
                record_store=self._record_store,
                executor=self._runtime_identity,
                clock=self._clock,
            )

    def get_plan(self, reference: str | PlanRef) -> PlanRecord:
        """Retrieve one immutable session-scoped compiled-plan record."""

        return self._record_store.get_plan(reference)

    def get_run(self, reference: str | RunRef) -> RunRecord:
        """Retrieve one complete immutable session-scoped run record."""

        return self._record_store.get_run(reference)

    def close(self) -> None:
        self._record_store.close()

    def __enter__(self) -> MethodsRuntime:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


__all__ = ["MethodsRuntime"]
