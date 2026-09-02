"""Focused tests for the bounded methods-first record session."""

from __future__ import annotations

from datetime import timedelta
from threading import Thread

import pytest
from defined_quant.method_records import (
    MAX_METHOD_SESSION_BYTES,
    MAX_METHOD_SESSION_RECORDS,
    MethodRecordLimits,
    MethodRecordScopeRegistry,
    MethodRecordStore,
    MethodRecordStoreError,
    MethodRecordStoreErrorCode,
)
from defined_quant_protocol.canonical import canonical_json_bytes
from defined_quant_protocol.execution import (
    AdapterExecutionRequest,
    AdapterExecutionSuccess,
    CanonicalValidation,
    PlanExecutionRequest,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    ValidationStatus,
)
from defined_quant_protocol.resolution import PlanRecord

from authoring.tests.test_methods_first_protocol import (
    _NOW,
    _compiled_records,
    _runtime,
)


def _records() -> tuple[PlanRecord, StepRecord, RunRecord]:
    capability, method, _, availability, _, plan, plan_record = _compiled_records()
    execution_request = PlanExecutionRequest(
        plan=plan.ref,
        plan_record_hash=plan_record.record_hash,
        requester=_runtime(),
        requested_at=_NOW,
    )
    adapter_request = AdapterExecutionRequest(
        plan=plan.ref,
        execution_request_hash=execution_request.request_hash,
        step_id="calculate",
        capability=capability.ref,
        implementation=plan.steps[0].implementation,
        adapter=plan.steps[0].adapter,
        backend_bindings=plan.steps[0].backend_bindings,
        transport=plan.steps[0].transport,
        locality=plan.steps[0].locality,
        execution_availability=availability,
        canonical_inputs={"prices": [100.0, 105.0]},
    )
    adapter_result = AdapterExecutionSuccess(
        request_hash=adapter_request.request_hash,
        adapter_runtime=_runtime(),
        canonical_output={"returns": [0.05]},
    )
    capability_validation = CanonicalValidation(
        status=ValidationStatus.PASSED,
        schema_hash=capability.contract_hash,
        validator=_runtime(),
    )
    step = StepRecord(
        status=StepStatus.SUCCEEDED,
        plan=plan.ref,
        execution_request_hash=execution_request.request_hash,
        method=method.ref,
        compiled_step=plan.steps[0],
        canonical_inputs={"prices": [100.0, 105.0]},
        execution_availability=availability,
        input_validation=capability_validation,
        adapter_request=adapter_request,
        adapter_result=adapter_result,
        output_validation=capability_validation,
        canonical_output={"returns": [0.05]},
        executor=_runtime(),
        started_at=_NOW,
        finished_at=_NOW,
    )
    run = RunRecord(
        status=RunStatus.SUCCEEDED,
        plan_record=plan_record,
        execution_request=execution_request,
        steps=(step,),
        method_input_validation=CanonicalValidation(
            status=ValidationStatus.PASSED,
            schema_hash=method.contract_hash,
            validator=_runtime(),
        ),
        method_output_validation=CanonicalValidation(
            status=ValidationStatus.PASSED,
            schema_hash=method.contract_hash,
            validator=_runtime(),
        ),
        canonical_method_output={"returns": [0.05]},
        executor=_runtime(),
        started_at=_NOW,
        finished_at=_NOW,
    )
    return plan_record, step, run


def _assert_code(
    error: pytest.ExceptionInfo[MethodRecordStoreError],
    expected: MethodRecordStoreErrorCode,
) -> None:
    assert error.value.code is expected
    assert str(error.value) == expected.value


def test_plan_step_and_run_publish_with_exact_canonical_bytes() -> None:
    plan, step, run = _records()
    store = MethodRecordStore()

    plan_reference = store.publish_plan(plan)
    first_size = store.used_bytes
    assert plan_reference == plan.ref.reference
    assert ":v" not in plan_reference
    assert store.publish_plan(plan) == plan_reference
    assert store.used_bytes == first_size
    assert store.record_count == 1
    assert store.get_plan(plan.ref) == plan
    assert store.record_bytes(plan_reference) == canonical_json_bytes(
        plan.model_dump(mode="json", exclude_computed_fields=True)
    )

    step_reference = store.publish_step(step)
    run_reference = store.publish_run(run)
    assert step_reference == step.ref.reference
    assert run_reference == run.ref.reference
    assert store.get_step(step_reference) == step
    assert store.get_run(run.ref) == run
    assert store.record_count == 3
    assert store.record_bytes(step_reference) == canonical_json_bytes(
        step.model_dump(mode="json", exclude_computed_fields=True)
    )
    assert store.record_bytes(run_reference) == canonical_json_bytes(
        run.model_dump(mode="json", exclude_computed_fields=True)
    )


