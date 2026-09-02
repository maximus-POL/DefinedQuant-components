from __future__ import annotations

import base64
import hashlib
import json
import py_compile
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from importlib import metadata
from importlib.machinery import ModuleSpec
from pathlib import Path
from zipfile import ZipFile

import pytest
from defined_quant.adapter_artifacts import (
    ArtifactAttestation,
    ArtifactVerificationError,
    reverify_installed_artifact,
    verify_installed_artifact,
)
from defined_quant.adapter_discovery import discover_installed_adapters
from defined_quant.registry import load_registry
from defined_quant_protocol import AdapterSpec, ImplementationSpec, canonical_hash

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PROJECT = ROOT / "adapters/dq_native"
MANIFEST_PATH = ROOT / "registry/artifacts/defined_quant_adapter_dq_native/manifest.json"
MANIFEST_DOMAIN = "registry.artifact.installed_distribution_manifest"
ADAPTER_IMPORT_PREFIX = "defined_quant_adapter_dq_native"
EXPECTED_RESOURCES = {
    "defined_quant_adapter_dq_native/__init__.py",
    "defined_quant_adapter_dq_native/_boundary.py",
    "defined_quant_adapter_dq_native/drawdown/__init__.py",
    "defined_quant_adapter_dq_native/drawdown/adapter.py",
    "defined_quant_adapter_dq_native/drawdown/kernel.py",
    "defined_quant_adapter_dq_native/log_return/__init__.py",
    "defined_quant_adapter_dq_native/log_return/adapter.py",
    "defined_quant_adapter_dq_native/log_return/kernel.py",
    "defined_quant_adapter_dq_native/monthly_calendar_matrix/__init__.py",
    "defined_quant_adapter_dq_native/monthly_calendar_matrix/adapter.py",
    "defined_quant_adapter_dq_native/monthly_calendar_matrix/kernel.py",
    "defined_quant_adapter_dq_native/py.typed",
    "defined_quant_adapter_dq_native/rebased_price_index/__init__.py",
    "defined_quant_adapter_dq_native/rebased_price_index/adapter.py",
    "defined_quant_adapter_dq_native/rebased_price_index/kernel.py",
    "defined_quant_adapter_dq_native/simple_return/__init__.py",
    "defined_quant_adapter_dq_native/simple_return/adapter.py",
    "defined_quant_adapter_dq_native/simple_return/kernel.py",
    "defined_quant_adapter_dq_native/statistics/__init__.py",
    "defined_quant_adapter_dq_native/statistics/kernel.py",
    "defined_quant_adapter_dq_native/statistics/rolling_sample_standard_deviation.py",
    "defined_quant_adapter_dq_native/statistics/sample_standard_deviation.py",
    "defined_quant_adapter_dq_native/statistics/square_root_annualize.py",
    "defined_quant_adapter_dq_native/statistics/square_root_annualize_series.py",
}


@dataclass(frozen=True, slots=True)
class ExtractedWheel:
    root: Path
    distribution: metadata.Distribution


class _RejectAdapterImports:
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: object | None = None,
    ) -> ModuleSpec | None:
        del path, target
        if fullname == ADAPTER_IMPORT_PREFIX or fullname.startswith(f"{ADAPTER_IMPORT_PREFIX}."):
            raise AssertionError("artifact verification imported adapter code")
        return None


