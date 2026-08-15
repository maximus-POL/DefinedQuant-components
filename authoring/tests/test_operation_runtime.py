"""Phase-1 parity and publication tests for the shared operation runtime."""

from __future__ import annotations

import ast
import errno
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import defined_quant.operation_runtime as operation_runtime
import pytest
from defined_quant.operation_runtime import (
    OperationRuntimeError,
    execute_operation,
    verify_operation_bundle,
)
from defined_quant.service import DefinedQuantService
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    SvgArtifactRequest,
)

ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT
    / ".agents"
    / "skills"
    / "use-defined-quant"
    / "scripts"
    / "run_component.py"
)
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "operation_runtime_phase0.v1.json"


def _fixture() -> dict[str, Any]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _typed_request(*, input_data: dict[str, Any] | None = None) -> OperationRequest:
    fixture = _fixture()
    case = fixture["typed_success"]
    return OperationRequest(
        component=ComponentRef.model_validate(fixture["component"]),
        input=input_data if input_data is not None else case["input"],
        provenance=CallerProvenance.model_validate(case["provenance"]),
        artifacts=SvgArtifactRequest.model_validate(case["artifacts"]),
    )


def _write_request(path: Path, request: OperationRequest) -> None:
    path.write_text(json.dumps(request.model_dump(mode="json")), encoding="utf-8")


def _run_typed_cli(
    request: OperationRequest,
    request_path: Path,
    output_dir: Path,
) -> subprocess.CompletedProcess[bytes]:
    _write_request(request_path, request)
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--request",
            str(request_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )


def _assert_surface(content: bytes, expected: dict[str, Any]) -> None:
    assert len(content) == expected["bytes"]
    assert hashlib.sha256(content).hexdigest() == expected["sha256"]


def _bundle_bytes(directory: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in directory.iterdir()
        if path.is_file()
    }


def _serialized_result(result: OperationSuccess | OperationFailure) -> bytes:
    return (
        json.dumps(
            result.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def test_runtime_service_and_typed_cli_match_frozen_phase0_bytes(
    tmp_path: Path,
) -> None:
    fixture = _fixture()["typed_success"]
    request = _typed_request()
    original_request = request.model_dump(mode="python")
    runtime_dir = tmp_path / "runtime"
    service_dir = tmp_path / "service"
    cli_dir = tmp_path / "cli"

    runtime_result = execute_operation(request, output_dir=runtime_dir)
    service_result = DefinedQuantService().execute_operation(
        request,
        output_dir=service_dir,
    )
    completed = _run_typed_cli(request, tmp_path / "request.json", cli_dir)

    assert isinstance(runtime_result, OperationSuccess)
    assert isinstance(service_result, OperationSuccess)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert request.model_dump(mode="python") == original_request
    assert request.operation_hash == fixture["operation_hash"]
    assert runtime_result == service_result
    runtime_response = _serialized_result(runtime_result)
    service_response = _serialized_result(service_result)
    assert runtime_response == service_response == completed.stdout
    assert _bundle_bytes(runtime_dir) == _bundle_bytes(service_dir) == _bundle_bytes(cli_dir)

    for member, expected in fixture["surfaces"].items():
        content = completed.stdout if member == "stdout" else (cli_dir / member).read_bytes()
        _assert_surface(content, expected)

    manifest = OperationSuccess.model_validate_json(completed.stdout).manifest
    verify_operation_bundle(cli_dir, manifest)
    assert manifest.runner.model_dump() == {"name": "use_defined_quant", "version": "0.1.0"}
    for member in (manifest.input, manifest.result, *manifest.artifacts):
        assert hashlib.sha256((cli_dir / member.path).read_bytes()).hexdigest() == member.sha256


def test_legacy_cli_matches_frozen_phase0_bytes(tmp_path: Path) -> None:
    fixture = _fixture()["legacy_success"]
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(fixture["input"]), encoding="utf-8")
    output_dir = tmp_path / "output"

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--component",
            "dq.market_data.simple_return",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    success = OperationSuccess.model_validate_json(completed.stdout)
    assert success.manifest.operation_hash == fixture["operation_hash"]
    assert success.manifest.request.provenance.model_dump(mode="json") == {
        "source_kind": "user_attachment",
        "interpretation_method": "caller_structured",
        "verification_status": "unverified",
        "label": "Legacy CLI component input; unmanaged.",
        "references": [],
        "content_sha256": None,
        "assumptions": [],
    }
    for member, expected in fixture["surfaces"].items():
        content = completed.stdout if member == "stdout" else (output_dir / member).read_bytes()
        _assert_surface(content, expected)


@pytest.mark.parametrize(
    "case_name",
    ["invalid_component_input", "ambiguous_input", "domain_refusal"],
)
def test_runtime_and_cli_failures_match_frozen_phase0_bytes(
    tmp_path: Path,
    case_name: str,
) -> None:
    case = _fixture()["failures"][case_name]
    request = _typed_request(input_data=case["input"])
    direct_dir = tmp_path / "direct"
    cli_dir = tmp_path / "cli"

    direct = execute_operation(request, output_dir=direct_dir)
    completed = _run_typed_cli(request, tmp_path / "request.json", cli_dir)

    assert isinstance(direct, OperationFailure)
    direct_bytes = _serialized_result(direct)
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == direct_bytes
    _assert_surface(completed.stderr, case["stderr"])
    assert direct.error.code.value == case["code"]
    if "component_code" in case:
        assert direct.error.details["component_error"]["code"] == case["component_code"]
    assert not direct_dir.exists()
    assert not cli_dir.exists()


@pytest.mark.parametrize("case_name", ["malformed_json", "duplicate_json_keys"])
def test_strict_cli_json_failures_match_frozen_phase0_bytes(
    tmp_path: Path,
    case_name: str,
) -> None:
    case = _fixture()["failures"][case_name]
    request_path = tmp_path / "request.json"
    request_path.write_text(case["request_bytes_utf8"], encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--request",
            str(request_path),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    _assert_surface(completed.stderr, case["stderr"])
    failure = OperationFailure.model_validate_json(completed.stderr)
    assert failure.error.code.value == "invalid_json"
    assert failure.operation_hash is None
    assert not (tmp_path / "output").exists()


def test_bundle_verifier_refuses_every_corrupted_surface(tmp_path: Path) -> None:
    output_dir = tmp_path / "original"
    result = execute_operation(_typed_request(), output_dir=output_dir)
    assert isinstance(result, OperationSuccess)
    manifest = result.manifest
    mutations = {
        "input": lambda directory: (directory / manifest.input.path).write_bytes(b"{}\n"),
        "result": lambda directory: (directory / manifest.result.path).write_bytes(b"{}\n"),
        "artifact": lambda directory: (directory / manifest.artifacts[0].path).write_bytes(
            b"<svg/>"
        ),
        "manifest": lambda directory: (directory / "manifest.json").write_bytes(b"{}\n"),
        "missing": lambda directory: (directory / manifest.result.path).unlink(),
        "extra": lambda directory: (directory / "extra.json").write_bytes(b"{}\n"),
    }

    for name, mutate in mutations.items():
        candidate = tmp_path / name
        shutil.copytree(output_dir, candidate)
        mutate(candidate)
        with pytest.raises(OperationRuntimeError) as raised:
            verify_operation_bundle(candidate, manifest)
        assert raised.value.code.value == "artifact_write_failed"


def test_runtime_revalidates_mutated_nested_request_data(tmp_path: Path) -> None:
    request = _typed_request()
    mutable_input: Any = request.input
    mutable_input["smuggled"] = object()

    result = execute_operation(request, output_dir=tmp_path / "output")

    assert isinstance(result, OperationFailure)
    assert result.error.code.value == "invalid_operation_request"
    assert result.operation_hash is None
    assert not (tmp_path / "output").exists()


def test_artifact_failure_cleans_staging_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"

    def fail_after_partial_write(_spec: Any, path: Path) -> None:
        path.write_bytes(b"partial")
        raise OSError(errno.EIO, "synthetic artifact failure")

    monkeypatch.setattr(operation_runtime, "save_svg", fail_after_partial_write)
    result = execute_operation(_typed_request(), output_dir=output_dir)

    assert isinstance(result, OperationFailure)
    assert result.error.code.value == "artifact_write_failed"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(".output.dq-stage-*"))


def test_publication_race_leaves_no_partial_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    atomic_rename = operation_runtime._atomic_rename_no_replace

    def race(staging: Path, destination: Path) -> None:
        destination.mkdir()
        atomic_rename(staging, destination)

    monkeypatch.setattr(operation_runtime, "_atomic_rename_no_replace", race)
    result = execute_operation(_typed_request(), output_dir=output_dir)

    assert isinstance(result, OperationFailure)
    assert result.error.code.value == "output_exists"
    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []
    assert not list(tmp_path.glob(".output.dq-stage-*"))


def test_no_atomic_rename_support_fails_closed_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"

    def unavailable(_staging: Path, _destination: Path) -> None:
        raise OSError(errno.ENOTSUP, "synthetic unsupported primitive")

    monkeypatch.setattr(operation_runtime, "_atomic_rename_no_replace", unavailable)
    result = execute_operation(_typed_request(), output_dir=output_dir)

    assert isinstance(result, OperationFailure)
    assert result.error.code.value == "artifact_write_failed"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(".output.dq-stage-*"))


