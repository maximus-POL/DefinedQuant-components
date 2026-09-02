from __future__ import annotations

import hashlib
import shutil
from copy import deepcopy
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import yaml  # type: ignore[import-untyped]
from defined_quant_protocol import canonical_hash

import authoring.pin_adapter_artifact as pin_module
from authoring.pin_adapter_artifact import (
    ADAPTER_DISTRIBUTION,
    MANIFEST_DOMAIN,
    MANIFEST_PATH,
    ArtifactPinError,
    manifest_from_wheel,
    pin_adapter_artifact,
)

ROOT = Path(__file__).resolve().parents[2]


def _copy_pin_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "adapters/dq_native", project / "adapters/dq_native")
    shutil.copytree(ROOT / "registry", project / "registry")
    return project


def _registry_snapshot(project: Path) -> dict[str, bytes]:
    registry = project / "registry"
    return {
        path.relative_to(registry).as_posix(): path.read_bytes()
        for path in sorted(registry.rglob("*"))
        if path.is_file()
    }


def _pin_paths(project: Path) -> set[str]:
    registry = project / "registry"
    paths = {
        MANIFEST_PATH.as_posix(),
        *(
            path.relative_to(registry).as_posix()
            for path in registry.glob("implementations/dq_native/*.yaml")
        ),
        *(
            path.relative_to(registry).as_posix()
            for path in registry.glob("evidence/implementations/dq_native/*.yaml")
        ),
    }
    assert len(paths) == 19
    return paths


def _without_pin(path: str, encoded: bytes) -> object:
    value = yaml.safe_load(encoded.decode("utf-8"))
    assert isinstance(value, dict)
    detached = deepcopy(value)
    artifact = detached["artifact"]
    assert isinstance(artifact, dict)
    if path.startswith("implementations/"):
        artifact["artifact_hash"] = "<generated>"
    else:
        hash_value = artifact["hash"]
        assert isinstance(hash_value, dict)
        hash_value["value"] = "<generated>"
    return detached