@pytest.fixture(scope="module")
def built_adapter_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    uv = shutil.which("uv")
    assert uv is not None, "the repository test runner requires uv"
    output = tmp_path_factory.mktemp("dq_native_wheel")
    subprocess.run(
        (
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(output),
            "--project",
            str(ADAPTER_PROJECT),
        ),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _extract_distribution(wheel: Path, destination: Path) -> ExtractedWheel:
    with ZipFile(wheel) as archive:
        archive.extractall(destination)
    dist_info = tuple(destination.glob("*.dist-info"))
    assert len(dist_info) == 1
    return ExtractedWheel(
        root=destination,
        distribution=metadata.PathDistribution(dist_info[0]),
    )


def _adapter() -> AdapterSpec:
    registry = load_registry(root=ROOT / "registry").as_protocol_registry()
    matches = tuple(item for item in registry.adapters if item.id == "dq_native.simple_return")
    assert len(matches) == 1
    return matches[0]


def _implementation() -> ImplementationSpec:
    registry = load_registry(root=ROOT / "registry").as_protocol_registry()
    matches = tuple(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )
    assert len(matches) == 1
    return matches[0]


def _manifest() -> dict[str, object]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@contextmanager
def _adapter_imports_forbidden() -> Iterator[None]:
    previous = {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == ADAPTER_IMPORT_PREFIX or name.startswith(f"{ADAPTER_IMPORT_PREFIX}.")
    }
    for name in previous:
        del sys.modules[name]
    blocker = _RejectAdapterImports()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        for name in tuple(sys.modules):
            if name == ADAPTER_IMPORT_PREFIX or name.startswith(f"{ADAPTER_IMPORT_PREFIX}."):
                del sys.modules[name]
        sys.modules.update(previous)


def test_installed_manifest_describes_only_wheel_metadata_and_resources() -> None:
    manifest = _manifest()
    implementation = _implementation()

    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "installed_distribution_manifest"
    assert manifest["distribution"] == "defined-quant-adapter-dq-native"
    assert manifest["version"] == "1.0.0"
    assert manifest["hashing"] == {
        "algorithm": "sha256",
        "canonicalization": "defined_quant_canonical_json_v1",
        "domain": MANIFEST_DOMAIN,
    }
    assert [item["name"] for item in manifest["entry_points"]] == [
        "dq_native_drawdown",
        "dq_native_log_return",
        "dq_native_monthly_calendar_matrix",
        "dq_native_rebased_price_index",
        "dq_native_rolling_sample_standard_deviation",
        "dq_native_sample_standard_deviation",
        "dq_native_simple_return",
        "dq_native_square_root_annualize",
        "dq_native_square_root_annualize_series",
    ]
    members = manifest["members"]
    assert isinstance(members, list)
    ordered_paths = [item["path"] for item in members]
    assert ordered_paths == sorted(ordered_paths, key=lambda item: item.encode("utf-8"))
    assert set(ordered_paths) == EXPECTED_RESOURCES
    assert all(
        isinstance(item["sha256"], str)
        and len(item["sha256"]) == 64
        and item["sha256"] == item["sha256"].lower()
        for item in members
    )
    paths = set(ordered_paths)
    assert all("README" not in path for path in paths)
    assert all("pyproject.toml" not in path for path in paths)
    assert all(".dist-info/" not in path for path in paths)
    assert canonical_hash(manifest, domain=MANIFEST_DOMAIN) == (
        implementation.artifact.artifact_hash
    )


def test_verifier_attests_an_isolated_built_wheel_without_importing_adapter_code(
    built_adapter_wheel: Path,
    tmp_path: Path,
) -> None:
    extracted = _extract_distribution(built_adapter_wheel, tmp_path)
    adapter = _adapter()
    implementation = _implementation()
    installed = discover_installed_adapters(distributions=(extracted.distribution,))

    with _adapter_imports_forbidden():
        attestation = verify_installed_artifact(
            installed=installed,
            adapter=adapter,
            artifact=implementation.artifact,
            expected_manifest=_manifest(),
        )
        assert not any(
            name == ADAPTER_IMPORT_PREFIX or name.startswith(f"{ADAPTER_IMPORT_PREFIX}.")
            for name in sys.modules
        )

    assert isinstance(attestation, ArtifactAttestation)
    assert attestation.adapter == adapter.ref
    assert attestation.artifact == implementation.artifact
    assert attestation.manifest_hash == implementation.artifact.artifact_hash

    with _adapter_imports_forbidden():
        reverify_installed_artifact(
            attestation=attestation,
            installed=installed,
            adapter=adapter,
            artifact=implementation.artifact,
        )


def test_verifier_ignores_installer_generated_bytecode_record_members(
    built_adapter_wheel: Path,
    tmp_path: Path,
) -> None:
    extracted = _extract_distribution(built_adapter_wheel, tmp_path)
    source = extracted.root / "defined_quant_adapter_dq_native/__init__.py"
    bytecode_result = py_compile.compile(str(source), doraise=True)
    assert bytecode_result is not None
    bytecode = Path(bytecode_result)
    assert bytecode.parent.name == "__pycache__"
    assert bytecode.suffix == ".pyc"
    assert bytecode.is_relative_to(extracted.root)

    dist_info = tuple(extracted.root.glob("*.dist-info"))
    assert len(dist_info) == 1
    record = dist_info[0] / "RECORD"
    record.write_text(
        record.read_text(encoding="utf-8")
        + f"{bytecode.relative_to(extracted.root).as_posix()},,\n",
        encoding="utf-8",
    )
    distribution = metadata.PathDistribution(dist_info[0])
    files = distribution.files
    assert files is not None
    assert bytecode.relative_to(extracted.root).as_posix() in {
        item.as_posix() for item in files
    }
    installed = discover_installed_adapters(distributions=(distribution,))

    with _adapter_imports_forbidden():
        attestation = verify_installed_artifact(
            installed=installed,
            adapter=_adapter(),
            artifact=_implementation().artifact,
            expected_manifest=_manifest(),
        )

    assert isinstance(attestation, ArtifactAttestation)

    with _adapter_imports_forbidden():
        reverify_installed_artifact(
            attestation=attestation,
            installed=installed,
            adapter=_adapter(),
            artifact=_implementation().artifact,
        )


def test_verifier_refuses_wheel_declared_cache_bytecode(
    built_adapter_wheel: Path,
    tmp_path: Path,
) -> None:
    extracted = _extract_distribution(built_adapter_wheel, tmp_path)
    source = extracted.root / "defined_quant_adapter_dq_native/__init__.py"
    bytecode_result = py_compile.compile(str(source), doraise=True)
    assert bytecode_result is not None
    bytecode = Path(bytecode_result)
    relative_bytecode = bytecode.relative_to(extracted.root).as_posix()
    encoded_digest = base64.urlsafe_b64encode(
        hashlib.sha256(bytecode.read_bytes()).digest()
    ).rstrip(b"=").decode("ascii")

    dist_info = tuple(extracted.root.glob("*.dist-info"))
    assert len(dist_info) == 1
    record = dist_info[0] / "RECORD"
    record.write_text(
        record.read_text(encoding="utf-8")
        + f"{relative_bytecode},sha256={encoded_digest},{bytecode.stat().st_size}\n",
        encoding="utf-8",
    )
    distribution = metadata.PathDistribution(dist_info[0])
    installed = discover_installed_adapters(distributions=(distribution,))

    with _adapter_imports_forbidden(), pytest.raises(ArtifactVerificationError):
        verify_installed_artifact(
            installed=installed,
            adapter=_adapter(),
            artifact=_implementation().artifact,
            expected_manifest=_manifest(),
        )


def test_verifier_refuses_tampered_installed_resource_without_importing(
    built_adapter_wheel: Path,
    tmp_path: Path,
) -> None:
    extracted = _extract_distribution(built_adapter_wheel, tmp_path)
    target = extracted.root / "defined_quant_adapter_dq_native/simple_return/adapter.py"
    target.write_bytes(target.read_bytes() + b"\n# modified after installation\n")
    installed = discover_installed_adapters(distributions=(extracted.distribution,))

    with _adapter_imports_forbidden(), pytest.raises(ArtifactVerificationError):
        verify_installed_artifact(
            installed=installed,
            adapter=_adapter(),
            artifact=_implementation().artifact,
            expected_manifest=_manifest(),
        )


def test_verifier_refuses_a_caller_modified_entry_point_manifest(
    built_adapter_wheel: Path,
    tmp_path: Path,
) -> None:
    extracted = _extract_distribution(built_adapter_wheel, tmp_path)
    installed = discover_installed_adapters(distributions=(extracted.distribution,))
    manifest = deepcopy(_manifest())
    entry_points = manifest["entry_points"]
    assert isinstance(entry_points, list)
    entry_point = entry_points[0]
    assert isinstance(entry_point, dict)
    entry_point["value"] = "untrusted.module:execute"

    with _adapter_imports_forbidden(), pytest.raises(ArtifactVerificationError):
        verify_installed_artifact(
            installed=installed,
            adapter=_adapter(),
            artifact=_implementation().artifact,
            expected_manifest=manifest,
        )


def test_artifact_attestation_cannot_be_asserted_by_a_caller() -> None:
    with pytest.raises(TypeError):
        ArtifactAttestation(artifact=_implementation().artifact)
