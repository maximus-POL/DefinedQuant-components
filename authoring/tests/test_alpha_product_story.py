"""One public-API product story across the complete pre-MCP alpha host."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import defined_quant
import pytest
from defined_quant import invalidate_subject_cache, subject_hash
from defined_quant.data_records import DatasetRegistrationRequest, cas_json_bytes
from defined_quant.host_failures import (
    HostFailureException,
    TrustLabel,
    tool_success_result_projection,
)
from defined_quant.service import DefinedQuantService
from defined_quant_protocol import ComponentRef

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "LOCAL_MCP_ALPHA_DESIGN.md"
SAMPLE_CSV = Path(__file__).parent / "fixtures" / "alpha_product_story.csv"
COMPONENT_ID = "dq.alpha_e2e.product_story"
COMPONENT_VERSION = "1.0.0"
COMPONENT_MODULE = "defined_quant.alpha_e2e.product_story.component"
TOOL_RESPONSE_LIMIT_BYTES = 256 * 1024

_COMPONENT_SOURCE = dedent(
    '''\
    """Synthetic component used only by the public alpha product-story test."""

    from pathlib import Path
    from typing import Literal

    from defined_quant import subject_hash
    from defined_quant.types import (
        AxisSpec,
        ChartKind,
        ChartSeries,
        DiagnosticOutput,
        NumberFormat,
        Unit,
        VisualizationSpec,
    )
    from pydantic import BaseModel, ConfigDict, Field, StrictFloat

    COMPONENT_ID = "dq.alpha_e2e.product_story"
    COMPONENT_VERSION = "1.0.0"
    CATALOG_ROOT = Path(__file__).resolve().parents[2]


    class Inputs(BaseModel):
        model_config = ConfigDict(frozen=True, extra="forbid")

        values: tuple[StrictFloat, ...] = Field(min_length=2)


    class Output(DiagnosticOutput):
        unit: Literal[Unit.DECIMAL]
        status: Literal["complete"]
        values: tuple[float, ...] = Field(min_length=2)


    def product_story(values: tuple[float, ...] | list[float]) -> Output:
        inputs = Inputs.model_validate({"values": values})
        return Output(
            component_id=COMPONENT_ID,
            version=COMPONENT_VERSION,
            subject_hash=subject_hash(COMPONENT_ID, root=CATALOG_ROOT),
            unit=Unit.DECIMAL,
            assumptions=("Synthetic values are accepted exactly as registered.",),
            disclosures=("Synthetic disclosure zero.", "Synthetic disclosure one."),
            warnings=("Synthetic warning zero.", "Synthetic warning one."),
            transformations=("Preserved the normalized value order.",),
            visualizations=(
                VisualizationSpec(
                    id="alpha_story_values",
                    kind=ChartKind.LINE,
                    title="Synthetic alpha story values",
                    alt_text="Line chart of deterministic synthetic values.",
                    categories=tuple(str(index) for index in range(len(inputs.values))),
                    series=(
                        ChartSeries(
                            key="values",
                            label="Value",
                            values=inputs.values,
                        ),
                    ),
                    x_axis=AxisSpec(label="Observation", unit=Unit.UNITLESS),
                    y_axis=AxisSpec(
                        label="Value",
                        unit=Unit.DECIMAL,
                        number_format=NumberFormat.DECIMAL,
                    ),
                ),
            ),
            passed=True,
            findings=(),
            coverage={
                "execution": 1.0,
                "normalization": 1.0,
                "registration": 1.0,
            },
            status="complete",
            values=inputs.values,
        )
    '''
)


def _install_test_component(catalog_root: Path) -> ComponentRef:
    component_root = (
        catalog_root / "categories" / "alpha_e2e" / "product_story"
    )
    component_root.mkdir(parents=True)
    (component_root / "component.py").write_text(_COMPONENT_SOURCE, encoding="utf-8")
    contract = {
        "schema_version": 1,
        "id": COMPONENT_ID,
        "slug": "product_story",
        "title": "Alpha Product Story",
        "category": "alpha_e2e",
        "group": "integration",
        "version": COMPONENT_VERSION,
        "lifecycle": "draft",
        "template": {"profile": "diagnostic", "version": 1},
        "callable": f"{COMPONENT_MODULE}:product_story",
        "summary": "Exercises every operation-record result shape deterministically.",
        "discovery": {
            "aliases": ["alpha product story"],
            "intents": ["exercise_alpha_product_story"],
            "input_concepts": ["synthetic_value_series"],
            "output_concepts": ["synthetic_diagnostics"],
        },
        "tags": ["integration", "synthetic"],
        "assumptions": ["Inputs are deterministic synthetic test data."],
        "limitations": ["This component is available only inside this test."],
        "depends_on": [],
        "supported_python": [">=3.11", "<3.14"],
        "guidance": {
            "use_when": ["The alpha host requires an end-to-end integration test."],
            "do_not_use_when": ["A financial result is requested."],
            "unsupported_scope": ["Financial interpretation."],
            "required_questions": [],
            "advisory_questions": [],
            "allowed_defaults": [],
            "constraints": [],
            "interpretation": "Treat every output as synthetic integration-test data.",
        },
        "display": {
            "formula": "identity(values)",
            "intent": "Exercise the public host product story.",
            "output": "Synthetic scalar, array, object, messages, and SVG artifact.",
        },
    }
    (component_root / "contract.yaml").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    invalidate_subject_cache()
    return ComponentRef(
        id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        subject_hash=subject_hash(COMPONENT_ID, root=catalog_root),
    )


def _registration_request(source: dict[str, Any]) -> DatasetRegistrationRequest:
    return DatasetRegistrationRequest.model_validate(
        {
            "source": source,
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
                "instrument": {"namespace": "synthetic", "symbol": "ALPHA"},
                "frequency": "daily",
                "timezone": "UTC",
                "ordering": "preserve_source_order",
                "price_kind": "unadjusted",
            },
            "provenance": {
                "source_kind": "synthetic",
                "interpretation_method": "caller_structured",
                "label": "Synthetic alpha product-story observations.",
            },
            "external_preprocessing": {"status": "none_declared"},
        }
    )


def _inline_rows() -> list[dict[str, Any]]:
    lines = SAMPLE_CSV.read_text(encoding="utf-8").splitlines()
    return [
        {"timestamp": timestamp, "price": float(price)}
        for timestamp, price in (line.split(",") for line in lines[1:])
    ]


def _documented_trust_statement(label: TrustLabel) -> str:
    marker = f"| `{label.value}` |"
    matches = [
        line.split("|", maxsplit=3)[2].strip()
        for line in DESIGN.read_text(encoding="utf-8").splitlines()
        if line.startswith(marker)
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_success_response(data: dict[str, Any], trust: TrustLabel) -> None:
    result = tool_success_result_projection(data, trust=trust)
    envelope = result["structuredContent"]
    assert envelope["outcome"] == "ok"
    assert envelope["data"] == data
    assert envelope["trust"] == {
        "label": trust.value,
        "statement": _documented_trust_statement(trust),
    }
    assert len(cas_json_bytes(result)) - 1 <= TOOL_RESPONSE_LIMIT_BYTES


def _assert_scope_refusal(caught: pytest.ExceptionInfo[HostFailureException]) -> None:
    failure = caught.value.failure
    assert failure.error.code == "reference_scope_denied"
    assert failure.trust.label is TrustLabel.NO_VERIFIED_RESULT
    assert failure.trust.statement == _documented_trust_statement(
        TrustLabel.NO_VERIFIED_RESULT
    )
    result = {
        "content": [{"type": "text", "text": failure.error.message}],
        "structuredContent": failure.model_dump(mode="json"),
        "isError": True,
    }
    assert len(cas_json_bytes(result)) - 1 <= TOOL_RESPONSE_LIMIT_BYTES


def test_complete_alpha_product_story_uses_only_the_public_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register, page, execute, inspect, verify, repeat, and enforce session scope."""

    catalog_root = tmp_path / "catalog"
    catalog_package = catalog_root / "categories"
    monkeypatch.setattr(
        defined_quant,
        "__path__",
        [*defined_quant.__path__, str(catalog_package)],
    )
    component = _install_test_component(catalog_root)
    inline_request = _registration_request(
        {"kind": "inline_rows", "rows": _inline_rows()}
    )
    csv_request = _registration_request(
        {
            "kind": "local_file",
            "path": str(SAMPLE_CSV),
            "format": "csv",
        }
    )
    state_root = tmp_path / "state"

    try:
        with DefinedQuantService(
            catalog_root=catalog_root,
            data_roots=(SAMPLE_CSV.parent,),
            session_state_root=state_root,
        ) as service:
            inline_registration = service.register_dataset(inline_request)
            repeated_inline = service.register_dataset(inline_request)
            csv_registration = service.register_dataset(csv_request)
            repeated_csv = service.register_dataset(csv_request)
            for response in (
                inline_registration,
                repeated_inline,
                csv_registration,
                repeated_csv,
            ):
                _assert_success_response(response, TrustLabel.UNVERIFIED_CALLER_DATA)

            inline_ref = inline_registration["dataset_ref"]
            csv_ref = csv_registration["dataset_ref"]
            assert repeated_inline["dataset_ref"] == inline_ref
            assert repeated_csv["dataset_ref"] == csv_ref
            assert inline_ref != csv_ref
            assert (
                inline_registration["normalized_payload_sha256"]
                == csv_registration["normalized_payload_sha256"]
            )

            for reference in (inline_ref, csv_ref):
                metadata = service.describe_dataset(reference, view="metadata")
                _assert_success_response(metadata, TrustLabel.UNVERIFIED_CALLER_DATA)
                assert metadata["row_count"] == len(_inline_rows())

            preview_rows: list[dict[str, Any]] = []
            cursor: str | None = None
            while True:
                preview = service.describe_dataset(
                    csv_ref,
                    view="preview",
                    cursor=cursor,
                    limit=3,
                )
                _assert_success_response(preview, TrustLabel.UNVERIFIED_CALLER_DATA)
                preview_rows.extend(preview["preview"]["rows"])
                cursor = preview["preview"]["next_cursor"]
                if cursor is None:
                    assert preview["preview"]["complete"] is True
                    break
            assert preview_rows == _inline_rows()

            operation = service.execute_recorded_operation(
                component,
                output_dir=tmp_path / "operation-one",
                sources=[
                    {
                        "kind": "dataset",
                        "ref": csv_ref,
                        "mappings": [
                            {"source_field": "price", "input_field": "values"}
                        ],
                    }
                ],
                artifacts="svg_all",
            )
            _assert_success_response(operation, TrustLabel.UNMANAGED_EXECUTION)
            operation_ref = operation["operation_ref"]

            summary = service.get_operation(operation_ref, view="summary")
            manifest = service.get_operation(operation_ref, view="manifest")
            scalar = service.get_operation(
                operation_ref,
                view="result_field",
                field="status",
            )
            for response in (summary, manifest, scalar):
                _assert_success_response(response, TrustLabel.UNMANAGED_EXECUTION)
            assert scalar["value_kind"] == "scalar"
            assert scalar["value"] == "complete"

            values: list[float] = []
            cursor = None
            while True:
                page = service.get_operation(
                    operation_ref,
                    view="result_field",
                    field="values",
                    cursor=cursor,
                    limit=3,
                )
                _assert_success_response(page, TrustLabel.UNMANAGED_EXECUTION)
                assert page["value_kind"] == "array"
                values.extend(page["items"])
                cursor = page["page"]["next_cursor"]
                if cursor is None:
                    assert page["page"]["complete"] is True
                    break
            assert values == [row["price"] for row in preview_rows]

            coverage_entries: list[dict[str, Any]] = []
            cursor = None
            while True:
                page = service.get_operation(
                    operation_ref,
                    view="result_field",
                    field="coverage",
                    cursor=cursor,
                    limit=1,
                )
                _assert_success_response(page, TrustLabel.UNMANAGED_EXECUTION)
                assert page["value_kind"] == "object"
                coverage_entries.extend(page["entries"])
                cursor = page["page"]["next_cursor"]
                if cursor is None:
                    assert page["page"]["complete"] is True
                    break
            assert coverage_entries == [
                {"key": "execution", "value": 1.0},
                {"key": "normalization", "value": 1.0},
                {"key": "registration", "value": 1.0},
            ]

            messages: list[dict[str, Any]] = []
            cursor = None
            while True:
                page = service.get_operation(
                    operation_ref,
                    view="messages",
                    cursor=cursor,
                    limit=1,
                )
                _assert_success_response(page, TrustLabel.UNMANAGED_EXECUTION)
                messages.extend(page["messages"])
                cursor = page["page"]["next_cursor"]
                if cursor is None:
                    assert page["page"]["complete"] is True
                    break
            assert [message["kind"] for message in messages] == [
                "warning",
                "warning",
                "disclosure",
                "disclosure",
            ]

            artifact = service.get_operation(
                operation_ref,
                view="artifact_metadata",
                artifact_id="alpha_story_values",
            )
            _assert_success_response(artifact, TrustLabel.UNMANAGED_EXECUTION)
            declared_artifact = manifest["manifest"]["artifacts"][0]
            artifact_bytes = (
                tmp_path / "operation-one" / declared_artifact["path"]
            ).read_bytes()
            artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
            assert artifact_sha256 == declared_artifact["sha256"]
            assert artifact_sha256 == artifact["artifact"]["sha256"]
            assert len(artifact_bytes) == artifact["artifact"]["size_bytes"]

            repeated_operation = service.execute_recorded_operation(
                component,
                output_dir=tmp_path / "operation-two",
                sources=[
                    {
                        "kind": "dataset",
                        "ref": csv_ref,
                        "mappings": [
                            {"source_field": "price", "input_field": "values"}
                        ],
                    }
                ],
                artifacts="svg_all",
            )
            _assert_success_response(
                repeated_operation, TrustLabel.UNMANAGED_EXECUTION
            )
            assert repeated_operation["operation_ref"] == operation_ref

            with DefinedQuantService(
                catalog_root=catalog_root,
                data_roots=(SAMPLE_CSV.parent,),
                session_state_root=state_root,
            ) as second_session:
                for reference in (inline_ref, csv_ref):
                    with pytest.raises(HostFailureException) as refused_dataset:
                        second_session.describe_dataset(reference)
                    _assert_scope_refusal(refused_dataset)
                with pytest.raises(HostFailureException) as refused_operation:
                    second_session.get_operation(operation_ref)
                _assert_scope_refusal(refused_operation)

        assert not any(
            name == "mcp" or name.startswith("mcp.") for name in sys.modules
        )
    finally:
        invalidate_subject_cache()
        for name in tuple(sys.modules):
            if name == "defined_quant.alpha_e2e" or name.startswith(
                "defined_quant.alpha_e2e."
            ):
                sys.modules.pop(name, None)
