"""Verification of exact installed adapter-distribution source artifacts.

The verifier reads installed package metadata and wheel-declared source/resource bytes without
importing adapter code. Installer-generated ``__pycache__`` bytecode is derived local state, not a
source resource in the artifact manifest. Only this module can construct
:class:`ArtifactAttestation`; installation or a caller-provided claim is never sufficient.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from defined_quant.adapter_discovery import (
    ADAPTER_ENTRY_POINT_GROUP,
    InstalledAdapterDiscovery,
)
from defined_quant_protocol import AdapterRef, AdapterSpec, ArtifactPin, canonical_hash
from defined_quant_protocol.canonical import canonical_json_bytes

INSTALLED_DISTRIBUTION_MANIFEST_DOMAIN = "registry.artifact.installed_distribution_manifest"

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "distribution",
        "version",
        "hashing",
        "entry_points",
        "members",
    }
)
_HASHING = {
    "algorithm": "sha256",
    "canonicalization": "defined_quant_canonical_json_v1",
    "domain": INSTALLED_DISTRIBUTION_MANIFEST_DOMAIN,
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ArtifactVerificationError(RuntimeError):
    """Safe failure indicating that an installed artifact could not be verified."""


class ArtifactAttestation:
    """Opaque proof that the host recomputed one exact installed artifact pin.

    The public constructor is deliberately unavailable.  A host receives an instance only from
    :func:`verify_installed_artifact` after exact metadata, entry-point, resource-member, and hash
    verification.
    """

    __slots__ = ("_adapter", "_artifact", "_expected_manifest_bytes", "_manifest_hash")
    _adapter: AdapterRef
    _artifact: ArtifactPin
    _expected_manifest_bytes: bytes
    _manifest_hash: str

    def __new__(cls, *args: object, **kwargs: object) -> ArtifactAttestation:
        del args, kwargs
        raise TypeError("ArtifactAttestation is produced only by artifact verification")

    @classmethod
    def _from_verification(
        cls,
        *,
        adapter: AdapterRef,
        artifact: ArtifactPin,
        expected_manifest_bytes: bytes,
        manifest_hash: str,
    ) -> ArtifactAttestation:
        value = object.__new__(cls)
        object.__setattr__(value, "_adapter", adapter)
        object.__setattr__(value, "_artifact", artifact)
        object.__setattr__(value, "_expected_manifest_bytes", expected_manifest_bytes)
        object.__setattr__(value, "_manifest_hash", manifest_hash)
        return value

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("ArtifactAttestation is immutable")

    @property
    def adapter(self) -> AdapterRef:
        return self._adapter

    @property
    def artifact(self) -> ArtifactPin:
        return self._artifact

    @property
    def manifest_hash(self) -> str:
        return self._manifest_hash

    def __repr__(self) -> str:
        return (
            "ArtifactAttestation("
            f"adapter={self.adapter.id!r}, distribution={self.artifact.distribution!r}, "
            f"version={self.artifact.version!r}, manifest_hash={self.manifest_hash!r})"
        )


def _fail(message: str) -> ArtifactVerificationError:
    return ArtifactVerificationError(message)


def _path_text(value: Any) -> str:
    as_posix = getattr(value, "as_posix", None)
    if callable(as_posix):
        result = as_posix()
    else:
        result = str(value)
    if not isinstance(result, str):
        raise _fail("installed distribution contains an invalid resource path")
    return result


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise _fail("artifact manifest member path is invalid")
    if "\\" in value or value.startswith("/"):
        raise _fail("artifact manifest member path must be canonical and relative")
    path = PurePosixPath(value)
    if str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise _fail("artifact manifest member path is not canonical")
    return value


def _validate_member_path(value: object) -> str:
    value = _validate_relative_path(value)
    path = PurePosixPath(value)
    if any(part.endswith(".dist-info") for part in path.parts):
        raise _fail("artifact manifest members cannot bind installer metadata files")
    return value


def _validate_expected_manifest(
    value: Mapping[str, object],
    *,
    adapter: AdapterSpec,
    artifact: ArtifactPin,
) -> dict[str, object]:
    if set(value) != _MANIFEST_KEYS:
        raise _fail("installed-distribution manifest is not a closed record")
    if value.get("schema_version") != 1:
        raise _fail("installed-distribution manifest schema version is unsupported")
    if value.get("kind") != "installed_distribution_manifest":
        raise _fail("artifact manifest kind is not installed_distribution_manifest")
    if value.get("distribution") != adapter.distribution:
        raise _fail("artifact manifest distribution does not match the adapter")
    if artifact.distribution != adapter.distribution:
        raise _fail("implementation artifact distribution does not match the adapter")
    if value.get("version") != artifact.version:
        raise _fail("artifact manifest version does not match the implementation pin")
    if value.get("hashing") != _HASHING:
        raise _fail("artifact manifest hashing declaration is not canonical")

    raw_entry_points = value.get("entry_points")
    if not isinstance(raw_entry_points, list) or not raw_entry_points:
        raise _fail("artifact manifest requires adapter entry-point metadata")
    entry_points: list[dict[str, str]] = []
    for raw in raw_entry_points:
        if not isinstance(raw, dict) or set(raw) != {"group", "name", "value"}:
            raise _fail("artifact manifest entry point is not a closed record")
        group = raw.get("group")
        name = raw.get("name")
        target = raw.get("value")
        if group != ADAPTER_ENTRY_POINT_GROUP:
            raise _fail("artifact manifest contains a non-adapter entry-point group")
        if not isinstance(name, str) or _SAFE_ID_PATTERN.fullmatch(name) is None:
            raise _fail("artifact manifest entry-point name is invalid")
        if not isinstance(target, str) or not target or len(target) > 512:
            raise _fail("artifact manifest entry-point target is invalid")
        entry_points.append({"group": group, "name": name, "value": target})
    if entry_points != sorted(
        entry_points,
        key=lambda item: (
            item["group"].encode("utf-8"),
            item["name"].encode("utf-8"),
            item["value"].encode("utf-8"),
        ),
    ) or len({(item["group"], item["name"]) for item in entry_points}) != len(entry_points):
        raise _fail("artifact manifest entry points must be unique and canonical")
    if not any(item["name"] == adapter.dispatch_key for item in entry_points):
        raise _fail("artifact manifest does not bind the registered dispatch key")

    raw_members = value.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        raise _fail("artifact manifest requires installed resource members")
    members: list[dict[str, str]] = []
    for raw in raw_members:
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256"}:
            raise _fail("artifact manifest member is not a closed record")
        path = _validate_member_path(raw.get("path"))
        digest = raw.get("sha256")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise _fail("artifact manifest member digest is invalid")
        members.append({"path": path, "sha256": digest})
    if members != sorted(members, key=lambda item: item["path"].encode("utf-8")) or len(
        {item["path"] for item in members}
    ) != len(members):
        raise _fail("artifact manifest members must be unique and canonical")

    normalized: dict[str, object] = {
        "schema_version": 1,
        "kind": "installed_distribution_manifest",
        "distribution": adapter.distribution,
        "version": artifact.version,
        "hashing": dict(_HASHING),
        "entry_points": entry_points,
        "members": members,
    }
    try:
        canonical_json_bytes(normalized)
    except (TypeError, ValueError) as exc:
        raise _fail("artifact manifest is not canonical JSON") from exc
    return normalized


def _installed_entry_points(distribution: Any) -> list[dict[str, str]]:
    try:
        entry_points = distribution.entry_points
    except (AttributeError, TypeError) as exc:
        raise _fail("installed distribution entry-point metadata is unavailable") from exc
    result: list[dict[str, str]] = []
    for entry_point in entry_points:
        try:
            group = entry_point.group
        except (AttributeError, TypeError) as exc:
            raise _fail("installed entry-point metadata is malformed") from exc
        if group != ADAPTER_ENTRY_POINT_GROUP:
            continue
        try:
            name = entry_point.name
            target = entry_point.value
        except (AttributeError, TypeError) as exc:
            raise _fail("installed adapter entry-point metadata is malformed") from exc
        if (
            not isinstance(name, str)
            or _SAFE_ID_PATTERN.fullmatch(name) is None
            or not isinstance(target, str)
            or not target
            or len(target) > 512
        ):
            raise _fail("installed adapter entry-point metadata is invalid")
        result.append({"group": group, "name": name, "value": target})
    return sorted(
        result,
        key=lambda item: (
            item["group"].encode("utf-8"),
            item["name"].encode("utf-8"),
            item["value"].encode("utf-8"),
        ),
    )


def _installed_members(distribution: Any) -> list[dict[str, str]]:
    try:
        raw_files = distribution.files
    except (AttributeError, TypeError) as exc:
        raise _fail("installed distribution resource metadata is unavailable") from exc
    if raw_files is None:
        raise _fail("installed distribution has no verifiable resource manifest")

    files: dict[str, Any] = {}
    for raw in raw_files:
        path = _validate_relative_path(_path_text(raw))
        parsed = PurePosixPath(path)
        if any(part.endswith(".dist-info") for part in parsed.parts):
            continue
        canonical = _validate_member_path(path)
        if _is_installer_generated_cache_bytecode(parsed, raw):
            continue
        if canonical in files:
            raise _fail("installed distribution resource paths are not unique")
        files[canonical] = raw
    if not files:
        raise _fail("installed distribution has no package resource members")

    try:
        base = Path(distribution.locate_file("")).resolve(strict=True)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _fail("installed distribution root cannot be verified") from exc
    if not base.is_dir():
        raise _fail("installed distribution root is not a directory")
    members: list[dict[str, str]] = []
    for path, raw in sorted(files.items(), key=lambda item: item[0].encode("utf-8")):
        try:
            located = Path(distribution.locate_file(raw))
            if located.is_symlink():
                raise _fail("installed distribution resource cannot be a symbolic link")
            target = located.resolve(strict=True)
            if not target.is_file() or os.path.commonpath((str(base), str(target))) != str(base):
                raise _fail("installed distribution resource escapes its installation root")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except ArtifactVerificationError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise _fail("installed distribution resource bytes cannot be verified") from exc
        members.append({"path": path, "sha256": digest})
    return members


def _is_installer_generated_cache_bytecode(path: PurePosixPath, raw: Any) -> bool:
    """Identify conventional bytecode rows added to RECORD by an installer."""

    if path.parent.name != "__pycache__" or path.suffix != ".pyc":
        return False
    try:
        return raw.hash is None and raw.size is None
    except (AttributeError, TypeError):
        return False


def verify_installed_artifact(
    *,
    installed: InstalledAdapterDiscovery,
    adapter: AdapterSpec,
    artifact: ArtifactPin,
    expected_manifest: Mapping[str, object],
) -> ArtifactAttestation:
    """Recompute an installed manifest and attest it only when every exact binding matches."""

    expected = _validate_expected_manifest(
        expected_manifest,
        adapter=adapter,
        artifact=artifact,
    )
    try:
        distribution, entry_point = installed._installed_match(adapter, artifact)
    except LookupError as exc:
        raise _fail("adapter is not one exact installed distribution match") from exc

    try:
        installed_name = distribution.metadata.get("Name")
        installed_version = distribution.version
    except (AttributeError, KeyError, TypeError) as exc:
        raise _fail("installed distribution identity metadata is unavailable") from exc
    canonical_name = re.sub(r"[-_.]+", "-", str(installed_name)).casefold()
    expected_name = re.sub(r"[-_.]+", "-", adapter.distribution).casefold()
    if canonical_name != expected_name or installed_version != artifact.version:
        raise _fail("installed distribution identity does not match the implementation pin")
    try:
        if entry_point.name != adapter.dispatch_key:
            raise _fail("installed entry point does not match the adapter dispatch key")
    except (AttributeError, TypeError) as exc:
        raise _fail("installed entry-point identity is unavailable") from exc

    actual: dict[str, object] = {
        "schema_version": 1,
        "kind": "installed_distribution_manifest",
        "distribution": adapter.distribution,
        "version": installed_version,
        "hashing": dict(_HASHING),
        "entry_points": _installed_entry_points(distribution),
        "members": _installed_members(distribution),
    }
    if actual != expected:
        raise _fail("installed distribution manifest does not match the registry manifest")
    manifest_hash = canonical_hash(actual, domain=INSTALLED_DISTRIBUTION_MANIFEST_DOMAIN)
    if manifest_hash != artifact.artifact_hash:
        raise _fail("installed distribution manifest does not match the registry artifact pin")
    return ArtifactAttestation._from_verification(
        adapter=adapter.ref,
        artifact=artifact,
        expected_manifest_bytes=json.dumps(
            expected,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        manifest_hash=manifest_hash,
    )


def reverify_installed_artifact(
    *,
    attestation: ArtifactAttestation,
    installed: InstalledAdapterDiscovery,
    adapter: AdapterSpec,
    artifact: ArtifactPin,
) -> None:
    """Recompute one attested artifact immediately before trusted code loading.

    The expected manifest is retained as opaque immutable canonical bytes inside the host-created
    attestation. Callers cannot replace it between availability construction and invocation.
    """

    if attestation.adapter != adapter.ref or attestation.artifact != artifact:
        raise _fail("artifact attestation does not bind the requested adapter")
    try:
        expected = json.loads(attestation._expected_manifest_bytes)
    except (TypeError, ValueError) as exc:
        raise _fail("artifact attestation manifest is unavailable") from exc
    if not isinstance(expected, dict):
        raise _fail("artifact attestation manifest is invalid")
    refreshed = verify_installed_artifact(
        installed=installed,
        adapter=adapter,
        artifact=artifact,
        expected_manifest=expected,
    )
    if refreshed.manifest_hash != attestation.manifest_hash:
        raise _fail("installed artifact changed after its original attestation")


__all__ = [
    "ArtifactAttestation",
    "ArtifactVerificationError",
    "INSTALLED_DISTRIBUTION_MANIFEST_DOMAIN",
    "reverify_installed_artifact",
    "verify_installed_artifact",
]