def test_phase1_runtime_has_no_mcp_import_or_dependency() -> None:
    paths = [
        ROOT / "shared" / "operation_runtime.py",
        ROOT / "shared" / "service.py",
        RUNNER,
        ROOT / "authoring" / "evidence_runtime.py",
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_roots = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "mcp" not in imported_roots
        assert "fastmcp" not in imported_roots

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == ["pydantic>=2.7,<3"]
    assert 'name = "mcp"' not in (ROOT / "uv.lock").read_text(encoding="utf-8")

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import defined_quant.operation_runtime; "
                "from defined_quant.service import DefinedQuantService; "
                "assert DefinedQuantService; "
                "assert not any(name == 'mcp' or name.startswith('mcp.') "
                "for name in sys.modules); "
                "assert not any(name.endswith('.component') for name in sys.modules)"
            ),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_numerical_evidence_has_no_second_component_execution_path() -> None:
    path = ROOT / "authoring" / "evidence_runtime.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "defined_quant"
        for alias in node.names
    }

    assert "component_models" not in imported_names
    assert "load_component" not in imported_names
    assert "subject_hash" not in imported_names
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "defined_quant.operation_runtime"
        for node in ast.walk(tree)
    )


def test_no_consumer_imports_a_private_operation_runtime_name() -> None:
    assert "execute_resolved_operation" in operation_runtime.__all__

    runtime_path = ROOT / "shared" / "operation_runtime.py"
    offenders: list[str] = []
    for source_root in (
        ROOT / "shared",
        ROOT / "protocol",
        ROOT / "categories",
        ROOT / "authoring",
        ROOT / ".agents",
    ):
        for path in source_root.rglob("*.py"):
            if path == runtime_path:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module not in {
                    "defined_quant.operation_runtime",
                    "shared.operation_runtime",
                }:
                    continue
                for imported in node.names:
                    if imported.name.startswith("_"):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert offenders == []
