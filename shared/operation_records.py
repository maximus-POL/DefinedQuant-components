"""Reconcile Phase-1 portable bundles into immutable Phase-3 operation records."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from defined_quant.data_records import (
    DatasetPayloadV1,
    DatasetRecordV1,
    OperationBindingV1,
    OperationMemberV1,
    OperationRecordV1,
    pretty_json_bytes,
    strict_json_loads,
)
from defined_quant.dataset_registry import resolve_dataset_fields
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.local_host_platform import (
    SecureFilesystemError,
    SecureFilesystemErrorCode,
    local_host_platform,
)
from defined_quant_protocol import (
    FileDigest,
    OperationManifest,
    PortCompatibility,
    SvgArtifact,
    canonical_json_bytes,
)
from defined_quant_protocol.operation import portable_member_key
from pydantic import ValidationError

MAX_NORMALIZED_RESULT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_OPERATION_ARTIFACTS = 128
MAX_OPERATION_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_RESULT_TOP_LEVEL_FIELDS = 256


class OperationRecordError(HostFailureException):
    """Safe reconciliation failure that never carries member bytes or caller paths."""

    def __init__(
        self,
        code: HostFailureCode | str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        closed_code = HostFailureCode(code)
        safe_details = dict(details or {})
        super().__init__(closed_code, details=safe_details)
        self.details = safe_details


def _result_limit(name: str, maximum: int, actual: int) -> OperationRecordError:
    return OperationRecordError(
        HostFailureCode.RESULT_LIMIT_EXCEEDED,
        details={"limit_name": name, "maximum": maximum, "actual": actual},
    )


def _same_json(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError, OverflowError):
        return False


def _bounded_member_bytes(path: Path, *, maximum: int, limit_name: str) -> bytes:
    try:
        return local_host_platform().secure_filesystem.read_regular_file(
            path,
            maximum_bytes=maximum,
        )
    except SecureFilesystemError as exc:
        if exc.code is SecureFilesystemErrorCode.BYTE_LIMIT_EXCEEDED:
            raise _result_limit(
                limit_name,
                maximum,
                exc.actual if exc.actual is not None else maximum + 1,
            ) from exc
        raise OperationRecordError("record_corrupt") from exc


def _manifest_bytes(
    directory: Path, manifest: OperationManifest, *, content: bytes | None = None
) -> bytes:
    if content is None:
        content = _bounded_member_bytes(
            directory / "manifest.json",
            maximum=MAX_OPERATION_BUNDLE_BYTES,
            limit_name="operation_bundle_bytes",
        )
    if content != pretty_json_bytes(manifest.model_dump(mode="json")):
        raise OperationRecordError("record_corrupt")
    try:
        parsed = OperationManifest.model_validate(strict_json_loads(content))
    except (ValidationError, ValueError) as exc:
        raise OperationRecordError("record_corrupt") from exc
    if parsed != manifest:
        raise OperationRecordError("record_corrupt")
    return content


def _load_result(
    directory: Path, manifest: OperationManifest, *, content: bytes | None = None
) -> dict[str, Any]:
    if content is None:
        content = _bounded_member_bytes(
            directory / manifest.result.path,
            maximum=MAX_NORMALIZED_RESULT_BYTES,
            limit_name="normalized_result_member_bytes",
        )
    if len(content) > MAX_NORMALIZED_RESULT_BYTES:
        raise _result_limit(
            "normalized_result_member_bytes", MAX_NORMALIZED_RESULT_BYTES, len(content)
        )
    try:
        value = strict_json_loads(content)
    except (UnicodeDecodeError, ValueError) as exc:
        raise OperationRecordError("record_corrupt") from exc
    if not isinstance(value, dict):
        raise OperationRecordError("record_corrupt")
    if len(value) > MAX_RESULT_TOP_LEVEL_FIELDS:
        raise _result_limit(
            "result_top_level_fields", MAX_RESULT_TOP_LEVEL_FIELDS, len(value)
        )
    return value


def _validate_binding(
    binding: OperationBindingV1,
    manifest: OperationManifest,
    *,
    dataset_record: DatasetRecordV1 | None,
    dataset_payload: DatasetPayloadV1 | None,
) -> None:
    source = binding.source
    if source is None:
        if dataset_record is not None or dataset_payload is not None:
            raise OperationRecordError(
                "invalid_field_mapping", details={"fields": []}
            )
        resolved = dict(binding.literals)
    elif source.kind == "operation":
        raise OperationRecordError("unsupported_binding")
    else:
        if dataset_record is None or dataset_payload is None:
            raise OperationRecordError("internal_failure")
        if source.ref != dataset_record.reference:
            raise OperationRecordError("record_corrupt")
        try:
            dataset_record.validate_payload(dataset_payload)
        except ValueError as exc:
            raise OperationRecordError("record_corrupt") from exc
        try:
            resolved = resolve_dataset_fields(
                dataset_payload,
                source.mappings,
                literals=binding.literals,
            )
        except (ValueError, HostFailureException) as exc:
            raise OperationRecordError(
                "invalid_field_mapping", details={"fields": []}
            ) from exc
        record_provenance = dataset_record.source.provenance.model_dump(mode="json")
        manifest_provenance = manifest.request.provenance.model_dump(mode="json")
        if not _same_json(record_provenance, manifest_provenance):
            raise OperationRecordError("record_corrupt")
    if not _same_json(resolved, manifest.request.input):
        raise OperationRecordError("invalid_field_mapping", details={"fields": []})


def _derive_record(
    manifest: OperationManifest,
    directory: Path,
    binding: OperationBindingV1,
    *,
    compatibility: Sequence[PortCompatibility],
) -> OperationRecordV1:
    if len(manifest.artifacts) > MAX_OPERATION_ARTIFACTS:
        raise _result_limit(
            "operation_artifacts", MAX_OPERATION_ARTIFACTS, len(manifest.artifacts)
        )

    expected_names = (
        "manifest.json",
        manifest.input.path,
        manifest.result.path,
        *(artifact.path for artifact in manifest.artifacts),
    )
    try:
        expected_keys = tuple(portable_member_key(name) for name in expected_names)
        entries = tuple(directory.iterdir())
        actual_names = tuple(entry.name for entry in entries)
        actual_keys = tuple(portable_member_key(name) for name in actual_names)
    except (OSError, ValueError) as exc:
        raise OperationRecordError("record_corrupt") from exc
    if (
        len(set(expected_keys)) != len(expected_keys)
        or len(set(actual_keys)) != len(actual_keys)
        or set(actual_names) != set(expected_names)
    ):
        raise OperationRecordError("record_corrupt")

    manifest_content = _bounded_member_bytes(
        directory / "manifest.json",
        maximum=MAX_OPERATION_BUNDLE_BYTES,
        limit_name="operation_bundle_bytes",
    )
    _manifest_bytes(directory, manifest, content=manifest_content)

    members: list[OperationMemberV1] = []
    total_bytes = len(manifest_content)
    declared_members: list[
        tuple[Literal["normalized_input", "result", "artifact"], FileDigest | SvgArtifact]
    ] = [
        ("normalized_input", manifest.input),
        ("result", manifest.result),
    ]
    declared_members.extend(("artifact", artifact) for artifact in manifest.artifacts)
    for kind, declared in declared_members:
        maximum = (
            MAX_NORMALIZED_RESULT_BYTES
            if kind == "result"
            else MAX_ARTIFACT_BYTES
            if kind == "artifact"
            else MAX_OPERATION_BUNDLE_BYTES
        )
        limit_name = (
            "normalized_result_member_bytes"
            if kind == "result"
            else "artifact_decoded_bytes"
            if kind == "artifact"
            else "operation_bundle_bytes"
        )
        content = _bounded_member_bytes(
            directory / declared.path,
            maximum=maximum,
            limit_name=limit_name,
        )
        total_bytes += len(content)
        if hashlib.sha256(content).hexdigest() != declared.sha256:
            raise OperationRecordError("record_corrupt")
        members.append(
            OperationMemberV1(
                kind=kind,
                path=declared.path,
                sha256=declared.sha256,
                size_bytes=len(content),
            )
        )
    if total_bytes > MAX_OPERATION_BUNDLE_BYTES:
        raise _result_limit(
            "operation_bundle_bytes", MAX_OPERATION_BUNDLE_BYTES, total_bytes
        )
    result_content = next(
        _bounded_member_bytes(
            directory / member.path,
            maximum=MAX_NORMALIZED_RESULT_BYTES,
            limit_name="normalized_result_member_bytes",
        )
        for member in members
        if member.kind == "result"
    )
    _load_result(directory, manifest, content=result_content)

    members.sort(
        key=lambda member: (
            {"normalized_input": 0, "result": 1, "artifact": 2}[member.kind],
            member.path.encode("utf-8"),
        )
    )

    renderers = sorted(
        {
            (artifact.renderer, artifact.renderer_version)
            for artifact in manifest.artifacts
        },
        key=lambda value: (value[0].encode("utf-8"), value[1].encode("utf-8")),
    )
    try:
        return OperationRecordV1.model_validate(
            {
                "host_schema_version": 1,
                "record_kind": "operation",
                "host_binding_version": 1,
                "execution_mode": "unmanaged",
                "protocol_version": manifest.protocol_version,
                "component": manifest.component.model_dump(mode="json"),
                "operation_hash": manifest.operation_hash,
                "manifest_sha256": hashlib.sha256(manifest_content).hexdigest(),
                "members": [member.model_dump(mode="json") for member in members],
                "runner": manifest.runner.model_dump(mode="json"),
                "renderers": [
                    {"name": name, "version": version} for name, version in renderers
                ],
                "binding": binding.model_dump(mode="json", exclude_none=True),
                "compatibility": [item.model_dump(mode="json") for item in compatibility],
            }
        )
    except ValidationError as exc:
        raise OperationRecordError("record_corrupt") from exc


def build_operation_record(
    manifest: OperationManifest,
    bundle_dir: Path,
    binding: OperationBindingV1 | Mapping[str, Any],
    *,
    dataset_record: DatasetRecordV1 | None = None,
    dataset_payload: DatasetPayloadV1 | None = None,
    compatibility: Sequence[PortCompatibility] = (),
) -> OperationRecordV1:
    """Build a record only after binding and exact Phase-1 bytes reconcile."""

    try:
        binding_value = (
            binding.canonical_projection()
            if isinstance(binding, OperationBindingV1)
            else binding
        )
        validated_binding = OperationBindingV1.model_validate(binding_value)
    except ValidationError as exc:
        raise OperationRecordError(
            "invalid_field_mapping", details={"fields": []}
        ) from exc
    if compatibility and (
        validated_binding.source is None
        or validated_binding.source.kind != "operation"
    ):
        raise OperationRecordError("record_corrupt")
    _validate_binding(
        validated_binding,
        manifest,
        dataset_record=dataset_record,
        dataset_payload=dataset_payload,
    )
    return _derive_record(
        manifest,
        bundle_dir,
        validated_binding,
        compatibility=compatibility,
    )


def reconcile_operation_record(
    record: OperationRecordV1,
    bundle_dir: Path,
    *,
    dataset_record: DatasetRecordV1 | None = None,
    dataset_payload: DatasetPayloadV1 | None = None,
) -> OperationManifest:
    """Rebuild every derivable field and refuse any stored-record divergence."""

    try:
        manifest_content = _bounded_member_bytes(
            bundle_dir / "manifest.json",
            maximum=MAX_OPERATION_BUNDLE_BYTES,
            limit_name="operation_bundle_bytes",
        )
        manifest = OperationManifest.model_validate(strict_json_loads(manifest_content))
    except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise OperationRecordError("record_corrupt") from exc
    _validate_binding(
        record.binding,
        manifest,
        dataset_record=dataset_record,
        dataset_payload=dataset_payload,
    )
    derived = _derive_record(
        manifest,
        bundle_dir,
        record.binding,
        compatibility=record.compatibility,
    )
    if derived.canonical_projection() != record.canonical_projection():
        raise OperationRecordError("record_corrupt")
    return manifest


def load_operation_result(
    record: OperationRecordV1,
    bundle_dir: Path,
    *,
    dataset_record: DatasetRecordV1 | None = None,
    dataset_payload: DatasetPayloadV1 | None = None,
) -> dict[str, Any]:
    """Return a result only after complete record, manifest, and member reconciliation."""

    manifest = reconcile_operation_record(
        record,
        bundle_dir,
        dataset_record=dataset_record,
        dataset_payload=dataset_payload,
    )
    return _load_result(bundle_dir, manifest)


def reconciled_bundle_bytes(
    record: OperationRecordV1,
    bundle_dir: Path,
    *,
    dataset_record: DatasetRecordV1 | None = None,
    dataset_payload: DatasetPayloadV1 | None = None,
) -> tuple[bytes, dict[str, bytes]]:
    """Copy exact bounded bundle bytes only after complete reconciliation."""

    manifest = reconcile_operation_record(
        record,
        bundle_dir,
        dataset_record=dataset_record,
        dataset_payload=dataset_payload,
    )
    manifest_content = _bounded_member_bytes(
        bundle_dir / "manifest.json",
        maximum=MAX_OPERATION_BUNDLE_BYTES,
        limit_name="operation_bundle_bytes",
    )
    member_by_path = {member.path: member for member in record.members}
    members: dict[str, bytes] = {}
    declared_members: tuple[FileDigest | SvgArtifact, ...] = (
        manifest.input,
        manifest.result,
        *manifest.artifacts,
    )
    for declared in declared_members:
        member = member_by_path[declared.path]
        maximum = (
            MAX_NORMALIZED_RESULT_BYTES
            if member.kind == "result"
            else MAX_ARTIFACT_BYTES
            if member.kind == "artifact"
            else MAX_OPERATION_BUNDLE_BYTES
        )
        limit_name = (
            "normalized_result_member_bytes"
            if member.kind == "result"
            else "artifact_decoded_bytes"
            if member.kind == "artifact"
            else "operation_bundle_bytes"
        )
        content = _bounded_member_bytes(
            bundle_dir / member.path,
            maximum=maximum,
            limit_name=limit_name,
        )
        if (
            len(content) != member.size_bytes
            or hashlib.sha256(content).hexdigest() != member.sha256
        ):
            raise OperationRecordError("record_corrupt")
        members[member.path] = content
    total = len(manifest_content) + sum(len(content) for content in members.values())
    if total > MAX_OPERATION_BUNDLE_BYTES:
        raise _result_limit("operation_bundle_bytes", MAX_OPERATION_BUNDLE_BYTES, total)
    return manifest_content, members


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_NORMALIZED_RESULT_BYTES",
    "MAX_OPERATION_ARTIFACTS",
    "MAX_OPERATION_BUNDLE_BYTES",
    "MAX_RESULT_TOP_LEVEL_FIELDS",
    "OperationRecordError",
    "build_operation_record",
    "load_operation_result",
    "reconciled_bundle_bytes",
    "reconcile_operation_record",
]
