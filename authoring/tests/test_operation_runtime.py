"""Phase-1 parity and publication tests for the shared operation runtime."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import defined_quant.operation_runtime as operation_runtime
import pytest
from defined_quant.local_host_platform import (
    SecureFilesystemError,
    SecureFilesystemErrorCode,
    local_host_platform,
)
from defined_quant.operation_runtime import (
    OperationRuntimeError,
    execute_operation,
    verify_operation_bundle,
)
from defined_quant.service import DefinedQuantService
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    OperationErrorCode,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    SvgArtifactRequest,
)
from defined_quant_protocol.operation import portable_member_key

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
WINDOWS_DEVICE_NAMES = (
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
)


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


def _run_stdin_cli(
    request: OperationRequest,
    output_dir: Path,
    *,
    line_ending: bytes = b"\n",
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    request_bytes = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--request",
            "-",
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        env=environment,
        input=request_bytes + line_ending,
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
    if sys.platform != "win32":
        for directory in (runtime_dir, service_dir, cli_dir):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
            assert all(
                stat.S_IMODE(member.stat().st_mode) == 0o600
                for member in directory.iterdir()
            )

    for member, expected in fixture["surfaces"].items():
        content = completed.stdout if member == "stdout" else (cli_dir / member).read_bytes()
        _assert_surface(content, expected)

    manifest = OperationSuccess.model_validate_json(completed.stdout).manifest
    verify_operation_bundle(cli_dir, manifest)
    assert manifest.runner.model_dump() == {"name": "use_defined_quant", "version": "0.1.0"}
    for member in (manifest.input, manifest.result, *manifest.artifacts):
        assert hashlib.sha256((cli_dir / member.path).read_bytes()).hexdigest() == member.sha256


def test_internal_worker_output_validation_does_not_require_a_home_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_home(_cls: type[Path]) -> Path:
        raise RuntimeError

    monkeypatch.setattr(Path, "home", classmethod(missing_home))
    assert operation_runtime.prepare_output_directory(
        tmp_path / "worker-bundle",
        component=None,
    ) == tmp_path / "worker-bundle"


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


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_binary_stdin_cli_matches_frozen_golden_bytes(
    tmp_path: Path,
    line_ending: bytes,
) -> None:
    fixture = _fixture()["typed_success"]
    completed = _run_stdin_cli(
        _typed_request(),
        tmp_path / ("lf" if line_ending == b"\n" else "crlf"),
        line_ending=line_ending,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert b"\r" not in completed.stdout
    _assert_surface(completed.stdout, fixture["surfaces"]["stdout"])


def test_binary_stdin_cli_rejects_invalid_utf8_and_ctrl_z_as_data(
    tmp_path: Path,
) -> None:
    arguments = [
        sys.executable,
        str(RUNNER),
        "--request",
        "-",
        "--output-dir",
        str(tmp_path / "output"),
    ]
    invalid = subprocess.run(
        arguments,
        cwd=ROOT,
        input=b'{"schema_version":1,"label":"\xff"}\n',
        check=False,
        capture_output=True,
    )
    request_bytes = json.dumps(
        _typed_request().model_dump(mode="json"),
        separators=(",", ":"),
    ).encode("utf-8")
    ctrl_z = subprocess.run(
        arguments,
        cwd=ROOT,
        input=request_bytes + b"\x1a",
        check=False,
        capture_output=True,
    )

    assert invalid.returncode == ctrl_z.returncode == 2
    assert invalid.stdout == ctrl_z.stdout == b""
    invalid_failure = OperationFailure.model_validate_json(invalid.stderr)
    ctrl_z_failure = OperationFailure.model_validate_json(ctrl_z.stderr)
    assert invalid_failure.error.code is OperationErrorCode.INVALID_JSON
    assert invalid_failure.error.message == "Operation request is not valid UTF-8."
    assert ctrl_z_failure.error.code is OperationErrorCode.INVALID_JSON
    assert ctrl_z_failure.error.message == "Operation request is not valid JSON."
    assert not (tmp_path / "output").exists()


def test_cli_bytes_ignore_locale_codepage_and_unicode_input(
    tmp_path: Path,
) -> None:
    request = _typed_request().model_copy(
        update={
            "provenance": CallerProvenance(
                source_kind="synthetic",
                interpretation_method="caller_structured",
                label="Zażółć gęślą jaźń — €.",
            )
        }
    )
    baseline_dir = tmp_path / "baseline"
    altered_dir = tmp_path / "altered"
    baseline = _run_stdin_cli(request, baseline_dir)
    environment = dict(os.environ)
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONIOENCODING": "cp1252:strict",
            "PYTHONUTF8": "0",
        }
    )
    altered = _run_stdin_cli(request, altered_dir, environment=environment)

    assert baseline.returncode == altered.returncode == 0
    assert baseline.stderr == altered.stderr == b""
    assert altered.stdout == baseline.stdout
    assert _bundle_bytes(altered_dir) == _bundle_bytes(baseline_dir)
    assert b"\r" not in altered.stdout


@pytest.mark.parametrize("path_kind", ["unicode", "long"])
def test_cli_supports_unicode_and_long_local_paths(
    tmp_path: Path,
    path_kind: str,
) -> None:
    parent = tmp_path / "zażółć-gęślą-jaźń"
    if path_kind == "long":
        while len(os.fspath(parent)) < 280:
            parent /= "long-cli-path-segment"
    secure = local_host_platform().secure_filesystem
    secure.ensure_directory_path(parent)
    request_path = parent / "żądanie.json"
    output_dir = parent / "wynik"
    secure.create_private_file(
        request_path,
        json.dumps(_typed_request().model_dump(mode="json")).encode("utf-8"),
    )
    completed = subprocess.run(
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

    assert completed.returncode == 0
    assert completed.stderr == b""
    _assert_surface(
        completed.stdout,
        _fixture()["typed_success"]["surfaces"]["stdout"],
    )
    assert secure.read_regular_file(
        output_dir / "manifest.json",
        maximum_bytes=2 * 1024 * 1024,
    )


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


def test_bundle_verifier_refuses_a_case_colliding_disk_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "original"
    result = execute_operation(_typed_request(), output_dir=output_dir)
    assert isinstance(result, OperationSuccess)
    real_iterdir = Path.iterdir

    def aliased_iterdir(path: Path) -> Any:
        entries = tuple(real_iterdir(path))
        if path == output_dir:
            return iter((*entries, path / "RESULT.JSON"))
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", aliased_iterdir)
    with pytest.raises(OperationRuntimeError) as raised:
        verify_operation_bundle(output_dir, result.manifest)
    assert raised.value.code.value == "artifact_write_failed"
    assert raised.value.message == (
        "The staged operation bundle failed deterministic reconciliation."
    )


@pytest.mark.parametrize(
    "members",
    [
        ("result.json", "RESULT.JSON"),
        ("manifest.json", "MANIFEST.JSON"),
        ("CON.txt",),
        ("result.json:stream",),
        ("result.json ",),
        ("result\\json",),
        ("PROGRA~1.json",),
    ],
)
def test_runtime_publication_refuses_nonportable_or_colliding_members(
    members: tuple[str, ...],
) -> None:
    with pytest.raises(OperationRuntimeError) as raised:
        operation_runtime._assert_flat_members(members)
    assert raised.value.code.value == "artifact_write_failed"
    assert raised.value.message == "The runner generated a non-flat output member path."


@pytest.mark.parametrize("device_name", WINDOWS_DEVICE_NAMES)
def test_numeric_artifact_prefix_protects_every_reserved_safe_id(device_name: str) -> None:
    member = f"01-{operation_runtime._safe_stem(device_name)}.svg"

    assert portable_member_key(member) == member


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

    def fail_render(_spec: Any) -> str:
        raise OSError("synthetic artifact failure")

    monkeypatch.setattr(operation_runtime, "render_svg", fail_render)
    result = execute_operation(_typed_request(), output_dir=output_dir)

    assert isinstance(result, OperationFailure)
    assert result.error.code.value == "artifact_write_failed"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(".defined-quant-operation-stage-*"))


def test_publication_race_leaves_no_partial_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    secure_filesystem = operation_runtime.local_host_platform().secure_filesystem
    atomic_publish = secure_filesystem.publish_directory_no_replace

    def race(staging: Path, destination: Path) -> None:
        destination.mkdir()
        atomic_publish(staging, destination)

    monkeypatch.setattr(secure_filesystem, "publish_directory_no_replace", race)
    result = execute_operation(_typed_request(), output_dir=output_dir)

    assert isinstance(result, OperationFailure)
    assert result.error.code.value == "output_exists"
    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []
    assert not list(tmp_path.glob(".defined-quant-operation-stage-*"))


def test_no_atomic_rename_support_fails_closed_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"

    def unavailable(_staging: Path, _destination: Path) -> None:
        raise SecureFilesystemError(SecureFilesystemErrorCode.CAPABILITY_UNAVAILABLE)

    secure_filesystem = operation_runtime.local_host_platform().secure_filesystem
    monkeypatch.setattr(secure_filesystem, "publish_directory_no_replace", unavailable)
    result = execute_operation(_typed_request(), output_dir=output_dir)

    assert isinstance(result, OperationFailure)
    assert result.error.code.value == "artifact_write_failed"
    assert not output_dir.exists()
    assert not list(tmp_path.glob(".defined-quant-operation-stage-*"))


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