def test_same_plan_reference_cannot_be_overwritten_by_different_record_bytes() -> None:
    plan, _, _ = _records()
    changed = plan.model_copy(update={"compiled_at": _NOW + timedelta(seconds=1)})
    assert changed.ref == plan.ref
    assert changed.record_hash != plan.record_hash
    store = MethodRecordStore()
    store.publish_plan(plan)

    with pytest.raises(MethodRecordStoreError) as conflict:
        store.publish_plan(changed)

    _assert_code(conflict, MethodRecordStoreErrorCode.PUBLICATION_CONFLICT)
    assert store.get_plan(plan.ref) == plan


def test_run_requires_its_exact_plan_and_every_exact_step() -> None:
    plan, step, run = _records()
    store = MethodRecordStore()

    with pytest.raises(MethodRecordStoreError) as missing_plan:
        store.publish_step(step)
    _assert_code(missing_plan, MethodRecordStoreErrorCode.DEPENDENCY_MISSING)

    store.publish_plan(plan)
    with pytest.raises(MethodRecordStoreError) as missing_step:
        store.publish_run(run)
    _assert_code(missing_step, MethodRecordStoreErrorCode.DEPENDENCY_MISSING)

    store.publish_step(step)
    assert store.publish_run(run) == run.ref.reference


def test_active_references_are_session_scoped_and_expire_on_close() -> None:
    plan, _, _ = _records()
    scopes = MethodRecordScopeRegistry()
    first = MethodRecordStore(scope_registry=scopes)
    second = MethodRecordStore(scope_registry=scopes)
    first.publish_plan(plan)

    with pytest.raises(MethodRecordStoreError) as foreign:
        second.get_plan(plan.ref)
    _assert_code(foreign, MethodRecordStoreErrorCode.REFERENCE_SCOPE_DENIED)

    first.close()
    assert first.used_bytes == 0
    assert first.record_count == 0
    with pytest.raises(MethodRecordStoreError) as expired:
        second.get_plan(plan.ref)
    _assert_code(expired, MethodRecordStoreErrorCode.REFERENCE_NOT_FOUND)
    with pytest.raises(MethodRecordStoreError) as closed:
        first.get_plan(plan.ref)
    _assert_code(closed, MethodRecordStoreErrorCode.CLOSED)
    first.close()


def test_limits_are_applied_to_exact_protocol_bytes() -> None:
    plan, step, _ = _records()
    encoded = canonical_json_bytes(plan.model_dump(mode="json", exclude_computed_fields=True))
    too_small = MethodRecordStore(
        limits=MethodRecordLimits(
            record_bytes=len(encoded) - 1,
            session_bytes=MAX_METHOD_SESSION_BYTES,
            records=MAX_METHOD_SESSION_RECORDS,
        )
    )
    with pytest.raises(MethodRecordStoreError) as oversized:
        too_small.publish_plan(plan)
    _assert_code(oversized, MethodRecordStoreErrorCode.RECORD_TOO_LARGE)

    one_record = MethodRecordStore(
        limits=MethodRecordLimits(
            records=1,
        )
    )
    one_record.publish_plan(plan)
    with pytest.raises(MethodRecordStoreError) as count:
        one_record.publish_step(step)
    _assert_code(count, MethodRecordStoreErrorCode.RECORD_COUNT_EXCEEDED)


def test_secret_bearing_adapter_output_is_refused_before_publication() -> None:
    plan, step, _ = _records()
    assert isinstance(step.adapter_result, AdapterExecutionSuccess)
    secret_output = {"returns": [0.05], "api_key": "do-not-store-this"}
    adapter_result = step.adapter_result.model_copy(
        update={"canonical_output": secret_output}
    )
    secret_step = step.model_copy(
        update={
            "adapter_result": adapter_result,
            "canonical_output": secret_output,
        }
    )
    store = MethodRecordStore()
    store.publish_plan(plan)

    with pytest.raises(MethodRecordStoreError) as sensitive:
        store.publish_step(secret_step)

    _assert_code(sensitive, MethodRecordStoreErrorCode.SENSITIVE_MATERIAL)
    assert store.record_count == 1


def test_concurrent_identical_publication_is_idempotent() -> None:
    plan, _, _ = _records()
    store = MethodRecordStore()
    references: list[str] = []

    def publish() -> None:
        references.append(store.publish_plan(plan))

    workers = [Thread(target=publish) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert references == [plan.ref.reference] * 8
    assert store.record_count == 1
