"""Owner-private, session-scoped content-addressed storage for local host records."""

from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from defined_quant.data_records import (
    MAX_RECORD_BYTES,
    DatasetPayloadV1,
    DatasetRecordV1,
    OperationRecordV1,
    cas_json_bytes,
    pretty_json_bytes,
    strict_cas_json_loads,
    strict_json_loads,
)
from defined_quant.host_failures import (
    HostFailureCode,
    HostFailureException,
)
from defined_quant.operation_records import reconcile_operation_record
from defined_quant_protocol import OperationManifest
from pydantic import ValidationError

MAX_DATASET_PAYLOAD_BYTES: Final = 128 * 1024 * 1024
MAX_OPERATION_BUNDLE_BYTES: Final = 128 * 1024 * 1024
MAX_SESSION_BYTES: Final = 1024 * 1024 * 1024
ORPHAN_SESSION_AGE_SECONDS: Final = 24 * 60 * 60
MAX_CURSOR_BYTES: Final = 512

_SESSION_PREFIX = "session-"
_MARKER_NAME = ".defined-quant-session-v1"
_LOCK_NAME = ".lock"
_MARKER_VALUE = {
    "application": "defined-quant-local-mcp",
    "schema_version": 1,
}
_REFERENCE_PATTERN = re.compile(r"^(dqds|dqop):v([0-9]{1,3}):([0-9a-f]{64})$")
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_MEMBER_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)*$"
)
_AT_FDCWD = -100
_RENAME_NOREPLACE = 0x00000001
_RENAME_EXCL = 0x00000004
_CURSOR_SELECTOR_DOMAIN = b"defined-quant-cursor-selector-v1\x00"


@dataclass(frozen=True, slots=True)
class CasLimits:
    """Configurable lower ceilings bounded by the hard alpha limits."""

    record_bytes: int = MAX_RECORD_BYTES
    dataset_payload_bytes: int = MAX_DATASET_PAYLOAD_BYTES
    operation_bundle_bytes: int = MAX_OPERATION_BUNDLE_BYTES
    session_bytes: int = MAX_SESSION_BYTES

    def __post_init__(self) -> None:
        ceilings = (
            (self.record_bytes, MAX_RECORD_BYTES),
            (self.dataset_payload_bytes, MAX_DATASET_PAYLOAD_BYTES),
            (self.operation_bundle_bytes, MAX_OPERATION_BUNDLE_BYTES),
            (self.session_bytes, MAX_SESSION_BYTES),
        )
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in ceilings):
            raise ValueError("CAS limits must be positive integers within the hard alpha maxima")


@dataclass(frozen=True, slots=True)
class StoredDataset:
    """One fully verified dataset record and normalized payload."""

    reference: str
    record: DatasetRecordV1
    payload: DatasetPayloadV1


@dataclass(frozen=True, slots=True)
class StoredOperation:
    """One fully verified operation record, manifest, and declared members."""

    reference: str
    record: OperationRecordV1
    manifest: OperationManifest
    manifest_bytes: bytes
    members: Mapping[str, bytes]


