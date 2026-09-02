"""Phase-3 reconciliation of immutable operation records with Phase-1 bundles."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from defined_quant.data_records import (
    DatasetPayloadV1,
    DatasetRecordV1,
    OperationBindingV1,
    pretty_json_bytes,
)
from defined_quant.dataset_registry import normalize_dataset, resolve_dataset_fields
from defined_quant.operation_records import (
    MAX_ARTIFACT_BYTES,
    MAX_OPERATION_BUNDLE_BYTES,
    OperationRecordError,
    build_operation_record,
    load_operation_result,
    reconcile_operation_record,
)
from defined_quant.operation_runtime import execute_operation
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    FileDigest,
    OperationManifest,
    OperationRequest,
    OperationSuccess,
    SvgArtifactRequest,
)

ROOT = Path(__file__).resolve().parents[2]
PHASE0 = Path(__file__).parent / "fixtures" / "operation_runtime.json"


def _phase0() -> dict[str, Any]:
    value = json.loads(PHASE0.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _component() -> ComponentRef:
    return ComponentRef.model_validate(_phase0()["component"])


def _literal_request(*, artifacts: bool = True) -> OperationRequest:
    case = _phase0()["typed_success"]
    return OperationRequest(
        component=_component(),
        input=case["input"],
        provenance=CallerProvenance.model_validate(case["provenance"]),
        artifacts=(SvgArtifactRequest() if artifacts else None),
    )


def _dataset_request() -> dict[str, Any]:
    return {
        "source": {
            "kind": "inline_rows",
            "rows": [
                {"timestamp": "2024-01-02T00:00:00Z", "price": 100.0},
                {"timestamp": "2024-01-03T00:00:00Z", "price": 103.0},
                {"timestamp": "2024-01-04T00:00:00Z", "price": 101.0},
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
            "label": "Synthetic Phase-3 reconciliation data.",
        },
        "external_preprocessing": {"status": "none_declared"},
    }


def test_literal_operation_record_reconciles_every_phase1_byte(tmp_path: Path) -> None:
    request = _literal_request()
    bundle = tmp_path / "bundle"
    outcome = execute_operation(request, output_dir=bundle)
    assert isinstance(outcome, OperationSuccess)

    record = build_operation_record(
        outcome.manifest,
        bundle,
        OperationBindingV1(literals=request.input),
    )

    assert record.operation_hash == request.operation_hash
    assert (
        record.manifest_sha256
        == hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    )
    assert record.runner.model_dump() == {"name": "use_defined_quant", "version": "0.1.0"}
    assert [member.kind for member in record.members] == [
        "normalized_input",
        "result",
        "artifact",
    ]
    assert record.renderers[0].model_dump() == {
        "name": "defined_quant_svg",
        "version": "0.1.3",
    }
    assert reconcile_operation_record(record, bundle) == outcome.manifest
    result = load_operation_result(record, bundle)
    assert result["component_id"] == request.component.id
    assert result["returns"] == pytest.approx([0.03, -0.019417475728155338])


def test_dataset_binding_reconstructs_exact_request_and_transitive_provenance(
    tmp_path: Path,
) -> None:
    dataset = normalize_dataset(_dataset_request())
    binding = OperationBindingV1.model_validate(
        {
            "literals": {"price_kind": "adjusted", "declared_frequency": "daily"},
            "source": {
                "kind": "dataset",
                "ref": dataset.record.reference,
                "mappings": [
                    {"source_field": "price", "input_field": "prices"},
                    {"source_field": "timestamp", "input_field": "timestamps"},
                ],
            },
        }
    )
    resolved = resolve_dataset_fields(
        dataset.payload,
        binding.source.mappings if binding.source is not None else (),
        literals=binding.literals,
    )
    request = OperationRequest(
        component=_component(),
        input=resolved,
        provenance=CallerProvenance.model_validate(
            dataset.record.source.provenance.model_dump(mode="json")
        ),
    )
    bundle = tmp_path / "dataset-operation"
    outcome = execute_operation(request, output_dir=bundle)
    assert isinstance(outcome, OperationSuccess)

    first = build_operation_record(
        outcome.manifest,
        bundle,
        binding,
        dataset_record=dataset.record,
        dataset_payload=dataset.payload,
    )
    second = build_operation_record(
        outcome.manifest,
        bundle,
        binding,
        dataset_record=dataset.record,
        dataset_payload=dataset.payload,
    )
    assert first == second
    assert first.reference == second.reference
    assert first.binding.source is not None
    assert first.binding.source.ref == dataset.record.reference
    assert (
        reconcile_operation_record(
            first,
            bundle,
            dataset_record=dataset.record,
            dataset_payload=dataset.payload,
        )
        == outcome.manifest
    )


@pytest.mark.parametrize("member", ["input.json", "result.json", "manifest.json"])
def test_reconciliation_refuses_mutated_phase1_members(tmp_path: Path, member: str) -> None:
    request = _literal_request(artifacts=False)
    original = tmp_path / "original"
    outcome = execute_operation(request, output_dir=original)
    assert isinstance(outcome, OperationSuccess)
    record = build_operation_record(
        outcome.manifest,
        original,
        {"literals": request.input},
    )
    corrupt = tmp_path / f"corrupt-{member}"
    shutil.copytree(original, corrupt)
    (corrupt / member).write_bytes(b"{}\n")

    with pytest.raises(OperationRecordError) as corrupt_error:
        reconcile_operation_record(record, corrupt)
    assert corrupt_error.value.code == "record_corrupt"


@pytest.mark.parametrize(
    "alias",
    ["RESULT.JSON", "MANIFEST.JSON", "CON.txt", "result.json:stream", "PROGRA~1"],
)
def test_reconciliation_refuses_nonportable_or_case_colliding_member_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
) -> None:
    request = _literal_request(artifacts=False)
    bundle = tmp_path / "bundle"
    outcome = execute_operation(request, output_dir=bundle)
    assert isinstance(outcome, OperationSuccess)
    real_iterdir = Path.iterdir

    def aliased_iterdir(path: Path) -> Any:
        entries = tuple(real_iterdir(path))
        if path == bundle:
            return iter((*entries, path / alias))
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", aliased_iterdir)
    with pytest.raises(OperationRecordError) as corrupt:
        build_operation_record(outcome.manifest, bundle, {"literals": request.input})
    assert corrupt.value.code == "record_corrupt"


def test_binding_mismatch_and_operation_source_fail_before_publication(tmp_path: Path) -> None:
    request = _literal_request(artifacts=False)
    bundle = tmp_path / "bundle"
    outcome = execute_operation(request, output_dir=bundle)
    assert isinstance(outcome, OperationSuccess)

    with pytest.raises(OperationRecordError) as mismatch:
        build_operation_record(
            outcome.manifest,
            bundle,
            {"literals": {**request.input, "price_kind": "unadjusted"}},
        )
    assert mismatch.value.code == "invalid_field_mapping"
    assert mismatch.value.details == {"fields": []}

    with pytest.raises(OperationRecordError) as unsupported:
        build_operation_record(
            outcome.manifest,
            bundle,
            {
                "literals": {"price_kind": "adjusted"},
                "source": {
                    "kind": "operation",
                    "ref": f"dqop:v1:{'a' * 64}",
                    "mappings": [{"source_field": "returns", "input_field": "prices"}],
                },
            },
        )
    assert unsupported.value.code == "unsupported_binding"


def test_artifact_limit_fails_before_phase1_verifier_reads_the_bundle(tmp_path: Path) -> None:
    request = _literal_request()
    bundle = tmp_path / "oversized-artifact"
    outcome = execute_operation(request, output_dir=bundle)
    assert isinstance(outcome, OperationSuccess)
    artifact = outcome.manifest.artifacts[0]
    content = b"x" * (MAX_ARTIFACT_BYTES + 1)
    (bundle / artifact.path).write_bytes(content)
    changed_artifact = artifact.model_copy(update={"sha256": hashlib.sha256(content).hexdigest()})
    manifest = outcome.manifest.model_copy(update={"artifacts": (changed_artifact,)})
    (bundle / "manifest.json").write_bytes(pretty_json_bytes(manifest.model_dump(mode="json")))

    with pytest.raises(OperationRecordError) as limited:
        build_operation_record(
            manifest,
            bundle,
            {"literals": request.input},
        )
    assert limited.value.code == "result_limit_exceeded"
    assert limited.value.details == {
        "limit_name": "artifact_decoded_bytes",
        "maximum": MAX_ARTIFACT_BYTES,
        "actual": MAX_ARTIFACT_BYTES + 1,
    }


def test_non_object_result_is_corruption_not_a_limit(tmp_path: Path) -> None:
    request = _literal_request(artifacts=False)
    bundle = tmp_path / "non-object-result"
    outcome = execute_operation(request, output_dir=bundle)
    assert isinstance(outcome, OperationSuccess)
    content = b"[]\n"
    (bundle / outcome.manifest.result.path).write_bytes(content)
    manifest = outcome.manifest.model_copy(
        update={
            "result": FileDigest(
                path=outcome.manifest.result.path,
                sha256=hashlib.sha256(content).hexdigest(),
            )
        }
    )
    (bundle / "manifest.json").write_bytes(pretty_json_bytes(manifest.model_dump(mode="json")))

    with pytest.raises(OperationRecordError) as corrupt:
        build_operation_record(manifest, bundle, {"literals": request.input})
    assert corrupt.value.code == "record_corrupt"


def test_manifest_is_a_bundle_member_not_a_two_megabyte_cas_record(tmp_path: Path) -> None:
    row_count = 25_000
    repeated = "x" * 100
    payload = DatasetPayloadV1.model_validate(
        {
            "schema_version": 1,
            "serialization": "dq-table-v1",
            "columns": [{"field_id": "label", "data_type": "string"}],
            "rows": [[repeated] for _ in range(row_count)],
        }
    )
    provenance = {
        "source_kind": "synthetic",
        "interpretation_method": "caller_structured",
        "verification_status": "unverified",
        "label": "Large manifest boundary regression.",
        "references": [],
        "assumptions": [],
    }
    dataset = DatasetRecordV1.model_validate(
        {
            "host_schema_version": 1,
            "record_kind": "dataset",
            "payload": {"serialization": "dq-table-v1", "sha256": payload.digest},
            "raw_source_sha256": "ab" * 32,
            "source": {"kind": "inline_rows", "provenance": provenance},
            "external_preprocessing": {"status": "none_declared"},
            "columns": [
                {
                    "source_name": "label",
                    "field_id": "label",
                    "data_type": "string",
                    "role": "label",
                    "semantic_port": None,
                }
            ],
            "semantics": {"ordering": "preserve_source_order"},
            "row_count": row_count,
            "column_count": 1,
            "observation_bounds": None,
            "normalization_events": [],
            "findings": [],
            "normalizer": {
                "name": "defined_quant_mcp_dataset_normalizer",
                "version": "1.0.0",
            },
        }
    )
    dataset.validate_payload(payload)
    request = OperationRequest(
        component=_component(),
        input={"blob": [repeated] * row_count},
        provenance=CallerProvenance.model_validate(provenance),
    )
    bundle = tmp_path / "large-manifest"
    bundle.mkdir()
    input_bytes = pretty_json_bytes(request.input)
    result_bytes = pretty_json_bytes({"accepted": True})
    (bundle / "input.json").write_bytes(input_bytes)
    (bundle / "result.json").write_bytes(result_bytes)
    manifest = OperationManifest(
        operation_hash=request.operation_hash,
        request=request,
        component=request.component,
        runner={"name": "use_defined_quant", "version": "0.1.0"},
        input=FileDigest(path="input.json", sha256=hashlib.sha256(input_bytes).hexdigest()),
        result=FileDigest(path="result.json", sha256=hashlib.sha256(result_bytes).hexdigest()),
    )
    manifest_bytes = pretty_json_bytes(manifest.model_dump(mode="json"))
    assert 2 * 1024 * 1024 < len(manifest_bytes) < MAX_OPERATION_BUNDLE_BYTES
    (bundle / "manifest.json").write_bytes(manifest_bytes)
    binding = OperationBindingV1.model_validate(
        {
            "literals": {},
            "source": {
                "kind": "dataset",
                "ref": dataset.reference,
                "mappings": [{"source_field": "label", "input_field": "blob"}],
            },
        }
    )

    record = build_operation_record(
        manifest,
        bundle,
        binding,
        dataset_record=dataset,
        dataset_payload=payload,
    )

    assert record.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
