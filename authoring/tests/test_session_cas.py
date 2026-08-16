"""Focused tests for the owner-private Phase-3 session CAS."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from defined_quant.data_records import (
    DatasetPayloadV1,
    DatasetRecordV1,
    OperationBindingV1,
    cas_json_bytes,
)
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.local_host_platform import (
    SecureFilesystemError,
    SecureFilesystemErrorCode,
)
from defined_quant.operation_records import build_operation_record
from defined_quant.operation_runtime import execute_operation
from defined_quant.session_cas import (
    ORPHAN_SESSION_AGE_SECONDS,
    CasLimits,
    SessionCas,
    SessionScopeRegistry,
    cleanup_orphan_sessions,
)
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    OperationRequest,
    OperationSuccess,
    SvgArtifactRequest,
)

ROOT = Path(__file__).resolve().parents[2]
HASH_VECTORS = ROOT / "docs" / "local_mcp" / "hash_vectors.v1.json"
OPERATION_FIXTURE = Path(__file__).parent / "fixtures" / "operation_runtime_phase0.v1.json"
WINDOWS_DEVICE_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _vector(vector_id: str) -> dict[str, Any]:
    fixture = json.loads(HASH_VECTORS.read_text(encoding="utf-8"))
    return next(vector for vector in fixture["vectors"] if vector["id"] == vector_id)


def _dataset() -> tuple[DatasetPayloadV1, DatasetRecordV1]:
    payload = DatasetPayloadV1.model_validate(_vector("dataset_payload_two_rows")["value"])
    record = DatasetRecordV1.model_validate(_vector("dataset_record_inline_two_rows")["value"])
    return payload, record


def _phase1_operation(tmp_path: Path) -> tuple[OperationSuccess, Path, OperationBindingV1]:
    fixture = json.loads(OPERATION_FIXTURE.read_text(encoding="utf-8"))
    case = fixture["typed_success"]
    request = OperationRequest(
        component=ComponentRef.model_validate(fixture["component"]),
        input=case["input"],
        provenance=CallerProvenance.model_validate(case["provenance"]),
        artifacts=SvgArtifactRequest.model_validate(case["artifacts"]),
    )
    bundle = tmp_path / "phase1-bundle"
    result = execute_operation(request, output_dir=bundle)
    assert isinstance(result, OperationSuccess)
    return result, bundle, OperationBindingV1(literals=request.input)


def _operation_members(result: OperationSuccess, bundle: Path) -> dict[str, bytes]:
    declared = (
        result.manifest.input,
        result.manifest.result,
        *result.manifest.artifacts,
    )
    return {member.path: (bundle / member.path).read_bytes() for member in declared}


def _assert_code(exc: pytest.ExceptionInfo[HostFailureException], code: HostFailureCode) -> None:
    assert exc.value.code is code


def _assert_portable_host_filename(name: str) -> None:
    assert name not in {"", ".", ".."}
    assert not name.endswith((".", " "))
    assert not set(name).intersection('<>:"/\\|?*')
    stem = name.split(".", 1)[0].lower()
    assert not stem or stem not in WINDOWS_DEVICE_NAMES


def test_dataset_publication_is_private_verified_and_idempotent(tmp_path: Path) -> None:
    payload, record = _dataset()
    sibling = tmp_path / "caller-owned-sibling"
    sibling.mkdir()
    store = SessionCas(tmp_path)

    reference = store.publish_dataset(payload, record)
    first_size = store.used_bytes
    loaded = store.load_dataset(reference)

    assert reference == record.reference
    assert loaded.record == record
    assert loaded.payload == payload
    assert store.publish_dataset(payload, record) == reference
    assert store.used_bytes == first_size
    entry = store.directory / "datasets" / record.digest
    if os.name != "nt":
        assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(entry.stat().st_mode) == 0o700
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o600 for path in entry.iterdir()
        )
    assert not list((store.directory / "staging").iterdir())

    store.close()
    assert not store.directory.exists()
    assert sibling.exists()
    store.close()


def test_every_generated_session_cas_filename_is_windows_portable(tmp_path: Path) -> None:
    payload, dataset_record = _dataset()
    result, bundle, binding = _phase1_operation(tmp_path)
    operation_record = build_operation_record(result.manifest, bundle, binding)
    store = SessionCas(tmp_path / "state")

    dataset_reference = store.publish_dataset(payload, dataset_record)
    store.publish_operation(
        operation_record,
        manifest_bytes=(bundle / "manifest.json").read_bytes(),
        members=_operation_members(result, bundle),
    )
    for kind in ("dataset", "operation"):
        stage = store._new_stage(kind)
        _assert_portable_host_filename(stage.name)
        stage.rmdir()

    dataset_entry = store.directory / "datasets" / dataset_record.digest
    store._quarantine(dataset_entry, dataset_reference)

    _assert_portable_host_filename(store.directory.name)
    generated_paths = tuple(store.directory.rglob("*"))
    assert generated_paths
    for path in generated_paths:
        _assert_portable_host_filename(path.name)
    assert (store.directory / ".lock").is_file()
    assert (store.directory / ".defined-quant-session-v1").is_file()
    assert tuple((store.directory / "quarantine").iterdir())
    assert (store.directory / "operations" / operation_record.digest / "record.json").is_file()
    store.close()


def test_active_session_scope_is_distinct_from_unknown_and_expired(tmp_path: Path) -> None:
    payload, record = _dataset()
    registry = SessionScopeRegistry()
    first = SessionCas(tmp_path, scope_registry=registry)
    second = SessionCas(tmp_path, scope_registry=registry)
    reference = first.publish_dataset(payload, record)

    with pytest.raises(HostFailureException) as foreign:
        second.load_dataset(reference)
    _assert_code(foreign, HostFailureCode.REFERENCE_SCOPE_DENIED)

    unknown = "dqds:v1:" + "0" * 64
    with pytest.raises(HostFailureException) as absent:
        second.load_dataset(unknown)
    _assert_code(absent, HostFailureCode.REFERENCE_NOT_FOUND)

    with pytest.raises(HostFailureException) as unsupported:
        second.load_dataset("dqds:v2:" + "0" * 64)
    _assert_code(unsupported, HostFailureCode.UNSUPPORTED_REFERENCE_VERSION)
    assert unsupported.value.failure.error.details == {"requested_version": "v2"}

    first.close()
    with pytest.raises(HostFailureException) as expired:
        second.load_dataset(reference)
    _assert_code(expired, HostFailureCode.REFERENCE_NOT_FOUND)
    second.close()


def test_corrupt_dataset_is_quarantined_and_remains_refused(tmp_path: Path) -> None:
    payload, record = _dataset()
    store = SessionCas(tmp_path)
    reference = store.publish_dataset(payload, record)
    entry = store.directory / "datasets" / record.digest
    (entry / "payload.json").write_bytes(
        b'{"schema_version":1,"schema_version":1}\n'
    )
    os.chmod(entry / "payload.json", 0o600)

    with pytest.raises(HostFailureException) as corrupt:
        store.load_dataset(reference)
    _assert_code(corrupt, HostFailureCode.RECORD_CORRUPT)
    assert not entry.exists()
    assert len(tuple((store.directory / "quarantine").iterdir())) == 1

    with pytest.raises(HostFailureException) as repeated:
        store.load_dataset(reference)
    _assert_code(repeated, HostFailureCode.RECORD_CORRUPT)
    with pytest.raises(HostFailureException) as repair:
        store.publish_dataset(payload, record)
    _assert_code(repair, HostFailureCode.RECORD_CORRUPT)
    assert not entry.exists()
    store.close()


def test_stored_records_must_preserve_every_materialized_default(tmp_path: Path) -> None:
    payload, record = _dataset()
    store = SessionCas(tmp_path)
    reference = store.publish_dataset(payload, record)
    record_path = store.directory / "datasets" / record.digest / "record.json"
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    del stored["source"]["provenance"]["verification_status"]
    record_path.write_bytes(cas_json_bytes(stored))
    os.chmod(record_path, 0o600)

    with pytest.raises(HostFailureException) as corrupt:
        store.load_dataset(reference)
    _assert_code(corrupt, HostFailureCode.RECORD_CORRUPT)
    assert not record_path.parent.exists()
    store.close()


def test_fifo_replacing_a_cas_member_is_quarantined_without_blocking(tmp_path: Path) -> None:
    payload, record = _dataset()
    store = SessionCas(tmp_path)
    reference = store.publish_dataset(payload, record)
    entry = store.directory / "datasets" / record.digest
    payload_path = entry / "payload.json"
    payload_path.unlink()
    os.mkfifo(payload_path, mode=0o600)

    with pytest.raises(HostFailureException) as corrupt:
        store.load_dataset(reference)
    _assert_code(corrupt, HostFailureCode.RECORD_CORRUPT)
    assert not entry.exists()
    assert len(tuple((store.directory / "quarantine").iterdir())) == 1
    store.close()


def test_session_state_root_must_be_owner_private(tmp_path: Path) -> None:
    state_root = tmp_path / "public-state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o777)
    try:
        with pytest.raises(HostFailureException) as refused:
            SessionCas(state_root)
        _assert_code(refused, HostFailureCode.RECORD_PUBLICATION_FAILED)
    finally:
        os.chmod(state_root, 0o700)


if os.name != "posix":
    setattr(test_fifo_replacing_a_cas_member_is_quarantined_without_blocking, "__test__", False)
    setattr(test_session_state_root_must_be_owner_private, "__test__", False)


def test_limits_fail_before_publication_and_leave_no_stage(tmp_path: Path) -> None:
    payload, record = _dataset()
    payload_bytes = len(cas_json_bytes(payload))
    payload_store = SessionCas(
        tmp_path,
        limits=CasLimits(dataset_payload_bytes=payload_bytes - 1),
    )
    with pytest.raises(HostFailureException) as payload_limit:
        payload_store.publish_dataset(payload, record)
    _assert_code(payload_limit, HostFailureCode.INPUT_LIMIT_EXCEEDED)
    assert payload_limit.value.failure.error.details == {
        "limit_name": "normalized_dataset_payload_bytes",
        "maximum": payload_bytes - 1,
        "actual": payload_bytes,
    }
    assert not list((payload_store.directory / "datasets").iterdir())
    assert not list((payload_store.directory / "staging").iterdir())
    payload_store.close()

    quota_store = SessionCas(tmp_path, limits=CasLimits(session_bytes=1))
    with pytest.raises(HostFailureException) as quota:
        quota_store.publish_dataset(payload, record)
    _assert_code(quota, HostFailureCode.CACHE_FULL)
    assert not list((quota_store.directory / "datasets").iterdir())
    assert not list((quota_store.directory / "staging").iterdir())
    quota_store.close()

    record_store = SessionCas(tmp_path, limits=CasLimits(record_bytes=1))
    with pytest.raises(HostFailureException) as record_limit:
        record_store.publish_dataset(payload, record)
    _assert_code(record_limit, HostFailureCode.RECORD_PUBLICATION_FAILED)
    assert not list((record_store.directory / "datasets").iterdir())
    record_store.close()


def test_competing_equal_atomic_publication_reuses_verified_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, record = _dataset()
    store = SessionCas(tmp_path)

    def competing_publish(stage: Path, destination: Path) -> None:
        shutil_copytree(stage, destination)
        raise SecureFilesystemError(SecureFilesystemErrorCode.ALREADY_EXISTS)

    monkeypatch.setattr(
        store._secure_filesystem,
        "publish_directory_no_replace",
        competing_publish,
    )
    assert store.publish_dataset(payload, record) == record.reference
    assert store.load_dataset(record.reference).payload == payload
    assert not list((store.directory / "staging").iterdir())
    store.close()


def shutil_copytree(source: Path, destination: Path) -> None:
    """Copy a staged entry while retaining its owner-private modes for a race test."""

    import shutil

    shutil.copytree(source, destination)


def test_operation_publication_reconciles_phase1_manifest_and_members(tmp_path: Path) -> None:
    result, bundle, binding = _phase1_operation(tmp_path)
    record = build_operation_record(result.manifest, bundle, binding)
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    members = _operation_members(result, bundle)
    bundle_size = len(manifest_bytes) + sum(len(value) for value in members.values())
    limited = SessionCas(
        tmp_path / "limited-state",
        limits=CasLimits(operation_bundle_bytes=bundle_size - 1),
    )
    with pytest.raises(HostFailureException) as over_limit:
        limited.publish_operation(
            record,
            manifest_bytes=manifest_bytes,
            members=members,
        )
    _assert_code(over_limit, HostFailureCode.RESULT_LIMIT_EXCEEDED)
    assert over_limit.value.failure.error.details == {
        "limit_name": "operation_bundle_bytes",
        "maximum": bundle_size - 1,
        "actual": bundle_size,
    }
    assert not list((limited.directory / "operations").iterdir())
    limited.close()

    exact_session_size = len(cas_json_bytes(record)) + bundle_size
    store = SessionCas(
        tmp_path / "state",
        limits=CasLimits(session_bytes=exact_session_size),
    )

    reference = store.publish_operation(
        record,
        manifest_bytes=manifest_bytes,
        members=members,
    )
    loaded = store.load_operation(reference)

    assert reference == record.reference
    assert loaded.record == record
    assert loaded.manifest == result.manifest
    assert loaded.manifest_bytes == manifest_bytes
    assert dict(loaded.members) == members
    first_size = store.used_bytes
    assert first_size == exact_session_size
    assert store.publish_operation(
        record,
        manifest_bytes=manifest_bytes,
        members=members,
    ) == reference
    assert store.used_bytes == first_size
    with pytest.raises(TypeError):
        loaded.members["extra.json"] = b"{}"  # type: ignore[index]

    result_path = (
        store.directory
        / "operations"
        / record.digest
        / "bundle"
        / result.manifest.result.path
    )
    result_path.write_bytes(b"{}\n")
    os.chmod(result_path, 0o600)
    with pytest.raises(HostFailureException) as corrupt:
        store.load_operation(reference)
    _assert_code(corrupt, HostFailureCode.RECORD_CORRUPT)
    assert len(tuple((store.directory / "quarantine").iterdir())) == 1
    store.close()


@pytest.mark.parametrize(
    "alias",
    [
        "RESULT.JSON",
        "MANIFEST.JSON",
        "CON.txt",
        "result.json.",
        "result.json ",
        "result.json:stream",
        "result\\json",
        "PROGRA~1.json",
    ],
)
def test_operation_publication_maps_nonportable_member_names_to_closed_failure(
    tmp_path: Path,
    alias: str,
) -> None:
    result, bundle, binding = _phase1_operation(tmp_path)
    record = build_operation_record(result.manifest, bundle, binding)
    members = _operation_members(result, bundle)
    members[alias] = next(iter(members.values()))
    store = SessionCas(tmp_path / "state")

    with pytest.raises(HostFailureException) as refused:
        store.publish_operation(
            record,
            manifest_bytes=(bundle / "manifest.json").read_bytes(),
            members=members,
        )

    _assert_code(refused, HostFailureCode.RECORD_PUBLICATION_FAILED)
    assert refused.value.failure.error.message == (
        "The immutable host record could not be published safely."
    )
    assert not tuple((store.directory / "operations").iterdir())
    assert not tuple((store.directory / "staging").iterdir())
    store.close()


def test_session_member_mapping_uses_segmentwise_portable_identity() -> None:
    with pytest.raises(ValueError, match="operation member mapping is invalid"):
        SessionCas._validated_member_mapping(
            {
                "Reports/Result.JSON": b"first",
                "reports/result.json": b"second",
            }
        )


def test_cursor_is_bound_to_session_reference_view_selector_and_index(tmp_path: Path) -> None:
    payload, record = _dataset()
    registry = SessionScopeRegistry()
    first = SessionCas(tmp_path, scope_registry=registry)
    second = SessionCas(tmp_path, scope_registry=registry)
    reference = first.publish_dataset(payload, record)
    token = first.encode_cursor(reference, "preview", None, 17)

    assert len(token) <= 512
    assert first.decode_cursor(
        token,
        reference=reference,
        view="preview",
        selector=None,
    ) == 17
    for changed in (
        {"reference": "dqds:v1:" + "0" * 64, "view": "preview", "selector": None},
        {"reference": reference, "view": "metadata", "selector": None},
        {"reference": reference, "view": "preview", "selector": "field"},
    ):
        with pytest.raises(HostFailureException) as mismatch:
            first.decode_cursor(token, **changed)
        _assert_code(mismatch, HostFailureCode.INVALID_TOOL_REQUEST)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(HostFailureException) as invalid:
        first.decode_cursor(
            tampered,
            reference=reference,
            view="preview",
            selector=None,
        )
    _assert_code(invalid, HostFailureCode.INVALID_TOOL_REQUEST)
    with pytest.raises(HostFailureException) as foreign:
        second.decode_cursor(
            token,
            reference=reference,
            view="preview",
            selector=None,
        )
    _assert_code(foreign, HostFailureCode.INVALID_TOOL_REQUEST)

    maximum_selector = "x" * 240
    maximum_index = 9_007_199_254_740_991
    maximum_token = first.encode_cursor(
        reference,
        "result_field",
        maximum_selector,
        maximum_index,
    )
    assert len(maximum_token) <= 512
    assert first.decode_cursor(
        maximum_token,
        reference=reference,
        view="result_field",
        selector=maximum_selector,
    ) == maximum_index
    with pytest.raises(HostFailureException) as wrong_selector:
        first.decode_cursor(
            maximum_token,
            reference=reference,
            view="result_field",
            selector="y" * 240,
        )
    _assert_code(wrong_selector, HostFailureCode.INVALID_TOOL_REQUEST)
    with pytest.raises(HostFailureException) as unpublished:
        first.encode_cursor(
            "dqop:v1:" + "f" * 64,
            "messages",
            None,
            20,
        )
    _assert_code(unpublished, HostFailureCode.REFERENCE_NOT_FOUND)
    first.close()
    second.close()


def _private_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    os.chmod(path, 0o600)


def _orphan(state_root: Path, name: str, marker_mtime: float) -> Path:
    directory = state_root / f"session-{name}"
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    marker = directory / ".defined-quant-session-v1"
    _private_file(
        marker,
        cas_json_bytes(
            {"application": "defined-quant-local-mcp", "schema_version": 1}
        ),
    )
    _private_file(directory / ".lock", b"0")
    os.utime(marker, (marker_mtime, marker_mtime))
    return directory


def test_orphan_cleanup_is_marked_owned_unlocked_and_strictly_older_than_24h(
    tmp_path: Path,
) -> None:
    now = 2_000_000_000.0
    old = _orphan(tmp_path, "old", now - ORPHAN_SESSION_AGE_SECONDS - 1)
    boundary = _orphan(tmp_path, "boundary", now - ORPHAN_SESSION_AGE_SECONDS)
    unmarked = tmp_path / "session-unmarked"
    unmarked.mkdir(mode=0o700)
    os.chmod(unmarked, 0o700)
    active = SessionCas(tmp_path)
    marker = active.directory / ".defined-quant-session-v1"
    os.utime(marker, (now - ORPHAN_SESSION_AGE_SECONDS - 1, now - ORPHAN_SESSION_AGE_SECONDS - 1))

    removed = cleanup_orphan_sessions(tmp_path, now=now)

    assert removed == (old,)
    assert not old.exists()
    assert boundary.exists()
    assert unmarked.exists()
    assert active.directory.exists()
    active.close()
