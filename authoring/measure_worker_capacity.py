"""Measure native worker outcomes for synthetic row/column payloads."""

from __future__ import annotations

import gc
import importlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from typing import Any, cast

import defined_quant
from defined_quant import invalidate_subject_cache, load_execution_policy, subject_hash
from defined_quant.data_records import cas_json_bytes
from defined_quant.host_failures import HostFailureException
from defined_quant.worker_runtime import WorkerController
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    InterpretationMethod,
    OperationFailure,
    OperationRequest,
    SourceKind,
)
from pydantic import JsonValue

_COMPONENT_ID = "dq.capacity_probe.matrix"
_COMPONENT_VERSION = "1.0.0"
_COMPONENT_MODULE = "defined_quant.capacity_probe.matrix.component"
_COLUMN_COUNTS = (1, 8, 128)
_ROW_CANDIDATES = {
    1: (20_000, 30_000, 40_000, 50_000, 60_000, 80_000, 100_000, 150_000, 200_000),
    8: (1_000, 2_500, 5_000, 7_500, 10_000, 15_000, 20_000, 25_000),
    128: (50, 100, 250, 500, 750, 1_000, 1_250, 1_500, 2_000),
}
_COMPONENT_SOURCE = dedent(
    '''\
    """Synthetic identity component for native worker-capacity measurement."""

    from pathlib import Path
    from typing import Literal

    from defined_quant import subject_hash
    from defined_quant.types import DiagnosticOutput, Unit
    from pydantic import BaseModel, ConfigDict

    COMPONENT_ID = "dq.capacity_probe.matrix"
    COMPONENT_VERSION = "1.0.0"
    CATALOG_ROOT = Path(__file__).resolve().parents[2]


    class Inputs(BaseModel):
        model_config = ConfigDict(frozen=True, extra="forbid")

        values: list[list[float]]


    class Output(DiagnosticOutput):
        unit: Literal[Unit.UNITLESS]
        values: list[list[float]]


    def matrix(values: list[list[float]]) -> Output:
        validated = Inputs.model_validate({"values": values})
        return Output(
            component_id=COMPONENT_ID,
            version=COMPONENT_VERSION,
            subject_hash=subject_hash(COMPONENT_ID, root=CATALOG_ROOT),
            unit=Unit.UNITLESS,
            assumptions=("Synthetic identity payload for capacity measurement.",),
            disclosures=(),
            warnings=(),
            transformations=(),
            visualizations=(),
            passed=True,
            findings=(),
            coverage={"worker_capacity": 1.0},
            values=validated.values,
        )
    '''
)


def _install_component(catalog_root: Path) -> ComponentRef:
    component_root = catalog_root / "categories" / "capacity_probe" / "matrix"
    component_root.mkdir(parents=True)
    (component_root / "component.py").write_text(_COMPONENT_SOURCE, encoding="utf-8")
    contract = {
        "schema_version": 1,
        "id": _COMPONENT_ID,
        "slug": "matrix",
        "title": "Worker Capacity Matrix Probe",
        "category": "capacity_probe",
        "group": "integration",
        "version": _COMPONENT_VERSION,
        "lifecycle": "draft",
        "template": {"profile": "diagnostic", "version": 1},
        "callable": f"{_COMPONENT_MODULE}:matrix",
        "summary": "Returns one synthetic matrix through the complete worker boundary.",
        "discovery": {
            "aliases": ["worker capacity matrix probe"],
            "intents": ["measure_worker_capacity"],
            "input_concepts": ["synthetic_matrix"],
            "output_concepts": ["synthetic_matrix"],
        },
        "tags": ["integration", "synthetic"],
        "assumptions": ["Values are deterministic synthetic probes."],
        "limitations": ["This component exists only during capacity measurement."],
        "depends_on": [],
        "supported_python": [">=3.11", "<3.14"],
        "guidance": {
            "use_when": ["The native worker capacity is being measured."],
            "do_not_use_when": ["A financial result is requested."],
            "unsupported_scope": ["Financial interpretation."],
            "required_questions": [],
            "advisory_questions": [],
            "allowed_defaults": [],
            "constraints": [],
            "interpretation": "Treat the output only as a capacity probe.",
        },
        "display": {
            "formula": "identity(values)",
            "intent": "Measure one native worker payload.",
            "output": "The unchanged synthetic matrix.",
        },
    }
    (component_root / "contract.yaml").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    invalidate_subject_cache()
    return ComponentRef(
        id=_COMPONENT_ID,
        version=_COMPONENT_VERSION,
        subject_hash=subject_hash(_COMPONENT_ID, root=catalog_root),
    )


