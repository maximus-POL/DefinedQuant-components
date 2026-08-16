"""Transport-neutral selection and contracts for local host platform capabilities."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, cast

from defined_quant.worker_process import WorkerProcessError, WorkerProcessProvider


class SecureFilesystemErrorCode(StrEnum):
    """Closed internal failures exposed by secure-filesystem providers."""

    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    ACCESS_DENIED = "access_denied"
    UNSAFE_PATH = "unsafe_path"
    NOT_REGULAR_FILE = "not_regular_file"
    BYTE_LIMIT_EXCEEDED = "byte_limit_exceeded"
    FILE_CHANGED = "file_changed"
    NOT_PRIVATE = "not_private"
    ALREADY_EXISTS = "already_exists"
    LOCK_UNAVAILABLE = "lock_unavailable"
    PUBLICATION_FAILED = "publication_failed"
    REMOVE_FAILED = "remove_failed"


class SecureFilesystemError(Exception):
    """Stable provider failure that never contains native error or path text."""

    def __init__(
        self,
        code: SecureFilesystemErrorCode,
        *,
        actual: int | None = None,
        maximum: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.actual = actual
        self.maximum = maximum


class PinnedRootHandle:
    """Opaque lifetime handle for one configured input root."""

    __slots__ = ()


class SessionLockHandle:
    """Opaque lifetime handle for one live session lock."""

    __slots__ = ()


class SecureFilesystem(Protocol):
    """Small secure-filesystem surface required by the transport-neutral host."""

    def pin_configured_root(self, root: Path) -> PinnedRootHandle: ...

    def close_pinned_root(self, handle: PinnedRootHandle) -> None: ...

    def read_file_beneath(
        self,
        handle: PinnedRootHandle,
        parts: Sequence[str],
        *,
        maximum_bytes: int,
    ) -> bytes: ...

    def read_regular_file(self, path: Path, *, maximum_bytes: int) -> bytes: ...

    def create_private_directory(
        self,
        path: Path,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None: ...

    def verify_private_directory(self, path: Path) -> None: ...

    def create_private_file(self, path: Path, content: bytes) -> None: ...

    def read_private_file(self, path: Path, *, maximum_bytes: int) -> bytes: ...

    def private_tree_size(self, path: Path) -> int: ...

    def acquire_session_lock(
        self,
        path: Path,
        *,
        blocking: bool,
    ) -> SessionLockHandle | None: ...

    def release_session_lock(self, handle: SessionLockHandle) -> None: ...

    def publish_directory_no_replace(self, source: Path, destination: Path) -> None: ...

    def remove_private_tree(self, path: Path) -> None: ...

    def remove_locked_session_tree(
        self,
        path: Path,
        handle: SessionLockHandle,
    ) -> None: ...

    def cleanup_orphan_sessions(
        self,
        root: Path,
        *,
        session_prefix: str,
        marker_name: str,
        lock_name: str,
        minimum_age_seconds: int,
        now: float,
        marker_validator: Callable[[bytes], bool],
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class LocalHostPlatform:
    """Selected local provider set, independent of any MCP transport."""

    provider_id: Literal["darwin", "linux", "win32"]
    secure_filesystem: SecureFilesystem
    worker_processes: WorkerProcessProvider
    state_namespace: str


def _select_local_host_platform() -> LocalHostPlatform | None:
    if os.name == "posix" and sys.platform == "linux":
        from defined_quant._posix_local_host import PosixSecureFilesystem
        from defined_quant._posix_worker import PosixWorkerProcessProvider

        provider = PosixSecureFilesystem(publication_api="renameat2")
        workers = PosixWorkerProcessProvider(platform_id="linux")
        return LocalHostPlatform(
            provider_id="linux",
            secure_filesystem=cast(SecureFilesystem, provider),
            worker_processes=cast(WorkerProcessProvider, workers),
            state_namespace=provider.state_namespace,
        )
    if os.name == "posix" and sys.platform == "darwin":
        from defined_quant._posix_local_host import PosixSecureFilesystem
        from defined_quant._posix_worker import PosixWorkerProcessProvider

        provider = PosixSecureFilesystem(publication_api="renamex_np")
        workers = PosixWorkerProcessProvider(platform_id="darwin")
        return LocalHostPlatform(
            provider_id="darwin",
            secure_filesystem=cast(SecureFilesystem, provider),
            worker_processes=cast(WorkerProcessProvider, workers),
            state_namespace=provider.state_namespace,
        )
    if os.name == "nt" and sys.platform == "win32":
        from defined_quant._windows_local_host import WindowsSecureFilesystem
        from defined_quant._windows_worker import WindowsWorkerProcessProvider

        try:
            secure_filesystem = WindowsSecureFilesystem()
            workers = WindowsWorkerProcessProvider()
        except (SecureFilesystemError, WorkerProcessError):
            return None
        return LocalHostPlatform(
            provider_id="win32",
            secure_filesystem=cast(SecureFilesystem, secure_filesystem),
            worker_processes=cast(WorkerProcessProvider, workers),
            state_namespace=secure_filesystem.state_namespace,
        )
    return None


_LOCAL_HOST_PLATFORM = _select_local_host_platform()


def implemented_provider_id() -> Literal["darwin", "linux", "win32"] | None:
    """Return the implemented provider selected for this process, if any."""

    return None if _LOCAL_HOST_PLATFORM is None else _LOCAL_HOST_PLATFORM.provider_id


def local_host_platform() -> LocalHostPlatform:
    """Return the selected platform facade or fail with one stable internal code."""

    if _LOCAL_HOST_PLATFORM is None:
        raise SecureFilesystemError(SecureFilesystemErrorCode.CAPABILITY_UNAVAILABLE)
    return _LOCAL_HOST_PLATFORM


__all__ = [
    "LocalHostPlatform",
    "PinnedRootHandle",
    "SecureFilesystem",
    "SecureFilesystemError",
    "SecureFilesystemErrorCode",
    "SessionLockHandle",
    "implemented_provider_id",
    "local_host_platform",
]
