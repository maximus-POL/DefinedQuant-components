from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from defined_quant import component_record, subject_hash
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    SvgArtifactRequest,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
RUNNER = SKILL_ROOT / "scripts" / "run_component.py"


def _component_ref() -> ComponentRef:
    record = component_record("dq.market_data.simple_return")
    return ComponentRef(
        id=record.component_id,
        version=record.version,
        subject_hash=subject_hash(record),
    )


def _input() -> dict[str, object]:
    return {
        "prices": [100.0, 103.0, 101.0],
        "price_kind": "adjusted",
        "timestamps": [
            "2026-07-20T00:00:00Z",
            "2026-07-21T00:00:00Z",
            "2026-07-22T00:00:00Z",
        ],
    }


def _request(
    *,
    artifacts: bool = True,
    input_data: dict[str, object] | None = None,
) -> OperationRequest:
    return OperationRequest(
        component=_component_ref(),
        input=input_data if input_data is not None else _input(),
        provenance=CallerProvenance(
            source_kind="synthetic",
            interpretation_method="caller_structured",
            verification_status="unverified",
            label="Synthetic adapter regression fixture.",
        ),
        artifacts=SvgArtifactRequest() if artifacts else None,
    )


def _write_request(path: Path, request: OperationRequest | dict[str, object]) -> None:
    value = request.model_dump(mode="json") if isinstance(request, OperationRequest) else request
    path.write_text(json.dumps(value), encoding="utf-8")