def _matrix_request(reference: ComponentRef, rows: int, columns: int) -> OperationRequest:
    row = [100.0123456789 + index * 0.0000001 for index in range(columns)]
    values = [list(row) for _index in range(rows)]
    return OperationRequest(
        component=reference,
        input={"values": cast(JsonValue, values)},
        provenance=CallerProvenance(
            source_kind=SourceKind.SYNTHETIC,
            interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
            label="Native worker capacity measurement.",
        ),
    )


def _simple_return_request(reference: ComponentRef, rows: int) -> OperationRequest:
    values = [100.0 + (index % 977) * 0.010203 for index in range(rows)]
    return OperationRequest(
        component=reference,
        input={
            "prices": cast(JsonValue, values),
            "price_kind": "adjusted",
        },
        provenance=CallerProvenance(
            source_kind=SourceKind.SYNTHETIC,
            interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
            label="Native worker capacity measurement.",
        ),
    )


def _envelope_bytes(request: OperationRequest) -> int:
    return len(
        cas_json_bytes(
            {
                "catalog_root": "C:/capacity-probe" if os.name == "nt" else "/capacity-probe",
                "output_dir": "C:/capacity-output" if os.name == "nt" else "/capacity-output",
                "request": request.model_dump(mode="json"),
                "schema_version": 1,
            }
        )
    )


def _measure_one(
    controller: WorkerController,
    catalog_root: Path | None,
    request: OperationRequest,
    root: Path,
    *,
    rows: int,
    columns: int,
) -> dict[str, Any]:
    request_bytes = _envelope_bytes(request)
    output = root / f"operation-{columns}-{rows}"
    try:
        execution = controller.execute_operation(
            request,
            output_dir=output,
            catalog_root=catalog_root,
        )
    except HostFailureException as exc:
        outcome = exc.code.value
        details = dict(exc.failure.error.details)
    else:
        if isinstance(execution.result, OperationFailure):
            outcome = f"operation_{execution.result.error.code.value}"
        else:
            outcome = "success"
        details = {}
    finally:
        del request
        gc.collect()
    return {
        "columns": columns,
        "details": details,
        "outcome": outcome,
        "request_bytes": request_bytes,
        "rows": rows,
    }


def main() -> int:
    with TemporaryDirectory(prefix="defined-quant-worker-capacity-") as temporary:
        root = Path(temporary)
        catalog_root = root / "catalog"
        defined_quant.__path__.append(os.fspath(catalog_root / "categories"))
        matrix_reference = _install_component(catalog_root)
        simple_return_reference = load_execution_policy(
            "simple_return_csv"
        ).components[0].component
        controller = WorkerController()
        try:
            for columns in _COLUMN_COUNTS:
                for rows in _ROW_CANDIDATES[columns]:
                    request = (
                        _simple_return_request(simple_return_reference, rows)
                        if columns == 1
                        else _matrix_request(matrix_reference, rows, columns)
                    )
                    result = _measure_one(
                        controller,
                        None if columns == 1 else catalog_root,
                        request,
                        root,
                        rows=rows,
                        columns=columns,
                    )
                    print(json.dumps(result, sort_keys=True), flush=True)
                    if result["outcome"] == "input_limit_exceeded":
                        break
        finally:
            controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