def test_pin_script_builds_the_wheel_and_rewrites_every_bound_record(
    tmp_path: Path,
) -> None:
    project = _copy_pin_project(tmp_path)
    source = project / "adapters/dq_native/src/defined_quant_adapter_dq_native/_boundary.py"
    source.write_bytes(source.read_bytes() + b"\n# artifact pin regeneration fixture\n")
    before = _registry_snapshot(project)

    result = pin_adapter_artifact(project)
    after = _registry_snapshot(project)

    assert set(after) == set(before)
    changed = {path for path in before if before[path] != after[path]}
    assert changed == _pin_paths(project)
    assert {
        path.relative_to(project / "registry").as_posix() for path in result.changed_files
    } == changed
    assert result.changed_files[-1].relative_to(project / "registry") == MANIFEST_PATH
    assert result.implementation_count == 9
    assert result.evidence_count == 9
    assert result.member_count == 24

    manifest = yaml.safe_load(after[MANIFEST_PATH.as_posix()].decode("utf-8"))
    assert isinstance(manifest, dict)
    member = next(
        item
        for item in manifest["members"]
        if item["path"] == "defined_quant_adapter_dq_native/_boundary.py"
    )
    assert member["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["distribution"] == ADAPTER_DISTRIBUTION

    for path in sorted(changed - {MANIFEST_PATH.as_posix()}):
        prior = yaml.safe_load(before[path].decode("utf-8"))
        value = yaml.safe_load(after[path].decode("utf-8"))
        assert isinstance(prior, dict)
        assert isinstance(value, dict)
        artifact = value["artifact"]
        prior_artifact = prior["artifact"]
        assert isinstance(artifact, dict)
        assert isinstance(prior_artifact, dict)
        actual_hash = (
            artifact["artifact_hash"]
            if path.startswith("implementations/")
            else artifact["hash"]["value"]
        )
        prior_hash = (
            prior_artifact["artifact_hash"]
            if path.startswith("implementations/")
            else prior_artifact["hash"]["value"]
        )
        assert actual_hash == result.artifact_hash
        assert after[path] == before[path].replace(
            prior_hash.encode("ascii"),
            result.artifact_hash.encode("ascii"),
            1,
        )
        assert _without_pin(path, before[path]) == _without_pin(path, after[path])

    first = _registry_snapshot(project)
    repeated = pin_adapter_artifact(project)
    assert repeated.changed_files == ()
    assert _registry_snapshot(project) == first


def test_check_mode_reports_stale_pins_without_writing(tmp_path: Path) -> None:
    project = _copy_pin_project(tmp_path)
    source = project / "adapters/dq_native/src/defined_quant_adapter_dq_native/__init__.py"
    source.write_bytes(source.read_bytes() + b"\n# stale pin fixture\n")
    before = _registry_snapshot(project)

    with pytest.raises(ArtifactPinError, match="artifact pins are stale"):
        pin_adapter_artifact(project, check=True)

    assert _registry_snapshot(project) == before


def test_malformed_target_record_fails_before_any_pin_is_written(tmp_path: Path) -> None:
    project = _copy_pin_project(tmp_path)
    source = project / "adapters/dq_native/src/defined_quant_adapter_dq_native/__init__.py"
    source.write_bytes(source.read_bytes() + b"\n# changed wheel fixture\n")
    evidence_path = (
        project / "registry/evidence/implementations/dq_native/simple_return.yaml"
    )
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    assert isinstance(evidence, dict)
    evidence["artifact"]["hash"]["value"] = "not-a-digest"
    evidence_path.write_text(yaml.safe_dump(evidence, sort_keys=False), encoding="utf-8")
    before = _registry_snapshot(project)

    with pytest.raises(ArtifactPinError, match="lowercase SHA-256"):
        pin_adapter_artifact(project)

    assert _registry_snapshot(project) == before


def test_publication_failure_rolls_back_every_replaced_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _copy_pin_project(tmp_path)
    source = project / "adapters/dq_native/src/defined_quant_adapter_dq_native/__init__.py"
    source.write_bytes(source.read_bytes() + b"\n# rollback fixture\n")
    before = _registry_snapshot(project)
    registry = project / "registry"
    manifest = registry / MANIFEST_PATH
    real_replace = pin_module._replace_file
    at_commit_point: dict[str, bytes] | None = None

    def fail_at_manifest(source_path: Path, destination_path: Path) -> None:
        nonlocal at_commit_point
        if destination_path == manifest:
            at_commit_point = {
                relative: (registry / relative).read_bytes() for relative in before
            }
            raise OSError("synthetic publication failure")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(pin_module, "_replace_file", fail_at_manifest)

    with pytest.raises(OSError, match="synthetic publication failure"):
        pin_adapter_artifact(project)

    assert at_commit_point is not None
    assert at_commit_point[MANIFEST_PATH.as_posix()] == before[MANIFEST_PATH.as_posix()]
    for path in _pin_paths(project) - {MANIFEST_PATH.as_posix()}:
        assert at_commit_point[path] != before[path]
    for path in set(before) - _pin_paths(project):
        assert at_commit_point[path] == before[path]
    assert _registry_snapshot(project) == before
    assert not [
        path
        for path in (project / "registry").rglob("*")
        if path.is_file() and path.name.endswith(".tmp")
    ]


def test_wheel_projection_recomputes_record_member_hashes(tmp_path: Path) -> None:
    wheel = tmp_path / "defined_quant_adapter_dq_native-1.0.0-py3-none-any.whl"
    package_path = "defined_quant_adapter_dq_native/__init__.py"
    cache_path = (
        "defined_quant_adapter_dq_native/__pycache__/__init__.cpython-313.pyc"
    )
    dist_info = "defined_quant_adapter_dq_native-1.0.0.dist-info"
    package_bytes = b"synthetic adapter bytes\n"
    entry_points = (
        "[defined_quant.adapters]\n"
        "dq_native_simple_return = defined_quant_adapter_dq_native.simple_return:execute\n"
    )
    def write_wheel(*, include_blank_cache: bool, package_size: str = "999") -> None:
        record_paths = [
            f"{package_path},sha256=deliberately-wrong,{package_size}"
        ]
        if include_blank_cache:
            record_paths.append(f"{cache_path},,")
        record_paths.extend(
            (
                f"{dist_info}/METADATA,,",
                f"{dist_info}/WHEEL,,",
                f"{dist_info}/entry_points.txt,,",
                f"{dist_info}/RECORD,,",
                "",
            )
        )
        with ZipFile(wheel, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(package_path, package_bytes)
            if include_blank_cache:
                archive.writestr(cache_path, b"wheel-declared bytecode fixture")
            archive.writestr(
                f"{dist_info}/METADATA",
                "Metadata-Version: 2.4\n"
                f"Name: {ADAPTER_DISTRIBUTION}\n"
                "Version: 1.0.0\n",
            )
            archive.writestr(
                f"{dist_info}/WHEEL",
                "Wheel-Version: 1.0\nTag: py3-none-any\n",
            )
            archive.writestr(f"{dist_info}/entry_points.txt", entry_points)
            archive.writestr(f"{dist_info}/RECORD", "\n".join(record_paths))

    write_wheel(include_blank_cache=False, package_size="not-an-integer")
    with pytest.raises(ArtifactPinError, match="invalid member size"):
        manifest_from_wheel(wheel, expected_version="1.0.0")

    write_wheel(include_blank_cache=True)
    with pytest.raises(ArtifactPinError, match="cache bytecode without RECORD integrity"):
        manifest_from_wheel(wheel, expected_version="1.0.0")

    write_wheel(include_blank_cache=False)
    artifact = manifest_from_wheel(wheel, expected_version="1.0.0")

    assert artifact.manifest["members"] == [
        {
            "path": package_path,
            "sha256": hashlib.sha256(package_bytes).hexdigest(),
        }
    ]
    assert artifact.artifact_hash == canonical_hash(
        artifact.manifest,
        domain=MANIFEST_DOMAIN,
    )