def _command(
    request_path: Path,
    output_dir: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--request",
            str(request_path),
            "--output-dir",
            str(output_dir),
            *extra,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _python_probe(source: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source, str(SKILL_ROOT / "scripts"), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _success(completed: subprocess.CompletedProcess[str]) -> OperationSuccess:
    assert completed.returncode == 0, completed.stderr
    return OperationSuccess.model_validate_json(completed.stdout)


def _failure(completed: subprocess.CompletedProcess[str]) -> OperationFailure:
    assert completed.returncode == 2, completed.stdout
    return OperationFailure.model_validate_json(completed.stderr)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_typed_request_is_portable_hash_bound_and_repeatable(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first_completed = _command(request_path, first_dir)
    second_completed = _command(request_path, second_dir)
    first = _success(first_completed)
    second = _success(second_completed)

    assert first == second
    assert first_completed.stdout == second_completed.stdout
    manifest = first.manifest
    assert manifest.execution_mode == "unmanaged"
    assert manifest.request == request
    assert manifest.component == request.component
    assert manifest.operation_hash == request.operation_hash
    assert manifest.operation_hash == manifest.request.operation_hash
    assert manifest.input.sha256 == _sha256(first_dir / manifest.input.path)
    assert manifest.result.sha256 == _sha256(first_dir / manifest.result.path)
    result_members = (manifest.input.path, manifest.result.path)
    assert all(not Path(member).is_absolute() for member in result_members)
    assert all(not Path(artifact.path).is_absolute() for artifact in manifest.artifacts)
    assert all(len(Path(artifact.path).parts) == 1 for artifact in manifest.artifacts)
    assert all(".." not in Path(artifact.path).parts for artifact in manifest.artifacts)
    assert str(tmp_path) not in first_completed.stdout
    assert (first_dir / "manifest.json").read_bytes() == (second_dir / "manifest.json").read_bytes()
    assert not list(tmp_path.glob(".first.dq-stage-*"))
    assert not list(tmp_path.glob(".second.dq-stage-*"))


def test_exact_component_identity_mismatch_refuses_before_output(tmp_path: Path) -> None:
    request = _request()
    changed = request.model_copy(
        update={
            "component": request.component.model_copy(update={"subject_hash": "0" * 64})
        }
    )
    request_path = tmp_path / "mismatch.json"
    _write_request(request_path, changed)
    output_parent = tmp_path / "missing" / "nested"
    output_dir = output_parent / "output"

    failure = _failure(_command(request_path, output_dir))

    assert failure.operation_hash == changed.operation_hash
    assert failure.error.code == "component_identity_mismatch"
    assert failure.error.component == changed.component
    assert not output_dir.exists()
    assert not (tmp_path / "missing").exists()


def test_invalid_component_input_retains_valid_request_hash(tmp_path: Path) -> None:
    request = _request(
        input_data={
            "prices": [100.0, 101.0],
            "price_kind": "vendor_defined",
        }
    )
    request_path = tmp_path / "invalid-input.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    failure = _failure(_command(request_path, output_dir))

    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "invalid_component_input"
    assert failure.error.component == request.component
    assert not output_dir.exists()


def test_missing_required_question_is_typed_as_ambiguous_input(tmp_path: Path) -> None:
    request = _request(input_data={"prices": [100.0, 101.0]})
    request_path = tmp_path / "missing-question.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    failure = _failure(_command(request_path, output_dir))

    assert failure.error.code == "component_refused"
    component_error = failure.error.details["component_error"]
    assert component_error["code"] == "ambiguous_input"
    assert component_error["details"]["questions"][0]["field"] == "price_kind"
    assert not output_dir.exists()


def test_component_domain_refusal_is_distinct_from_input_validation(tmp_path: Path) -> None:
    request = _request(
        input_data={
            "prices": [100.0, 0.0],
            "price_kind": "adjusted",
        }
    )
    request_path = tmp_path / "domain-refusal.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    failure = _failure(_command(request_path, output_dir))

    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "component_refused"
    assert failure.error.details["component_error"]["code"] == "domain_error"
    assert not output_dir.exists()


def test_over_chart_limit_preserves_full_result_without_svg(tmp_path: Path) -> None:
    request = _request(
        input_data={
            "prices": [100.0] * 502,
            "price_kind": "adjusted",
        }
    )
    request_path = tmp_path / "over-chart-limit.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    success = _success(_command(request_path, output_dir))
    result = json.loads((output_dir / success.manifest.result.path).read_text(encoding="utf-8"))

    assert result["returns"] == [0.0] * 501
    assert len(result["derivations"]) == 501
    assert result["derivations"][0] == {
        "output": {"field": "returns", "index": 0},
        "inputs": [
            {"field": "prices", "index": 0, "citation_id": None},
            {"field": "prices", "index": 1, "citation_id": None},
        ],
        "expression": "(prices[1] - prices[0]) / prices[0]",
        "value": 0.0,
    }
    assert result["visualizations"] == []
    assert any(
        warning.startswith("visualization_omitted:") for warning in result["warnings"]
    )
    assert success.manifest.artifacts == ()


def test_runtime_configuration_is_rejected_inside_request(tmp_path: Path) -> None:
    request = _request().model_dump(mode="json")
    request["output_dir"] = str(tmp_path / "smuggled-output")
    request["overwrite"] = True
    request_path = tmp_path / "invalid-request.json"
    _write_request(request_path, request)

    failure = _failure(_command(request_path, tmp_path / "real-output"))

    assert failure.operation_hash is None
    assert failure.error.code == "invalid_operation_request"
    assert not (tmp_path / "smuggled-output").exists()
    assert not (tmp_path / "real-output").exists()


def test_malformed_json_has_no_invented_operation_hash(tmp_path: Path) -> None:
    request_path = tmp_path / "malformed.json"
    request_path.write_text("{", encoding="utf-8")

    failure = _failure(_command(request_path, tmp_path / "output"))

    assert failure.operation_hash is None
    assert failure.error.code == "invalid_json"


def test_duplicate_json_keys_are_rejected_before_schema_validation(tmp_path: Path) -> None:
    request_path = tmp_path / "duplicate.json"
    request_path.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    completed = _command(request_path, tmp_path / "output")
    failure = _failure(completed)

    assert failure.operation_hash is None
    assert failure.error.code == "invalid_json"
    assert failure.error.details == {}
    assert not (tmp_path / "output").exists()


def test_nonfinite_normalized_input_is_typed_and_never_materialized(tmp_path: Path) -> None:
    request = _request(
        input_data={
            "prices": ["NaN", 101.0],
            "price_kind": "adjusted",
        }
    )
    request_path = tmp_path / "nonfinite-input.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    completed = _command(request_path, output_dir)
    failure = _failure(completed)

    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "invalid_component_input"
    assert "NaN" not in completed.stderr
    assert not output_dir.exists()


def test_nonfinite_component_result_is_refused_before_staging(tmp_path: Path) -> None:
    request = _request(
        input_data={
            "prices": [1e-308, 1e15],
            "price_kind": "adjusted",
        }
    )
    request_path = tmp_path / "nonfinite-result.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    completed = _command(request_path, output_dir)
    failure = _failure(completed)

    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "component_refused"
    assert "Infinity" not in completed.stderr
    assert not output_dir.exists()
    assert not list(tmp_path.glob(".output.dq-stage-*"))


def test_nonfinite_component_output_is_rejected_before_staging(tmp_path: Path) -> None:
    completed = _python_probe(
        """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import run_component as runner

root = Path(sys.argv[2])
record = runner.component_record("dq.market_data.simple_return")
reference = runner._component_ref(record)
request = runner.OperationRequest(
    component=reference,
    input={"prices": [100.0, 101.0], "price_kind": "adjusted"},
    provenance=runner.CallerProvenance(
        source_kind="synthetic",
        interpretation_method="caller_structured",
        label="Synthetic non-finite output regression fixture.",
    ),
)
inputs_model, _ = runner.component_models(record)
validated = inputs_model.model_validate(request.input)
original = runner.load_component(record)

def nonfinite_component(**kwargs):
    result = original(**kwargs)
    return result.model_copy(update={"returns": (float("inf"),)})

runner.load_component = lambda selected: nonfinite_component
try:
    runner._run(request, record, root / "output")
except Exception as error:
    runner._print_model(runner._failure(error, request=request), stream=sys.stdout)
""",
        str(tmp_path),
    )

    assert completed.returncode == 0, completed.stderr
    failure = OperationFailure.model_validate_json(completed.stdout)
    assert failure.operation_hash is not None
    assert failure.error.code == "output_validation_failed"
    assert "Infinity" not in completed.stdout
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output.dq-stage-*"))


def test_artifact_request_is_optional_and_output_conflicts_are_typed(tmp_path: Path) -> None:
    request = _request(artifacts=False)
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    first = _success(_command(request_path, output_dir))
    before_conflict = {
        path.name: path.read_bytes() for path in output_dir.iterdir() if path.is_file()
    }
    conflict = _failure(_command(request_path, output_dir))

    assert first.manifest.artifacts == ()
    assert sorted(path.name for path in output_dir.iterdir()) == [
        "input.json",
        "manifest.json",
        "result.json",
    ]
    assert conflict.operation_hash == request.operation_hash
    assert conflict.error.code == "output_exists"
    assert conflict.error.details == {}
    after_conflict = {
        path.name: path.read_bytes() for path in output_dir.iterdir() if path.is_file()
    }
    assert after_conflict == before_conflict


def test_existing_empty_output_directory_is_refused(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "already-exists"
    output_dir.mkdir()

    failure = _failure(_command(request_path, output_dir))

    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "output_exists"
    assert list(output_dir.iterdir()) == []


def test_output_target_refusal_precedes_component_domain_call(tmp_path: Path) -> None:
    request = _request(
        input_data={
            "prices": [100.0, 0.0],
            "price_kind": "adjusted",
        }
    )
    request_path = tmp_path / "would-refuse.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "already-exists"
    output_dir.mkdir()

    failure = _failure(_command(request_path, output_dir))

    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "output_exists"
    assert list(output_dir.iterdir()) == []


def test_preexisting_output_symlink_is_refused_without_touching_target(tmp_path: Path) -> None:
    request = _request(
        input_data={
            "prices": [100.0, 0.0],
            "price_kind": "adjusted",
        }
    )
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    outside_target = tmp_path / "outside-target"
    outside_target.mkdir()
    sentinel = outside_target / "sentinel.txt"
    sentinel.write_text("must remain unchanged\n", encoding="utf-8")
    output_dir = tmp_path / "output-link"
    output_dir.symlink_to(outside_target, target_is_directory=True)

    failure = _failure(_command(request_path, output_dir))

    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "invalid_output_directory"
    assert sentinel.read_text(encoding="utf-8") == "must remain unchanged\n"
    assert sorted(path.name for path in outside_target.iterdir()) == ["sentinel.txt"]


def test_atomic_publish_refuses_a_raced_empty_destination(tmp_path: Path) -> None:
    completed = _python_probe(
        """
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import run_component as runner

root = Path(sys.argv[2])
staging = root / "staging"
staging.mkdir()
(staging / "result.json").write_text("{}", encoding="utf-8")
destination = root / "output"
component = runner.ComponentRef(
    id="dq.market_data.simple_return",
    version="0.1.0",
    subject_hash="a" * 64,
)
atomic_publish = runner._atomic_rename_no_replace

def race(source, target):
    target.mkdir()
    atomic_publish(source, target)

runner._atomic_rename_no_replace = race
try:
    runner._publish_staged_directory(
        staging,
        destination,
        ("result.json",),
        component=component,
    )
except runner.AdapterError as error:
    code = error.code.value
else:
    code = "succeeded"

print(json.dumps({
    "code": code,
    "destination_exists": destination.is_dir(),
    "destination_members": sorted(path.name for path in destination.iterdir()),
    "staging_exists": staging.is_dir(),
    "staging_members": sorted(path.name for path in staging.iterdir()),
}, sort_keys=True))
""",
        str(tmp_path),
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "code": "output_exists",
        "destination_exists": True,
        "destination_members": [],
        "staging_exists": True,
        "staging_members": ["result.json"],
    }


def test_argument_errors_are_typed_protocol_failures() -> None:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "--not-a-runner-option"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    failure = _failure(completed)
    assert failure.operation_hash is None
    assert failure.error.code == "invalid_operation_request"
    assert completed.stdout == ""
    assert "usage:" not in completed.stderr


def test_generic_runner_contains_no_component_specific_dispatch() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "dq.market_data.simple_return" not in source
    assert "earnings_comparison" not in source
    assert "Dashboard" not in source
    assert "ViewBundle" not in source
    assert '"--overwrite"' not in source


def test_dq_error_taxonomy_and_failure_sanitization_are_fail_closed() -> None:
    completed = _python_probe(
        """
import json
import sys
sys.path.insert(0, sys.argv[1])
import run_component as runner
from defined_quant.types import (
    AmbiguousInput,
    ComponentContractError,
    ComponentLoadError,
    ContractEvaluationError,
    DQError,
    DomainError,
    UnsupportedScope,
)

cases = {
    "ambiguous": AmbiguousInput("ambiguous"),
    "unsupported": UnsupportedScope("unsupported"),
    "domain": DomainError("domain"),
    "contract": ComponentContractError("contract"),
    "load": ComponentLoadError("load"),
    "contract_evaluation": ContractEvaluationError("evaluation"),
    "unknown": DQError("unknown"),
}
payload = {
    name: runner._mapped_dq_error(error, component=None).code.value
    for name, error in cases.items()
}
private_locations = [
    "/Users/example/private/catalog",
    r"C:\\Users\\example\\private\\catalog",
    r"\\\\server\\private\\catalog",
    "file:///Users/example/private/catalog",
]
integrity_errors = {
    "contract": ComponentContractError,
    "load": ComponentLoadError,
    "contract_evaluation": ContractEvaluationError,
}
payload["integrity_details"] = {
    name: runner._mapped_dq_error(
        error_type(
            " | ".join(private_locations),
            details={"nested": {"paths": private_locations}},
        ),
        component=None,
    ).details["component_error"]
    for name, error_type in integrity_errors.items()
}
unsafe = runner.AdapterError(
    runner.OperationErrorCode.OPERATION_FAILED,
    "unsafe",
    details={"nan": float("nan"), "object": object()},
)
failure = runner._failure(unsafe, request=None)
payload["failure"] = failure.model_dump(mode="json")
print(json.dumps(payload, sort_keys=True, allow_nan=False))
"""
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ambiguous"] == "component_refused"
    assert payload["unsupported"] == "component_refused"
    assert payload["domain"] == "component_refused"
    assert payload["contract"] == "component_contract_error"
    assert payload["load"] == "component_contract_error"
    assert payload["contract_evaluation"] == "component_contract_error"
    assert payload["unknown"] == "component_execution_failed"
    assert payload["integrity_details"] == {
        "contract": {"code": "component_contract_error"},
        "contract_evaluation": {"code": "contract_evaluation_error"},
        "load": {"code": "component_load_error"},
    }
    assert payload["failure"]["status"] == "failed"
    assert payload["failure"]["error"]["details"] == {
        "unavailable_type": "dict"
    }
    assert "NaN" not in completed.stdout
    assert "/Users/example/private/catalog" not in completed.stdout
    assert "C:\\\\Users\\\\example\\\\private\\\\catalog" not in completed.stdout
    assert "\\\\\\\\server\\\\private\\\\catalog" not in completed.stdout
    assert "file:///Users/example/private/catalog" not in completed.stdout
    assert completed.stderr == ""


def test_component_stdout_is_captured_and_never_leaks() -> None:
    completed = _python_probe(
        """
import sys
sys.path.insert(0, sys.argv[1])
import run_component as runner

record = runner.component_record("dq.market_data.simple_return")
reference = runner._component_ref(record)
request = runner.OperationRequest(
    component=reference,
    input={"prices": [100.0, 101.0], "price_kind": "adjusted"},
    provenance=runner.CallerProvenance(
        source_kind="synthetic",
        interpretation_method="caller_structured",
        label="Synthetic noise test.",
    ),
)
inputs_model, _ = runner.component_models(record)
validated = inputs_model.model_validate(request.input)
original = runner.load_component(record)

def noisy_component(**kwargs):
    print("CAPTURED_SECRET_OUTPUT")
    return original(**kwargs)

runner.load_component = lambda selected: noisy_component
try:
    runner._execute(record, request, validated)
except Exception as error:
    runner._print_model(runner._failure(error, request=request), stream=sys.stdout)
"""
    )

    assert completed.returncode == 0, completed.stderr
    failure = OperationFailure.model_validate_json(completed.stdout)
    assert failure.error.code == "component_contract_error"
    assert failure.error.details == {
        "stderr_emitted": False,
        "stdout_emitted": True,
    }
    assert "CAPTURED_SECRET_OUTPUT" not in completed.stdout
    assert completed.stderr == ""


def test_invalid_catalog_component_returns_typed_contract_error(tmp_path: Path) -> None:
    component_dir = tmp_path / "categories" / "invalid" / "broken"
    component_dir.mkdir(parents=True)
    (component_dir / "contract.yaml").write_text(
        json.dumps(
            {
                "id": "dq.invalid.broken",
                "category": "invalid",
                "slug": "broken",
                "version": "0.1.0",
                "callable": "defined_quant.invalid.broken:broken",
                "guidance": {},
                "display": {},
                "depends_on": [],
            }
        ),
        encoding="utf-8",
    )
    (component_dir / "component.py").write_text(
        "def broken():\n    raise AssertionError('must not execute')\n",
        encoding="utf-8",
    )
    request = OperationRequest(
        component=ComponentRef(
            id="dq.invalid.broken",
            version="0.1.0",
            subject_hash="0" * 64,
        ),
        input={},
        provenance=CallerProvenance(
            source_kind="synthetic",
            interpretation_method="caller_structured",
            label="Synthetic invalid-catalog fixture.",
        ),
    )
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    completed = _command(
        request_path,
        output_dir,
        "--catalog-root",
        str(tmp_path / "categories"),
    )

    failure = _failure(completed)
    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "component_contract_error"
    assert failure.error.details["component_error"]["code"] == "component_load_error"
    assert str(tmp_path) not in completed.stderr
    assert not output_dir.exists()


def test_malformed_catalog_contract_never_leaks_host_path(tmp_path: Path) -> None:
    component_dir = tmp_path / "categories" / "invalid" / "broken"
    component_dir.mkdir(parents=True)
    (component_dir / "contract.yaml").write_text("id: [unterminated", encoding="utf-8")
    request = OperationRequest(
        component=ComponentRef(
            id="dq.invalid.broken",
            version="0.1.0",
            subject_hash="0" * 64,
        ),
        input={},
        provenance=CallerProvenance(
            source_kind="synthetic",
            interpretation_method="caller_structured",
            label="Synthetic malformed-catalog fixture.",
        ),
    )
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    completed = _command(
        request_path,
        output_dir,
        "--catalog-root",
        str(tmp_path / "categories"),
    )

    failure = _failure(completed)
    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "component_contract_error"
    assert failure.error.details == {
        "component_error": {"code": "component_contract_error"}
    }
    assert str(tmp_path) not in completed.stdout
    assert str(tmp_path) not in completed.stderr
    assert not output_dir.exists()


def test_non_semver_catalog_identity_returns_typed_contract_error(tmp_path: Path) -> None:
    source_dir = PROJECT_ROOT / "categories/market_data/simple_return"
    component_dir = tmp_path / "categories/market_data/simple_return"
    component_dir.mkdir(parents=True)
    contract = json.loads((source_dir / "contract.yaml").read_text(encoding="utf-8"))
    contract["version"] = "draft"
    (component_dir / "contract.yaml").write_text(json.dumps(contract), encoding="utf-8")
    (component_dir / "component.py").write_text(
        (source_dir / "component.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    request = OperationRequest(
        component=ComponentRef(
            id="dq.market_data.simple_return",
            version="0.1.0",
            subject_hash="0" * 64,
        ),
        input=_input(),
        provenance=CallerProvenance(
            source_kind="synthetic",
            interpretation_method="caller_structured",
            label="Synthetic invalid-version fixture.",
        ),
    )
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    output_dir = tmp_path / "output"

    completed = _command(
        request_path,
        output_dir,
        "--catalog-root",
        str(tmp_path / "categories"),
    )

    failure = _failure(completed)
    assert failure.operation_hash == request.operation_hash
    assert failure.error.code == "component_contract_error"
    assert "errors" in failure.error.details
    assert str(tmp_path) not in completed.stderr
    assert not output_dir.exists()