class SessionScopeRegistry:
    """Process-local knowledge used to distinguish absent and foreign active references."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions_by_reference: dict[str, set[str]] = {}

    def register(self, session_id: str, reference: str) -> None:
        with self._lock:
            self._sessions_by_reference.setdefault(reference, set()).add(session_id)

    def known_outside(self, session_id: str, reference: str) -> bool:
        with self._lock:
            sessions = self._sessions_by_reference.get(reference, set())
            return bool(sessions.difference({session_id}))

    def unregister_session(self, session_id: str) -> None:
        with self._lock:
            empty: list[str] = []
            for reference, sessions in self._sessions_by_reference.items():
                sessions.discard(session_id)
                if not sessions:
                    empty.append(reference)
            for reference in empty:
                del self._sessions_by_reference[reference]


_DEFAULT_SCOPE_REGISTRY = SessionScopeRegistry()


def _fail(
    code: HostFailureCode,
    *,
    details: Mapping[str, Any] | None = None,
) -> HostFailureException:
    return HostFailureException(code, details=cast(Any, details))


def _owner_matches(info: os.stat_result) -> bool:
    getuid = getattr(os, "getuid", None)
    return getuid is not None and info.st_uid == getuid()


def _private_mode(info: os.stat_result, *, directory: bool) -> bool:
    if os.name != "posix":
        return False
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return expected_type and info.st_mode & 0o077 == 0


def _validate_owned_directory(path: Path, *, private: bool) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError("session directory is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or not _owner_matches(info):
        raise ValueError("session directory is not a correctly owned real directory")
    if private and not _private_mode(info, directory=True):
        raise ValueError("session directory is not owner-private")
    return info


def _write_private(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | nofollow, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short CAS write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_private(path: Path, *, maximum_bytes: int) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, os.O_RDONLY | nofollow | nonblocking)
    try:
        info = os.fstat(descriptor)
        if not _owner_matches(info) or not _private_mode(info, directory=False):
            raise ValueError("CAS member is not an owner-private regular file")
        if info.st_size > maximum_bytes:
            raise ValueError("CAS member exceeds its byte ceiling")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError("CAS member exceeds its byte ceiling")
        after = os.fstat(descriptor)
        if (
            after.st_dev != info.st_dev
            or after.st_ino != info.st_ino
            or after.st_size != info.st_size
            or total != info.st_size
        ):
            raise ValueError("CAS member changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _atomic_rename_no_replace(source: Path, destination: Path) -> None:
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "renameat2 is unavailable")
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            _AT_FDCWD,
            source_bytes,
            _AT_FDCWD,
            destination_bytes,
            _RENAME_NOREPLACE,
        )
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise OSError(errno.ENOTSUP, "renamex_np is unavailable")
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, _RENAME_EXCL)
    elif os.name == "nt":
        os.rename(source, destination)
        return
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace publication is unavailable")
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), os.fspath(destination))


def _lock_file(descriptor: int, *, blocking: bool) -> bool:
    if os.name == "nt":  # pragma: no cover - reserved for a future tested Windows boundary
        import msvcrt

        mode = (
            msvcrt.LK_LOCK  # type: ignore[attr-defined]
            if blocking
            else msvcrt.LK_NBLCK  # type: ignore[attr-defined]
        )
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, mode, 1)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    import fcntl

    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(descriptor, flags)
        return True
    except BlockingIOError:
        return False


def _unlock_file(descriptor: int) -> None:
    if os.name == "nt":  # pragma: no cover - reserved for a future tested Windows boundary
        import msvcrt

        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(  # type: ignore[attr-defined]
                descriptor,
                msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
                1,
            )
        except OSError:
            pass
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _reference_parts(
    reference: str,
    *,
    expected: Literal["dataset", "operation"] | None,
) -> tuple[str, str]:
    match = _REFERENCE_PATTERN.fullmatch(reference)
    if match is None:
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
    kind = "dataset" if match.group(1) == "dqds" else "operation"
    version = match.group(2)
    if version != "1":
        raise _fail(
            HostFailureCode.UNSUPPORTED_REFERENCE_VERSION,
            details={"requested_version": f"v{version}"},
        )
    if expected is not None and kind != expected:
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
    return kind, match.group(3)


def _cursor_selector_binding(selector: object) -> str | None:
    if selector is None:
        return None
    if not isinstance(selector, str) or not selector:
        raise ValueError("invalid cursor selector")
    encoded = selector.encode("utf-8")
    if len(encoded) > 240:
        raise ValueError("invalid cursor selector")
    return hashlib.sha256(_CURSOR_SELECTOR_DOMAIN + encoded).hexdigest()


def _entry_size(path: Path) -> int:
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        for name in (*directories, *files):
            candidate = Path(root) / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not _owner_matches(info):
                raise ValueError("CAS entry contains an unsafe member")
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError("CAS entry contains a non-regular member")
    return total


class SessionCas:
    """One ephemeral immutable store whose references expire when the session closes."""

    def __init__(
        self,
        state_root: Path,
        *,
        scope_registry: SessionScopeRegistry | None = None,
        limits: CasLimits = CasLimits(),
    ) -> None:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED)
        self._mutex = RLock()
        self._scope_registry = scope_registry or _DEFAULT_SCOPE_REGISTRY
        self._limits = limits
        self._session_id = secrets.token_hex(16)
        self._cursor_key = secrets.token_bytes(32)
        self._references: set[str] = set()
        self._corrupt_references: set[str] = set()
        self._used_bytes = 0
        self._closed = False
        self._lock_descriptor = -1

        self._state_root = Path(state_root)
        self._prepare_state_root()
        cleanup_orphan_sessions(self._state_root)
        self._directory = self._state_root / f"{_SESSION_PREFIX}{self._session_id}"
        try:
            self._directory.mkdir(mode=0o700)
            os.chmod(self._directory, 0o700)
            for name in ("datasets", "operations", "staging", "quarantine"):
                child = self._directory / name
                child.mkdir(mode=0o700)
                os.chmod(child, 0o700)
            _write_private(self._directory / _MARKER_NAME, cas_json_bytes(_MARKER_VALUE))
            _write_private(self._directory / _LOCK_NAME, b"0")
            self._lock_descriptor = os.open(
                self._directory / _LOCK_NAME,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            )
            if not _lock_file(self._lock_descriptor, blocking=True):
                raise OSError(errno.EBUSY, "could not lock session directory")
        except Exception as exc:
            if self._lock_descriptor >= 0:
                os.close(self._lock_descriptor)
            if self._directory.exists() and not self._directory.is_symlink():
                shutil.rmtree(self._directory, ignore_errors=True)
            raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc

    @property
    def directory(self) -> Path:
        """Return the internal session location for controller diagnostics and tests."""

        return self._directory

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def used_bytes(self) -> int:
        return self._used_bytes

    def _prepare_state_root(self) -> None:
        try:
            if not self._state_root.is_absolute():
                raise ValueError("session-state root must be absolute")
            normalized = Path(os.path.abspath(self._state_root))
            if normalized != self._state_root:
                raise ValueError("session-state root must not contain relative segments")
            if normalized in {Path(normalized.anchor), Path.home().resolve()}:
                raise ValueError("session-state root is too broad")
            if self._state_root.exists():
                if self._state_root.resolve(strict=True) != self._state_root:
                    raise ValueError("session-state root must not traverse symbolic links")
                _validate_owned_directory(self._state_root, private=True)
            else:
                if self._state_root.parent.resolve(strict=True) != self._state_root.parent:
                    raise ValueError("session-state root parent must not traverse symbolic links")
                self._state_root.mkdir(mode=0o700)
                os.chmod(self._state_root, 0o700)
                _validate_owned_directory(self._state_root, private=True)
        except (OSError, ValueError) as exc:
            raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc

    def _require_open(self) -> None:
        if self._closed:
            raise _fail(HostFailureCode.REFERENCE_NOT_FOUND)

    def _destination(self, reference: str, *, expected: Literal["dataset", "operation"]) -> Path:
        _, digest = _reference_parts(reference, expected=expected)
        parent = "datasets" if expected == "dataset" else "operations"
        return self._directory / parent / digest

    def _reserve(self, size: int) -> None:
        if self._used_bytes + size > self._limits.session_bytes:
            raise _fail(HostFailureCode.CACHE_FULL)

    def _register(self, reference: str, size: int) -> None:
        if reference not in self._references:
            self._reserve(size)
            self._references.add(reference)
            self._used_bytes += size
            self._scope_registry.register(self._session_id, reference)

    def _resolve(self, reference: str, *, expected: Literal["dataset", "operation"]) -> Path:
        self._require_open()
        destination = self._destination(reference, expected=expected)
        if reference in self._corrupt_references:
            raise _fail(HostFailureCode.RECORD_CORRUPT)
        if reference in self._references:
            return destination
        if self._scope_registry.known_outside(self._session_id, reference):
            raise _fail(HostFailureCode.REFERENCE_SCOPE_DENIED)
        raise _fail(HostFailureCode.REFERENCE_NOT_FOUND)

    def publish_dataset(self, payload: DatasetPayloadV1, record: DatasetRecordV1) -> str:
        """Atomically publish one normalized payload and its immutable record."""

        with self._mutex:
            self._require_open()
            try:
                validated_payload = DatasetPayloadV1.model_validate(
                    payload.canonical_projection()
                )
                validated_record = DatasetRecordV1.model_validate(
                    record.canonical_projection()
                )
                validated_record.validate_payload(validated_payload)
                payload_bytes = cas_json_bytes(validated_payload)
                record_bytes = cas_json_bytes(validated_record)
            except (TypeError, ValueError, ValidationError) as exc:
                raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc
            if len(payload_bytes) > self._limits.dataset_payload_bytes:
                raise _fail(
                    HostFailureCode.INPUT_LIMIT_EXCEEDED,
                    details={
                        "limit_name": "normalized_dataset_payload_bytes",
                        "maximum": self._limits.dataset_payload_bytes,
                        "actual": len(payload_bytes),
                    },
                )
            if len(record_bytes) > self._limits.record_bytes:
                raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED)
            reference = validated_record.reference
            if reference in self._corrupt_references:
                raise _fail(HostFailureCode.RECORD_CORRUPT)
            destination = self._destination(reference, expected="dataset")
            if destination.exists():
                try:
                    loaded = self._load_dataset_directory(destination, reference)
                except Exception as exc:
                    self._quarantine(destination, reference)
                    raise _fail(HostFailureCode.RECORD_CORRUPT) from exc
                self._register(reference, _entry_size(destination))
                return loaded.reference
            size = len(payload_bytes) + len(record_bytes)
            self._reserve(size)
            stage = self._new_stage("dataset")
            try:
                _write_private(stage / "record.json", record_bytes)
                _write_private(stage / "payload.json", payload_bytes)
                self._load_dataset_directory(stage, reference)
                self._publish_stage(stage, destination, reference, "dataset")
                self._register(reference, size)
                return reference
            except HostFailureException:
                raise
            except Exception as exc:
                raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc
            finally:
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)

    def load_dataset(self, reference: str) -> StoredDataset:
        """Return a dataset only after complete schema, digest, and cross-record verification."""

        with self._mutex:
            destination = self._resolve(reference, expected="dataset")
            try:
                return self._load_dataset_directory(destination, reference)
            except HostFailureException:
                raise
            except Exception as exc:
                self._quarantine(destination, reference)
                raise _fail(HostFailureCode.RECORD_CORRUPT) from exc

    def _load_dataset_directory(self, directory: Path, reference: str) -> StoredDataset:
        _validate_owned_directory(directory, private=True)
        entries = {entry.name for entry in directory.iterdir()}
        if entries != {"record.json", "payload.json"}:
            raise ValueError("dataset entry has unexpected members")
        record_bytes = _read_private(
            directory / "record.json", maximum_bytes=self._limits.record_bytes
        )
        payload_bytes = _read_private(
            directory / "payload.json", maximum_bytes=self._limits.dataset_payload_bytes
        )
        record = DatasetRecordV1.model_validate(
            strict_cas_json_loads(record_bytes, maximum_bytes=self._limits.record_bytes)
        )
        payload = DatasetPayloadV1.model_validate(
            strict_cas_json_loads(payload_bytes, maximum_bytes=self._limits.dataset_payload_bytes)
        )
        if cas_json_bytes(record) != record_bytes or cas_json_bytes(payload) != payload_bytes:
            raise ValueError("dataset entry omits or changes a materialized record field")
        if record.reference != reference:
            raise ValueError("dataset reference digest does not match its stored record")
        record.validate_payload(payload)
        return StoredDataset(reference=reference, record=record, payload=payload)

    def publish_operation(
        self,
        record: OperationRecordV1,
        *,
        manifest_bytes: bytes,
        members: Mapping[str, bytes],
    ) -> str:
        """Atomically publish one reconciled Phase-1 manifest and all declared members."""

        with self._mutex:
            self._require_open()
            try:
                validated_record = OperationRecordV1.model_validate(
                    record.canonical_projection()
                )
                self._validated_manifest(manifest_bytes)
                member_copy = self._validated_member_mapping(members)
                record_bytes = cas_json_bytes(validated_record)
            except (TypeError, ValueError, ValidationError) as exc:
                raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc
            bundle_size = len(manifest_bytes) + sum(len(value) for value in member_copy.values())
            if bundle_size > self._limits.operation_bundle_bytes:
                raise _fail(
                    HostFailureCode.RESULT_LIMIT_EXCEEDED,
                    details={
                        "limit_name": "operation_bundle_bytes",
                        "maximum": self._limits.operation_bundle_bytes,
                        "actual": bundle_size,
                    },
                )
            if len(record_bytes) > self._limits.record_bytes:
                raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED)
            reference = validated_record.reference
            if reference in self._corrupt_references:
                raise _fail(HostFailureCode.RECORD_CORRUPT)
            destination = self._destination(reference, expected="operation")
            size = len(record_bytes) + bundle_size
            if destination.exists():
                try:
                    loaded = self._load_operation_directory(destination, reference)
                except Exception as exc:
                    self._quarantine(destination, reference)
                    raise _fail(HostFailureCode.RECORD_CORRUPT) from exc
                if (
                    loaded.record != validated_record
                    or loaded.manifest_bytes != manifest_bytes
                    or dict(loaded.members) != member_copy
                ):
                    raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED)
                self._register(reference, _entry_size(destination))
                return loaded.reference
            self._reserve(size)
            stage = self._new_stage("operation")
            try:
                _write_private(stage / "record.json", record_bytes)
                bundle_root = stage / "bundle"
                bundle_root.mkdir(mode=0o700)
                os.chmod(bundle_root, 0o700)
                _write_private(bundle_root / "manifest.json", manifest_bytes)
                for name, content in member_copy.items():
                    destination_member = bundle_root.joinpath(*name.split("/"))
                    destination_member.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.chmod(destination_member.parent, 0o700)
                    _write_private(destination_member, content)
                self._load_operation_directory(stage, reference)
                if destination.exists():
                    try:
                        loaded = self._load_operation_directory(destination, reference)
                    except Exception as exc:
                        self._quarantine(destination, reference)
                        raise _fail(HostFailureCode.RECORD_CORRUPT) from exc
                    if (
                        loaded.record != validated_record
                        or loaded.manifest_bytes != manifest_bytes
                        or dict(loaded.members) != member_copy
                    ):
                        raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED)
                    self._register(reference, _entry_size(destination))
                    return loaded.reference
                self._publish_stage(stage, destination, reference, "operation")
                self._register(reference, size)
                return reference
            except HostFailureException:
                raise
            except Exception as exc:
                raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc
            finally:
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)

    def load_operation(self, reference: str) -> StoredOperation:
        """Return an operation only after complete record, manifest, and member verification."""

        with self._mutex:
            destination = self._resolve(reference, expected="operation")
            try:
                return self._load_operation_directory(destination, reference)
            except HostFailureException as exc:
                self._quarantine(destination, reference)
                if exc.code is HostFailureCode.RECORD_CORRUPT:
                    raise
                raise _fail(HostFailureCode.RECORD_CORRUPT) from exc
            except Exception as exc:
                self._quarantine(destination, reference)
                raise _fail(HostFailureCode.RECORD_CORRUPT) from exc

    def _load_operation_directory(self, directory: Path, reference: str) -> StoredOperation:
        _validate_owned_directory(directory, private=True)
        entries = {entry.name for entry in directory.iterdir()}
        if entries != {"record.json", "bundle"}:
            raise ValueError("operation entry has unexpected members")
        record_bytes = _read_private(
            directory / "record.json", maximum_bytes=self._limits.record_bytes
        )
        record = OperationRecordV1.model_validate(
            strict_cas_json_loads(record_bytes, maximum_bytes=self._limits.record_bytes)
        )
        if cas_json_bytes(record) != record_bytes:
            raise ValueError("operation entry omits or changes a materialized record field")
        if record.reference != reference:
            raise ValueError("operation reference digest does not match its stored record")
        bundle_root = directory / "bundle"
        manifest_bytes, members = self._read_operation_bundle(bundle_root)
        manifest = self._reconcile_operation(record, bundle_root)
        return StoredOperation(
            reference=reference,
            record=record,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            members=MappingProxyType(members),
        )

    def _read_operation_bundle(self, bundle_root: Path) -> tuple[bytes, dict[str, bytes]]:
        _validate_owned_directory(bundle_root, private=True)
        remaining = self._limits.operation_bundle_bytes
        manifest_bytes = _read_private(bundle_root / "manifest.json", maximum_bytes=remaining)
        remaining -= len(manifest_bytes)
        paths: list[str] = []
        for root, directories, files in os.walk(bundle_root, followlinks=False):
            directories.sort(key=lambda item: item.encode("utf-8"))
            files.sort(key=lambda item: item.encode("utf-8"))
            for name in directories:
                _validate_owned_directory(Path(root) / name, private=True)
            for name in files:
                path = Path(root) / name
                relative = path.relative_to(bundle_root).as_posix()
                if relative == "manifest.json":
                    continue
                if _MEMBER_PATH_PATTERN.fullmatch(relative) is None or len(relative.encode()) > 512:
                    raise ValueError("operation member path is unsafe")
                paths.append(relative)
        if not 2 <= len(paths) <= 130 or len(set(paths)) != len(paths):
            raise ValueError("operation bundle has an invalid member count")
        members: dict[str, bytes] = {}
        for relative in sorted(paths, key=str.encode):
            content = _read_private(bundle_root / relative, maximum_bytes=remaining)
            remaining -= len(content)
            members[relative] = content
        return manifest_bytes, members

    def _reconcile_operation(
        self,
        record: OperationRecordV1,
        bundle_root: Path,
    ) -> OperationManifest:
        source = record.binding.source
        if source is not None and source.kind == "operation":
            raise ValueError("operation-source records remain deferred")
        dataset: StoredDataset | None = None
        if source is not None:
            dataset = self.load_dataset(source.ref)
        return reconcile_operation_record(
            record,
            bundle_root,
            dataset_record=None if dataset is None else dataset.record,
            dataset_payload=None if dataset is None else dataset.payload,
        )

    @staticmethod
    def _validated_manifest(content: bytes) -> OperationManifest:
        value = strict_json_loads(content, maximum_bytes=MAX_OPERATION_BUNDLE_BYTES)
        manifest = OperationManifest.model_validate(value)
        if pretty_json_bytes(manifest.model_dump(mode="json")) != content:
            raise ValueError("operation manifest is not the exact Phase-1 serialization")
        return manifest

    @staticmethod
    def _validated_member_mapping(members: Mapping[str, bytes]) -> dict[str, bytes]:
        if not 2 <= len(members) <= 130:
            raise ValueError("operation bundle has an invalid member count")
        result: dict[str, bytes] = {}
        for name, content in members.items():
            if (
                not isinstance(name, str)
                or _MEMBER_PATH_PATTERN.fullmatch(name) is None
                or len(name.encode("utf-8")) > 512
                or not isinstance(content, bytes)
            ):
                raise ValueError("operation member mapping is invalid")
            result[name] = bytes(content)
        return result

    def _new_stage(self, kind: str) -> Path:
        stage = Path(tempfile.mkdtemp(prefix=f"{kind}-", dir=self._directory / "staging"))
        os.chmod(stage, 0o700)
        return stage

    def _publish_stage(
        self,
        stage: Path,
        destination: Path,
        reference: str,
        kind: Literal["dataset", "operation"],
    ) -> None:
        try:
            _atomic_rename_no_replace(stage, destination)
            return
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY} and not destination.exists():
                raise
        if kind == "dataset":
            try:
                self._load_dataset_directory(destination, reference)
            except Exception as exc:
                self._quarantine(destination, reference)
                raise _fail(HostFailureCode.RECORD_CORRUPT) from exc
        else:
            try:
                self._load_operation_directory(destination, reference)
            except Exception as exc:
                self._quarantine(destination, reference)
                raise _fail(HostFailureCode.RECORD_CORRUPT) from exc

    def _quarantine(self, destination: Path, reference: str) -> None:
        self._corrupt_references.add(reference)
        if not destination.exists() or destination.is_symlink():
            return
        target = self._directory / "quarantine" / (
            f"{destination.parent.name}-{destination.name}-{secrets.token_hex(8)}"
        )
        try:
            os.rename(destination, target)
        except OSError:
            pass

    def encode_cursor(
        self,
        reference: str,
        view: str,
        selector: str | None,
        next_index: int,
    ) -> str:
        """Create an opaque session-authenticated cursor for one next page index."""

        with self._mutex:
            self._require_open()
            _reference_parts(reference, expected=None)
            if reference in self._corrupt_references:
                raise _fail(HostFailureCode.RECORD_CORRUPT)
            if reference not in self._references:
                if self._scope_registry.known_outside(self._session_id, reference):
                    raise _fail(HostFailureCode.REFERENCE_SCOPE_DENIED)
                raise _fail(HostFailureCode.REFERENCE_NOT_FOUND)
            return self._encode_cursor_token(reference, view, selector, next_index)

    def _encode_pending_cursor(
        self,
        reference: str,
        view: str,
        selector: str | None,
        next_index: int,
    ) -> str:
        """Prepare a cursor that remains internal until its record publishes successfully."""

        with self._mutex:
            self._require_open()
            _reference_parts(reference, expected=None)
            if reference in self._corrupt_references:
                raise _fail(HostFailureCode.RECORD_CORRUPT)
            return self._encode_cursor_token(reference, view, selector, next_index)

    def _encode_cursor_token(
        self,
        reference: str,
        view: str,
        selector: str | None,
        next_index: int,
    ) -> str:
        try:
            invalid_view = (
                not isinstance(view, str)
                or not view
                or len(view.encode("utf-8")) > 64
            )
            selector_binding = _cursor_selector_binding(selector)
        except (UnicodeEncodeError, ValueError):
            invalid_view = True
            selector_binding = None
        if invalid_view or (
            type(next_index) is not int
            or not 0 <= next_index <= 9_007_199_254_740_991
        ):
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
        payload = json.dumps(
            {
                "i": next_index,
                "r": reference,
                "s": selector_binding,
                "v": view,
                "z": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        signature = hmac.digest(self._cursor_key, payload, "sha256")
        token = base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")
        if len(token) > MAX_CURSOR_BYTES:
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
        return token

    def decode_cursor(
        self,
        token: str,
        *,
        reference: str,
        view: str,
        selector: str | None,
    ) -> int:
        """Authenticate a cursor and require its session, record, view, and selector binding."""

        with self._mutex:
            self._require_open()
            try:
                if not isinstance(token, str) or _CURSOR_PATTERN.fullmatch(token) is None:
                    raise ValueError("invalid cursor shape")
                padding = "=" * (-len(token) % 4)
                decoded = base64.b64decode(token + padding, altchars=b"-_", validate=True)
                canonical_token = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
                if canonical_token != token:
                    raise ValueError("non-canonical cursor encoding")
                if len(decoded) <= 32:
                    raise ValueError("invalid cursor length")
                payload, signature = decoded[:-32], decoded[-32:]
                expected_signature = hmac.digest(self._cursor_key, payload, "sha256")
                if not hmac.compare_digest(signature, expected_signature):
                    raise ValueError("invalid cursor authentication")
                value = strict_json_loads(payload)
                if not isinstance(value, dict) or set(value) != {"i", "r", "s", "v", "z"}:
                    raise ValueError("invalid cursor payload")
                if (
                    value["z"] != 1
                    or value["r"] != reference
                    or value["v"] != view
                    or value["s"] != _cursor_selector_binding(selector)
                    or type(value["i"]) is not int
                    or value["i"] < 0
                ):
                    raise ValueError("cursor binding mismatch")
                _reference_parts(reference, expected=None)
                if (
                    reference not in self._references
                    or reference in self._corrupt_references
                ):
                    raise ValueError("cursor reference has expired")
                return value["i"]
            except (TypeError, ValueError, UnicodeError):
                raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []}) from None

    def close(self) -> None:
        """Expire every reference and remove only this exact marked active session directory."""

        with self._mutex:
            if self._closed:
                return
            self._closed = True
            self._scope_registry.unregister_session(self._session_id)
            self._references.clear()
            try:
                marker = self._directory / _MARKER_NAME
                if self._directory.exists() and self._valid_marker(marker):
                    if os.name == "nt" and self._lock_descriptor >= 0:
                        _unlock_file(self._lock_descriptor)
                        os.close(self._lock_descriptor)
                        self._lock_descriptor = -1
                    shutil.rmtree(self._directory)
            except OSError as exc:
                raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc
            finally:
                if self._lock_descriptor >= 0:
                    _unlock_file(self._lock_descriptor)
                    os.close(self._lock_descriptor)
                    self._lock_descriptor = -1

    @staticmethod
    def _valid_marker(marker: Path) -> bool:
        try:
            content = _read_private(marker, maximum_bytes=1024)
            return bool(strict_cas_json_loads(content, maximum_bytes=1024) == _MARKER_VALUE)
        except (OSError, TypeError, ValueError):
            return False

    def __enter__(self) -> SessionCas:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def _safe_orphan_tree(directory: Path) -> bool:
    try:
        _validate_owned_directory(directory, private=True)
        for root, directories, files in os.walk(directory, followlinks=False):
            for name in (*directories, *files):
                info = (Path(root) / name).lstat()
                if stat.S_ISLNK(info.st_mode) or not _owner_matches(info):
                    return False
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    return False
        return True
    except (OSError, ValueError):
        return False


def cleanup_orphan_sessions(
    state_root: Path,
    *,
    now: float | None = None,
) -> tuple[Path, ...]:
    """Remove only marked, owned, unlocked application sessions strictly older than 24 hours."""

    root = Path(state_root)
    try:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise ValueError("owner-private CAS is not implemented on this platform")
        if not root.is_absolute() or Path(os.path.abspath(root)) != root:
            raise ValueError("session-state root is not a normalized absolute path")
        if root in {Path(root.anchor), Path.home().resolve()}:
            raise ValueError("session-state root is too broad")
        if root.resolve(strict=True) != root:
            raise ValueError("session-state root must not traverse symbolic links")
        _validate_owned_directory(root, private=True)
    except (OSError, ValueError) as exc:
        raise _fail(HostFailureCode.RECORD_PUBLICATION_FAILED) from exc
    current_time = time.time() if now is None else now
    removed: list[Path] = []
    for candidate in sorted(root.iterdir(), key=lambda item: item.name.encode("utf-8")):
        if not candidate.name.startswith(_SESSION_PREFIX) or not _safe_orphan_tree(candidate):
            continue
        marker = candidate / _MARKER_NAME
        lock_path = candidate / _LOCK_NAME
        try:
            marker_info = marker.lstat()
            if current_time - marker_info.st_mtime <= ORPHAN_SESSION_AGE_SECONDS:
                continue
            if not SessionCas._valid_marker(marker):
                continue
            descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            continue
        locked = False
        try:
            locked = _lock_file(descriptor, blocking=False)
            if not locked:
                continue
            before = candidate.lstat()
            if not _safe_orphan_tree(candidate):
                continue
            after = candidate.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                continue
            if os.name == "nt":  # Windows refuses deletion while its lock file is open.
                _unlock_file(descriptor)
                os.close(descriptor)
                descriptor = -1
                locked = False
                if not _safe_orphan_tree(candidate):
                    continue
            shutil.rmtree(candidate)
            removed.append(candidate)
        except OSError:
            continue
        finally:
            if locked:
                _unlock_file(descriptor)
            if descriptor >= 0:
                os.close(descriptor)
    return tuple(removed)


__all__ = [
    "MAX_CURSOR_BYTES",
    "MAX_DATASET_PAYLOAD_BYTES",
    "MAX_OPERATION_BUNDLE_BYTES",
    "MAX_SESSION_BYTES",
    "ORPHAN_SESSION_AGE_SECONDS",
    "CasLimits",
    "SessionCas",
    "SessionScopeRegistry",
    "StoredDataset",
    "StoredOperation",
    "cleanup_orphan_sessions",
]
