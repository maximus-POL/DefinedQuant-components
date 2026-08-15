"""Phase-3 service integration and bounded record-view tests."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from defined_quant.data_records import (
    DatasetRegistrationRequest,
    cas_json_bytes,
    pretty_json_bytes,
)
from defined_quant.host_failures import (
    HostFailureException,
    TrustLabel,
    tool_success_result_projection,
)
from defined_quant.operation_runtime import execute_operation as execute_phase1_operation
from defined_quant.service import DefinedQuantService
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    FileDigest,
    OperationRequest,
    OperationSuccess,
)

ROOT = Path(__file__).resolve().parents[2]
EVALUATIONS = ROOT / "docs" / "local_mcp" / "evaluation_cases.v1.json"


def _daily_request(*, count: int = 55) -> dict[str, Any]:
    return {
        "source": {
            "kind": "inline_rows",
            "rows": [
                {
                    "timestamp": (
                        datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "price": float(100 + index),
                }
                for index in range(count)
            ],
        },
        "columns": [
            {
                "source_name": "timestamp",
                "field_id": "timestamp",
                "data_type": "timestamp",
                "role": "timestamp",
            },
            {
                "source_name": "price",
                "field_id": "price",
                "data_type": "number",
                "role": "value",
            },
        ],
        "semantics": {
            "frequency": "daily",
            "timezone": "UTC",
            "ordering": "preserve_source_order",
            "price_kind": "adjusted",
        },
        "provenance": {
            "source_kind": "synthetic",
            "interpretation_method": "caller_structured",
            "label": "Synthetic daily prices for Phase-3 service tests.",
        },
        "external_preprocessing": {"status": "none_declared"},
    }


def _evaluation_component() -> dict[str, Any]:
    value = json.loads(EVALUATIONS.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value["cases"][0]["fixture"]["component"]


def _execute_with_result_updates(
    service: DefinedQuantService,
    request: OperationRequest,
    *,
    output_dir: Path,
    updates: dict[str, Any],
) -> OperationSuccess:
    outcome = execute_phase1_operation(
        request,
        output_dir=output_dir,
        catalog_root=service.catalog_root,
    )
    assert isinstance(outcome, OperationSuccess)
    result_path = output_dir / outcome.manifest.result.path
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    result.update(updates)
    result_bytes = pretty_json_bytes(result)
    result_path.write_bytes(result_bytes)
    manifest = outcome.manifest.model_copy(
        update={
            "result": FileDigest(
                path=outcome.manifest.result.path,
                sha256=hashlib.sha256(result_bytes).hexdigest(),
            )
        }
    )
    (output_dir / "manifest.json").write_bytes(
        pretty_json_bytes(manifest.model_dump(mode="json"))
    )
    return OperationSuccess(manifest=manifest)


def _tool_result_bytes(data: dict[str, Any], *, trust: TrustLabel) -> bytes:
    return cas_json_bytes(tool_success_result_projection(data, trust=trust))[:-1]


def test_service_session_is_lazy_and_dataset_preview_cursor_is_limit_independent(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    service = DefinedQuantService(session_state_root=state_root)
    assert not state_root.exists()

    registered = service.register_dataset(_daily_request())
    reference = registered["dataset_ref"]
    assert registered["compact"] is True
    assert registered["row_count"] == 55
    assert state_root.exists()
    first = service.describe_dataset(reference, view="preview", limit=20)
    assert first["compact"] is False
    assert first["preview"]["returned"] == 20
    assert first["preview"]["rows"][0] == {
        "timestamp": "2024-01-01T00:00:00Z",
        "price": 100.0,
    }
    cursor = first["preview"]["next_cursor"]
    assert isinstance(cursor, str)
    second = service.describe_dataset(
        reference,
        view="preview",
        cursor=cursor,
        limit=35,
    )
    assert second["preview"]["start"] == 20
    assert second["preview"]["returned"] == 35
    assert second["preview"]["complete"] is True
    assert second["preview"]["next_cursor"] is None
    assert len(_tool_result_bytes(first, trust=TrustLabel.UNVERIFIED_CALLER_DATA)) <= 256 * 1024
    assert len(_tool_result_bytes(second, trust=TrustLabel.UNVERIFIED_CALLER_DATA)) <= 256 * 1024

    session_directory = service.session_cas.directory
    service.close()
    assert not session_directory.exists()
    service.close()


def test_model_and_mapping_service_entry_points_return_identical_references(
    tmp_path: Path,
) -> None:
    service = DefinedQuantService(session_state_root=tmp_path / "state")
    dataset_payload = _daily_request(count=2)
    typed_dataset = DatasetRegistrationRequest.model_validate(dataset_payload)

    mapping_dataset = service.register_dataset(dataset_payload)
    model_dataset = service.register_dataset(typed_dataset)

    assert model_dataset["dataset_ref"] == mapping_dataset["dataset_ref"]

    component_payload = _evaluation_component()
    typed_component = ComponentRef.model_validate(component_payload)
    provenance_payload = {
        "source_kind": "synthetic",
        "interpretation_method": "caller_structured",
        "label": "Model-or-mapping service parity.",
    }
    typed_provenance = CallerProvenance.model_validate(provenance_payload)
    literals = {"prices": [100.0, 101.0], "price_kind": "adjusted"}

    mapping_operation = service.execute_recorded_operation(
        component_payload,
        output_dir=tmp_path / "mapping-operation",
        literals=literals,
        provenance=provenance_payload,
    )
    model_operation = service.execute_recorded_operation(
        typed_component,
        output_dir=tmp_path / "model-operation",
        literals=literals,
        provenance=typed_provenance,
    )

    assert model_operation["operation_ref"] == mapping_operation["operation_ref"]
    service.close()


def test_preview_pages_reserve_the_complete_success_result_envelope(tmp_path: Path) -> None:
    service = DefinedQuantService(session_state_root=tmp_path / "state")
    registration = service.register_dataset(
        {
            "source": {
                "kind": "inline_rows",
                "rows": [{"label": "x" * 14_500} for _ in range(50)],
            },
            "columns": [
                {
                    "source_name": "label",
                    "field_id": "label",
                    "data_type": "string",
                    "role": "label",
                }
            ],
            "semantics": {"ordering": "preserve_source_order"},
            "provenance": {
                "source_kind": "synthetic",
                "interpretation_method": "caller_structured",
                "label": "Complete tool-result byte-bound regression.",
            },
        }
    )

    page = service.describe_dataset(
        registration["dataset_ref"], view="preview", limit=50
    )
    complete_result = _tool_result_bytes(
        page, trust=TrustLabel.UNVERIFIED_CALLER_DATA
    )

    assert 0 < page["preview"]["returned"] < 50
    assert page["preview"]["complete"] is False
    assert len(complete_result) <= 256 * 1024
    service.close()


def test_cursor_refuses_tampering_and_cross_session_reuse(tmp_path: Path) -> None:
    first_service = DefinedQuantService(session_state_root=tmp_path / "state")
    second_service = DefinedQuantService(session_state_root=tmp_path / "state")
    reference = first_service.register_dataset(_daily_request())["dataset_ref"]
    first_page = first_service.describe_dataset(reference, view="preview", limit=20)
    cursor = first_page["preview"]["next_cursor"]
    assert isinstance(cursor, str)

    with pytest.raises(HostFailureException) as tampered:
        first_service.describe_dataset(
            reference,
            view="preview",
            cursor=cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
        )
    assert tampered.value.code == "invalid_tool_request"

    with pytest.raises(HostFailureException) as foreign:
        second_service.describe_dataset(
            reference,
            view="preview",
            cursor=cursor,
        )
    assert foreign.value.code == "reference_scope_denied"
    first_service.close()
    second_service.close()


def test_dataset_bound_execution_publishes_reconciled_dqop_and_bounded_views(
    tmp_path: Path,
) -> None:
    service = DefinedQuantService(session_state_root=tmp_path / "state")
    registration = service.register_dataset(
        {
            **_daily_request(count=4),
            "source": {
                "kind": "inline_rows",
                "rows": [
                    {"timestamp": "2024-01-02T00:00:00Z", "price": 100.0},
                    {"timestamp": "2024-01-03T00:00:00Z", "price": 110.0},
                    {"timestamp": "2024-01-04T00:00:00Z", "price": 121.0},
                    {"timestamp": "2024-01-05T00:00:00Z", "price": 108.9},
                ],
            },
        }
    )
    summary = service.execute_recorded_operation(
        _evaluation_component(),
        output_dir=tmp_path / "operation",
        literals={"price_kind": "adjusted", "declared_frequency": "daily"},
        sources=[
            {
                "kind": "dataset",
                "ref": registration["dataset_ref"],
                "mappings": [
                    {"source_field": "price", "input_field": "prices"},
                    {"source_field": "timestamp", "input_field": "timestamps"},
                ],
            }
        ],
        artifacts="svg_all",
    )

    reference = summary["operation_ref"]
    assert reference.startswith("dqop:v1:")
    assert summary["compact"] is True
    assert summary["source_context"] == {
        "kind": "dataset",
        "ref": registration["dataset_ref"],
        "external_preprocessing_status": "none_declared",
        "normalization_event_count": 0,
        "normalization_event_application_count": 0,
    }
    assert summary["result"]["returns"] == {
        "kind": "structured",
        "value_kind": "array",
        "count": 3,
        "sha256": summary["result"]["returns"]["sha256"],
        "page_available": True,
    }
    assert "prices" not in json.dumps(summary, sort_keys=True)
    assert summary["artifacts"][0]["artifact_id"] == "simple_periodic_returns"

    first = service.get_operation(reference, view="result_field", field="returns", limit=1)
    assert first["items"] == pytest.approx([0.1])
    second = service.get_operation(
        reference,
        view="result_field",
        field="returns",
        cursor=first["page"]["next_cursor"],
        limit=2,
    )
    assert second["items"] == pytest.approx([0.1, -0.1])
    assert second["page"]["complete"] is True
    manifest = service.get_operation(reference, view="manifest")
    assert manifest["manifest_sha256"] == summary["manifest_sha256"]
    artifact = service.get_operation(
        reference,
        view="artifact_metadata",
        artifact_id="simple_periodic_returns",
    )
    assert artifact["artifact"]["resource_uri"].startswith("dqop://v1/")
    assert len(
        _tool_result_bytes(
            service.get_operation(reference), trust=TrustLabel.UNMANAGED_EXECUTION
        )
    ) <= 256 * 1024
    assert not any(name == "mcp" or name.startswith("mcp.") for name in sys.modules)
    service.close()


def test_runtime_refusal_uses_exhaustive_transport_neutral_mapping(tmp_path: Path) -> None:
    service = DefinedQuantService(session_state_root=tmp_path / "state")
    with pytest.raises(HostFailureException) as refused:
        service.execute_recorded_operation(
            _evaluation_component(),
            output_dir=tmp_path / "operation",
            literals={"prices": [100.0, 101.0]},
            provenance={
                "source_kind": "synthetic",
                "interpretation_method": "caller_structured",
                "label": "Missing convention fixture.",
            },
        )
    assert refused.value.failure.model_dump(mode="json") == {
        "host_schema_version": 1,
        "outcome": "needs_information",
        "error": {
            "code": "missing_convention",
            "message": "An answer-changing financial convention must be supplied explicitly.",
            "retry_allowed": False,
            "details": {"fields": ["price_kind"]},
        },
        "trust": {
            "label": "no_verified_result",
            "statement": (
                "No dataset, component result, or digest-checked artifact was returned."
            ),
        },
    }
    service.close()


def test_summary_overflow_refuses_before_operation_cas_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def execute_with_large_scalar(
        service: DefinedQuantService,
        request: OperationRequest,
        *,
        output_dir: Path,
    ) -> OperationSuccess:
        return _execute_with_result_updates(
            service,
            request,
            output_dir=output_dir,
            updates={"oversized_scalar": "x" * (300 * 1024)},
        )

    monkeypatch.setattr(DefinedQuantService, "execute_operation", execute_with_large_scalar)
    service = DefinedQuantService(session_state_root=tmp_path / "state")

    with pytest.raises(HostFailureException) as limited:
        service.execute_recorded_operation(
            _evaluation_component(),
            output_dir=tmp_path / "oversized-operation",
            literals={"prices": [100.0, 101.0], "price_kind": "adjusted"},
            provenance={
                "source_kind": "synthetic",
                "interpretation_method": "caller_structured",
                "label": "Pre-publication response-limit regression.",
            },
        )

    assert limited.value.code == "result_limit_exceeded"
    assert limited.value.failure.error.details["limit_name"] == (
        "structured_tool_response_bytes"
    )
    assert service.session_cas.used_bytes == 0
    assert not list((service.session_cas.directory / "operations").iterdir())
    service.close()


def test_prepublication_summary_cursor_becomes_live_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [f"Disclosure {index}." for index in range(21)]

    def execute_with_many_messages(
        service: DefinedQuantService,
        request: OperationRequest,
        *,
        output_dir: Path,
    ) -> OperationSuccess:
        return _execute_with_result_updates(
            service,
            request,
            output_dir=output_dir,
            updates={"warnings": [], "disclosures": messages},
        )

    monkeypatch.setattr(DefinedQuantService, "execute_operation", execute_with_many_messages)
    service = DefinedQuantService(session_state_root=tmp_path / "state")
    summary = service.execute_recorded_operation(
        _evaluation_component(),
        output_dir=tmp_path / "message-operation",
        literals={"prices": [100.0, 101.0], "price_kind": "adjusted"},
        provenance={
            "source_kind": "synthetic",
            "interpretation_method": "caller_structured",
            "label": "Pending-cursor publication regression.",
        },
    )

    cursor = summary["messages"]["next_cursor"]
    assert isinstance(cursor, str)
    assert summary["messages"]["complete"] is False
    assert summary["messages"]["items"] == [
        {"kind": "disclosure", "index": index, "text": messages[index]}
        for index in range(20)
    ]
    remainder = service.get_operation(
        summary["operation_ref"],
        view="messages",
        cursor=cursor,
        limit=20,
    )
    assert remainder["messages"] == [
        {"kind": "disclosure", "index": 20, "text": messages[20]}
    ]
    assert remainder["page"]["complete"] is True
    service.close()
