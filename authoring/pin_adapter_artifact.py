#!/usr/bin/env python3
"""Build the DQ-native adapter wheel and regenerate its registry artifact pins."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Any, cast
from zipfile import BadZipFile, ZipFile, ZipInfo

import yaml  # type: ignore[import-untyped]
from defined_quant_protocol import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PROJECT = Path("adapters/dq_native")
REGISTRY_ROOT = Path("registry")
MANIFEST_PATH = Path("artifacts/defined_quant_adapter_dq_native/manifest.json")
MANIFEST_REFERENCE = "registry/artifacts/defined_quant_adapter_dq_native/manifest.json"
ADAPTER_DISTRIBUTION = "defined-quant-adapter-dq-native"
ADAPTER_PACKAGE = "defined_quant_adapter_dq_native"
ADAPTER_ENTRY_POINT_GROUP = "defined_quant.adapters"
MANIFEST_DOMAIN = "registry.artifact.installed_distribution_manifest"
HASHING_DECLARATION = {
    "algorithm": "sha256",
    "canonicalization": "defined_quant_canonical_json_v1",
    "domain": MANIFEST_DOMAIN,
}

_SAFE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactPinError(RuntimeError):
    """A safe authoring failure that leaves every registry pin unchanged."""


@dataclass(frozen=True, slots=True)
class WheelArtifact:
    distribution: str
    version: str
    manifest: dict[str, object]
    artifact_hash: str


@dataclass(frozen=True, slots=True)
class PinResult:
    artifact_hash: str
    implementation_count: int
    evidence_count: int
    member_count: int
    changed_files: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _Output:
    path: Path
    original: bytes
    rendered: bytes


def _fail(message: str) -> ArtifactPinError:
    return ArtifactPinError(message)


def _path_order(path: Path, root: Path) -> bytes:
    return path.relative_to(root).as_posix().encode("utf-8")


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _fail(f"{name} must be a string-keyed object")
    return cast(dict[str, Any], dict(value))


def _yaml_mapping(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        encoded = path.read_bytes()
        text = encoded.decode("utf-8")
        value = yaml.safe_load(text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise _fail(f"cannot read authored record {path}") from exc
    return encoded, _mapping(value, name=str(path))


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{name} must be a non-empty string")
    return value


def _required_hash(value: object, *, name: str) -> str:
    result = _required_string(value, name=name)
    if _SHA256_PATTERN.fullmatch(result) is None:
        raise _fail(f"{name} must be a lowercase SHA-256 digest")
    return result


def _record_identity(value: Mapping[str, object], *, name: str) -> tuple[str, str]:
    return (
        _required_string(value.get("id"), name=f"{name} id"),
        _required_string(value.get("version"), name=f"{name} version"),
    )


def _reference(value: object, *, name: str) -> tuple[str, str]:
    reference = _mapping(value, name=name)
    return _record_identity(reference, name=name)


def _canonical_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _canonical_member_path(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1000
        or "\\" in value
        or "\0" in value
        or value.startswith("/")
    ):
        raise _fail(f"{name} is not a safe wheel member path")
    path = PurePosixPath(value)
    if str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise _fail(f"{name} is not a canonical wheel member path")
    return value


def _zip_files(archive: ZipFile) -> dict[str, ZipInfo]:
    files: dict[str, ZipInfo] = {}
    for info in archive.infolist():
        if info.is_dir():
            _canonical_member_path(info.filename.removesuffix("/"), name="wheel directory")
            continue
        path = _canonical_member_path(info.filename, name="wheel member")
        if path in files:
            raise _fail("wheel member paths are not unique")
        mode = info.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise _fail("wheel members cannot be symbolic links")
        files[path] = info
    if not files:
        raise _fail("adapter wheel has no file members")
    return files


def _one_dist_info_file(
    files: Mapping[str, ZipInfo],
    *,
    filename: str,
) -> tuple[str, ZipInfo]:
    matches: list[tuple[str, ZipInfo]] = []
    for path, info in files.items():
        parts = PurePosixPath(path).parts
        if len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] == filename:
            matches.append((parts[0], info))
    if len(matches) != 1:
        raise _fail(f"adapter wheel must contain exactly one .dist-info/{filename}")
    return matches[0]


def _entry_points(encoded: bytes) -> list[dict[str, str]]:
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise _fail("adapter wheel entry-point metadata is malformed") from exc

    result: list[dict[str, str]] = []
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if not section or "[" in section or "]" in section:
                raise _fail("adapter wheel entry-point metadata is malformed")
            continue
        name, separator, target = line.partition("=")
        if section is None or not separator:
            raise _fail("adapter wheel entry-point metadata is malformed")
        if section != ADAPTER_ENTRY_POINT_GROUP:
            continue
        name = name.strip()
        target = target.strip()
        if _SAFE_ID_PATTERN.fullmatch(name) is None:
            raise _fail("adapter wheel contains an invalid dispatch key")
        if not target or len(target) > 512:
            raise _fail("adapter wheel contains an invalid entry-point target")
        result.append(
            {
                "group": ADAPTER_ENTRY_POINT_GROUP,
                "name": name,
                "value": target,
            }
        )
    if not result:
        raise _fail("adapter wheel has no Defined Quant adapter entry points")
    if len({item["name"] for item in result}) != len(result):
        raise _fail("adapter wheel dispatch keys are not unique")
    return sorted(
        result,
        key=lambda item: (
            item["group"].encode("utf-8"),
            item["name"].encode("utf-8"),
            item["value"].encode("utf-8"),
        ),
    )


def _record_members(
    archive: ZipFile,
    files: Mapping[str, ZipInfo],
    *,
    dist_info: str,
    record: ZipInfo,
) -> list[dict[str, str]]:
    try:
        rows = csv.reader(io.StringIO(archive.read(record).decode("utf-8"), newline=""))
        record_rows: dict[str, tuple[str, str]] = {}
        for row in rows:
            if len(row) != 3:
                raise _fail("adapter wheel RECORD is malformed")
            path = _canonical_member_path(row[0], name="wheel RECORD member")
            if path in record_rows:
                raise _fail("adapter wheel RECORD paths are not unique")
            if row[2]:
                try:
                    int(row[2])
                except ValueError as exc:
                    raise _fail("adapter wheel RECORD contains an invalid member size") from exc
            record_rows[path] = (row[1], row[2])
    except UnicodeError as exc:
        raise _fail("adapter wheel RECORD is not UTF-8") from exc

    declared_resources = {
        path
        for path in record_rows
        if not any(part.endswith(".dist-info") for part in PurePosixPath(path).parts)
    }
    archived_resources = {
        path
        for path in files
        if not any(part.endswith(".dist-info") for part in PurePosixPath(path).parts)
    }
    if declared_resources != archived_resources:
        raise _fail("adapter wheel resources and RECORD declarations do not match")
    blank_cache_bytecode = {
        path
        for path in declared_resources
        if _is_installer_generated_cache_bytecode(path, *record_rows[path])
    }
    if blank_cache_bytecode:
        raise _fail("built wheel contains cache bytecode without RECORD integrity metadata")
    if not declared_resources:
        raise _fail("adapter wheel has no package resource members")
    if any(not path.startswith(f"{ADAPTER_PACKAGE}/") for path in declared_resources):
        raise _fail("adapter wheel contains a resource outside the DQ-native package")
    if f"{dist_info}/RECORD" not in record_rows:
        raise _fail("adapter wheel RECORD does not declare itself")

    return [
        {
            "path": path,
            "sha256": hashlib.sha256(archive.read(files[path])).hexdigest(),
        }
        for path in sorted(declared_resources, key=lambda item: item.encode("utf-8"))
    ]


def _is_installer_generated_cache_bytecode(
    path: str,
    digest: str,
    size: str,
) -> bool:
    parsed = PurePosixPath(path)
    return (
        parsed.parent.name == "__pycache__"
        and parsed.suffix == ".pyc"
        and not digest
        and not size
    )


def manifest_from_wheel(
    wheel: Path,
    *,
    expected_version: str,
) -> WheelArtifact:
    """Derive the exact installed-distribution manifest from one built wheel."""

    try:
        with ZipFile(wheel) as archive:
            files = _zip_files(archive)
            dist_info, metadata_info = _one_dist_info_file(files, filename="METADATA")
            wheel_dist_info, wheel_info = _one_dist_info_file(files, filename="WHEEL")
            entry_dist_info, entry_info = _one_dist_info_file(
                files,
                filename="entry_points.txt",
            )
            record_dist_info, record_info = _one_dist_info_file(files, filename="RECORD")
            if {dist_info, wheel_dist_info, entry_dist_info, record_dist_info} != {dist_info}:
                raise _fail("adapter wheel contains inconsistent .dist-info directories")

            metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_info))
            distribution = metadata.get("Name")
            version = metadata.get("Version")
            if not isinstance(distribution, str) or not isinstance(version, str):
                raise _fail("adapter wheel identity metadata is incomplete")
            if _canonical_distribution_name(distribution) != _canonical_distribution_name(
                ADAPTER_DISTRIBUTION
            ):
                raise _fail("built wheel is not the DQ-native adapter distribution")
            if version != expected_version:
                raise _fail("built wheel version does not match the adapter project version")

            try:
                wheel_metadata = archive.read(wheel_info).decode("utf-8").replace("\r\n", "\n")
            except UnicodeError as exc:
                raise _fail("adapter WHEEL metadata is not UTF-8") from exc
            if "Tag: py3-none-any" not in wheel_metadata.splitlines():
                raise _fail("DQ-native adapter wheel must be platform-neutral")

            entry_points = _entry_points(archive.read(entry_info))
            members = _record_members(
                archive,
                files,
                dist_info=dist_info,
                record=record_info,
            )
    except (BadZipFile, KeyError, OSError) as exc:
        raise _fail("cannot read the built DQ-native adapter wheel") from exc

    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "installed_distribution_manifest",
        "distribution": ADAPTER_DISTRIBUTION,
        "version": version,
        "hashing": dict(HASHING_DECLARATION),
        "entry_points": entry_points,
        "members": members,
    }
    return WheelArtifact(
        distribution=ADAPTER_DISTRIBUTION,
        version=version,
        manifest=manifest,
        artifact_hash=canonical_hash(manifest, domain=MANIFEST_DOMAIN),
    )


def _project_version(project: Path) -> str:
    try:
        value = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise _fail("cannot read the DQ-native adapter project metadata") from exc
    project_metadata = _mapping(value.get("project"), name="adapter project metadata")
    distribution = _required_string(
        project_metadata.get("name"),
        name="adapter project name",
    )
    if distribution != ADAPTER_DISTRIBUTION:
        raise _fail("adapter project name is outside the bounded pin target")
    return _required_string(project_metadata.get("version"), name="adapter project version")


def _build_wheel(root: Path, project: Path, output: Path) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise _fail("uv is unavailable")
    result = subprocess.run(
        (
            uv,
            "build",
            "--project",
            os.fspath(project),
            "--wheel",
            "--out-dir",
            os.fspath(output),
        ),
        cwd=root,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown build failure"
        raise _fail(f"DQ-native adapter wheel build failed: {detail}")
    wheels = tuple(sorted(output.glob("*.whl"), key=lambda item: item.name.encode("utf-8")))
    if len(wheels) != 1:
        raise _fail("adapter build must produce exactly one wheel")
    return wheels[0]


def _render_hash(path: Path, original: bytes, old: str, new: str) -> bytes:
    if old == new:
        return original
    old_bytes = old.encode("ascii")
    if original.count(old_bytes) != 1:
        raise _fail(f"{path} does not contain exactly one validated artifact hash")
    return original.replace(old_bytes, new.encode("ascii"), 1)


def _implementation_outputs(
    registry: Path,
    artifact: WheelArtifact,
) -> tuple[list[_Output], set[tuple[str, str]], set[str]]:
    adapters: dict[tuple[str, str], str] = {}
    for path in sorted(
        registry.glob("adapters/**/*.yaml"),
        key=lambda item: _path_order(item, registry),
    ):
        _encoded, value = _yaml_mapping(path)
        if value.get("distribution") != artifact.distribution:
            continue
        identity = _record_identity(value, name=f"adapter {path}")
        dispatch_key = _required_string(
            value.get("dispatch_key"),
            name=f"adapter {path} dispatch_key",
        )
        if identity in adapters:
            raise _fail("adapter identities for the pinned distribution are not unique")
        adapters[identity] = dispatch_key
    if not adapters:
        raise _fail("registry has no adapters for the built distribution")

    wheel_dispatch_keys = {
        entry["name"]
        for entry in cast(list[dict[str, str]], artifact.manifest["entry_points"])
    }
    registered_dispatch_keys = set(adapters.values())
    if wheel_dispatch_keys != registered_dispatch_keys:
        raise _fail("built wheel entry points do not match registered adapter dispatch keys")

    outputs: list[_Output] = []
    implementations: set[tuple[str, str]] = set()
    used_adapters: set[tuple[str, str]] = set()
    for path in sorted(
        registry.glob("implementations/**/*.yaml"),
        key=lambda item: _path_order(item, registry),
    ):
        encoded, value = _yaml_mapping(path)
        adapter = _reference(value.get("adapter"), name=f"implementation {path} adapter")
        artifact_value = _mapping(
            value.get("artifact"),
            name=f"implementation {path} artifact",
        )
        targets_distribution = artifact_value.get("distribution") == artifact.distribution
        targets_adapter = adapter in adapters
        if not targets_distribution and not targets_adapter:
            continue
        if not targets_distribution or not targets_adapter:
            raise _fail(f"implementation {path} has a contradictory adapter artifact binding")
        if artifact_value.get("version") != artifact.version:
            raise _fail(f"implementation {path} artifact version does not match the built wheel")
        old_hash = _required_hash(
            artifact_value.get("artifact_hash"),
            name=f"implementation {path} artifact_hash",
        )
        identity = _record_identity(value, name=f"implementation {path}")
        if identity in implementations:
            raise _fail("implementation identities for the pinned distribution are not unique")
        implementations.add(identity)
        used_adapters.add(adapter)
        outputs.append(
            _Output(
                path=path,
                original=encoded,
                rendered=_render_hash(path, encoded, old_hash, artifact.artifact_hash),
            )
        )
    if not implementations:
        raise _fail("registry has no implementations for the built distribution")
    if used_adapters != set(adapters):
        raise _fail("every registered adapter dispatch must be bound by an implementation")
    return outputs, implementations, wheel_dispatch_keys


def _evidence_outputs(
    registry: Path,
    artifact: WheelArtifact,
    implementations: set[tuple[str, str]],
) -> list[_Output]:
    outputs: list[_Output] = []
    subjects: set[tuple[str, str]] = set()
    for path in sorted(
        registry.glob("evidence/implementations/**/*.yaml"),
        key=lambda item: _path_order(item, registry),
    ):
        encoded, value = _yaml_mapping(path)
        subject_value = _mapping(
            value.get("subject"),
            name=f"implementation evidence {path} subject",
        )
        subject = _record_identity(subject_value, name=f"implementation evidence {path} subject")
        artifact_value = _mapping(
            value.get("artifact"),
            name=f"implementation evidence {path} artifact",
        )
        targets_distribution = artifact_value.get("distribution") == artifact.distribution
        targets_subject = subject in implementations
        if not targets_distribution and not targets_subject:
            continue
        if not targets_distribution or not targets_subject:
            raise _fail(f"implementation evidence {path} has a contradictory artifact subject")
        if subject_value.get("kind") != "implementation":
            raise _fail(f"implementation evidence {path} has the wrong subject kind")
        if artifact_value.get("kind") != "installed_distribution_manifest":
            raise _fail(f"implementation evidence {path} has the wrong artifact kind")
        if artifact_value.get("version") != artifact.version:
            raise _fail(f"implementation evidence {path} version does not match the built wheel")
        if artifact_value.get("manifest") != MANIFEST_REFERENCE:
            raise _fail(f"implementation evidence {path} points to the wrong artifact manifest")
        hash_value = _mapping(
            artifact_value.get("hash"),
            name=f"implementation evidence {path} artifact hash",
        )
        if {
            "algorithm": hash_value.get("algorithm"),
            "canonicalization": hash_value.get("canonicalization"),
            "domain": hash_value.get("domain"),
        } != HASHING_DECLARATION:
            raise _fail(f"implementation evidence {path} has the wrong hashing declaration")
        old_hash = _required_hash(
            hash_value.get("value"),
            name=f"implementation evidence {path} artifact hash value",
        )
        if subject in subjects:
            raise _fail(
                "implementation evidence subjects for the pinned distribution are not unique"
            )
        subjects.add(subject)
        outputs.append(
            _Output(
                path=path,
                original=encoded,
                rendered=_render_hash(path, encoded, old_hash, artifact.artifact_hash),
            )
        )
    if subjects != implementations:
        raise _fail("pinned implementations and implementation evidence are not one-to-one")
    return outputs


def _manifest_output(registry: Path, artifact: WheelArtifact) -> _Output:
    path = registry / MANIFEST_PATH
    try:
        original = path.read_bytes()
    except OSError as exc:
        raise _fail("canonical DQ-native artifact manifest is missing") from exc
    rendered = (
        json.dumps(
            artifact.manifest,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return _Output(path=path, original=original, rendered=rendered)


def _stage(path: Path, content: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _replace_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _write_outputs(outputs: Sequence[_Output]) -> tuple[Path, ...]:
    changed = tuple(item for item in outputs if item.original != item.rendered)
    if not changed:
        return ()
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for item in changed:
            staged[item.path] = _stage(item.path, item.rendered)
            backups[item.path] = _stage(item.path, item.original)
        for item in changed:
            if item.path.read_bytes() != item.original:
                raise _fail(f"authored record changed during pin generation: {item.path}")
            _replace_file(staged[item.path], item.path)
            replaced.append(item.path)
    except BaseException:
        for path in reversed(replaced):
            backup = backups.get(path)
            if backup is not None and backup.exists():
                _replace_file(backup, path)
        raise
    finally:
        for temporary in (*staged.values(), *backups.values()):
            temporary.unlink(missing_ok=True)
    return tuple(item.path for item in changed)


def pin_adapter_artifact(root: Path = ROOT, *, check: bool = False) -> PinResult:
    """Build, preflight, and either check or rewrite all DQ-native artifact pins."""

    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise _fail("project root does not exist") from exc
    project = root / ADAPTER_PROJECT
    registry = root / REGISTRY_ROOT
    if not project.is_dir() or not registry.is_dir():
        raise _fail("project root lacks the bounded DQ-native adapter or registry")
    version = _project_version(project)
    with tempfile.TemporaryDirectory(prefix="defined-quant-adapter-pin-") as raw_output:
        wheel = _build_wheel(root, project, Path(raw_output))
        artifact = manifest_from_wheel(wheel, expected_version=version)

    implementation_outputs, implementations, _dispatch_keys = _implementation_outputs(
        registry,
        artifact,
    )
    evidence_outputs = _evidence_outputs(registry, artifact, implementations)
    # The old manifest remains the trust commit point until every dependent pin is published.
    # An interrupted update therefore fails closed; the manifest is replaced atomically last.
    outputs = (*implementation_outputs, *evidence_outputs, _manifest_output(registry, artifact))
    stale = tuple(item.path for item in outputs if item.original != item.rendered)
    if check and stale:
        relative = ", ".join(path.relative_to(root).as_posix() for path in stale)
        raise _fail(f"adapter artifact pins are stale: {relative}")
    changed = () if check else _write_outputs(outputs)
    members = cast(list[dict[str, str]], artifact.manifest["members"])
    return PinResult(
        artifact_hash=artifact.artifact_hash,
        implementation_count=len(implementation_outputs),
        evidence_count=len(evidence_outputs),
        member_count=len(members),
        changed_files=changed,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root containing adapters/dq_native and registry",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="build and verify the committed pins without changing files",
    )
    args = parser.parse_args(argv)
    try:
        result = pin_adapter_artifact(args.root, check=args.check)
    except ArtifactPinError as exc:
        parser.exit(1, f"error: {exc}\n")
    action = "Verified" if args.check else "Pinned"
    print(
        f"{action} {result.implementation_count} implementations and "
        f"{result.evidence_count} evidence records to {result.member_count} wheel members: "
        f"{result.artifact_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
