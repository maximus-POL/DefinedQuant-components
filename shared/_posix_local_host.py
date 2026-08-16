"""POSIX secure-filesystem provider for the local host platform facade."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Literal

from defined_quant.local_host_platform import (
    PinnedRootHandle,
    SecureFilesystemError,
    SecureFilesystemErrorCode,
    SessionLockHandle,
)

_AT_FDCWD = -100
_RENAME_NOREPLACE = 0x00000001
_RENAME_EXCL = 0x00000004


@dataclass(slots=True, repr=False)
class _PosixPinnedRoot(PinnedRootHandle):
    _descriptor: int
    _device: int
    _inode: int
    _closed: bool = False
    _lock: RLock = field(default_factory=RLock)

    def __repr__(self) -> str:
        return "<PinnedRootHandle>"


@dataclass(slots=True, repr=False)
class _PosixSessionLock(SessionLockHandle):
    _descriptor: int
    _released: bool = False

    def __repr__(self) -> str:
        return "<SessionLockHandle>"


def _failure(
    code: SecureFilesystemErrorCode,
    *,
    actual: int | None = None,
    maximum: int | None = None,
) -> SecureFilesystemError:
    return SecureFilesystemError(code, actual=actual, maximum=maximum)


def _owner_matches(info: os.stat_result) -> bool:
    return info.st_uid == os.getuid()


def _private_mode(info: os.stat_result, *, directory: bool) -> bool:
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return expected_type and info.st_mode & 0o077 == 0


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _read_descriptor(
    descriptor: int,
    metadata: os.stat_result,
    *,
    maximum_bytes: int,
) -> bytes:
    if metadata.st_size > maximum_bytes:
        raise _failure(
            SecureFilesystemErrorCode.BYTE_LIMIT_EXCEEDED,
            actual=metadata.st_size,
            maximum=maximum_bytes,
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum_bytes:
            raise _failure(
                SecureFilesystemErrorCode.BYTE_LIMIT_EXCEEDED,
                actual=total,
                maximum=maximum_bytes,
            )
    after = os.fstat(descriptor)
    if (
        after.st_dev != metadata.st_dev
        or after.st_ino != metadata.st_ino
        or after.st_size != metadata.st_size
        or total != metadata.st_size
    ):
        raise _failure(SecureFilesystemErrorCode.FILE_CHANGED)
    return b"".join(chunks)


class PosixSecureFilesystem:
    """Current Linux/macOS implementation of the secure-filesystem contract."""

    def __init__(self, *, publication_api: Literal["renameat2", "renamex_np"]) -> None:
        self._publication_api = publication_api

    @property
    def state_namespace(self) -> str:
        """Preserve the existing per-UID default state-root namespace."""

        return str(os.getuid())

    def pin_configured_root(self, root: Path) -> PinnedRootHandle:
        descriptor = -1
        try:
            if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
                raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
            descriptor = os.open(
                root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            metadata = os.fstat(descriptor)
            path_metadata = root.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
            handle = _PosixPinnedRoot(
                _descriptor=descriptor,
                _device=metadata.st_dev,
                _inode=metadata.st_ino,
            )
            descriptor = -1
            return handle
        except SecureFilesystemError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        finally:
            if descriptor >= 0:
                _close_descriptor(descriptor)

    def close_pinned_root(self, handle: PinnedRootHandle) -> None:
        pinned = self._pinned(handle)
        with pinned._lock:
            if pinned._closed:
                return
            pinned._closed = True
            _close_descriptor(pinned._descriptor)

    def read_file_beneath(
        self,
        handle: PinnedRootHandle,
        parts: Sequence[str],
        *,
        maximum_bytes: int,
    ) -> bytes:
        pinned = self._pinned(handle)
        descriptors: list[int] = []
        try:
            if not parts or any(not part or part in {".", ".."} for part in parts):
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            with pinned._lock:
                if pinned._closed:
                    raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
                descriptors.append(os.dup(pinned._descriptor))
            pinned_metadata = os.fstat(descriptors[0])
            if (pinned_metadata.st_dev, pinned_metadata.st_ino) != (
                pinned._device,
                pinned._inode,
            ):
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            current = descriptors[-1]
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            for part in parts[:-1]:
                current = os.open(part, directory_flags, dir_fd=current)
                descriptors.append(current)
            file_descriptor = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=current,
            )
            descriptors.append(file_descriptor)
            metadata = os.fstat(file_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
            return _read_descriptor(
                file_descriptor,
                metadata,
                maximum_bytes=maximum_bytes,
            )
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def read_regular_file(self, path: Path, *, maximum_bytes: int) -> bytes:
        descriptor = -1
        try:
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino
                or not stat.S_ISREG(opened.st_mode)
            ):
                raise _failure(SecureFilesystemErrorCode.FILE_CHANGED)
            return _read_descriptor(descriptor, metadata, maximum_bytes=maximum_bytes)
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None
        finally:
            if descriptor >= 0:
                _close_descriptor(descriptor)

    def create_private_directory(
        self,
        path: Path,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        try:
            path.mkdir(mode=0o700, parents=parents, exist_ok=exist_ok)
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            os.chmod(path, 0o700)
            self.verify_private_directory(path)
        except FileExistsError:
            raise _failure(SecureFilesystemErrorCode.ALREADY_EXISTS) from None
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None

    def ensure_directory_path(self, path: Path) -> None:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None

    def verify_directory_path(self, path: Path) -> None:
        try:
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None

    def verify_private_directory(self, path: Path) -> None:
        try:
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or not _owner_matches(metadata)
                or not _private_mode(metadata, directory=True)
            ):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE) from None

    def create_private_file(self, path: Path, content: bytes) -> None:
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
                view = view[written:]
            os.fsync(descriptor)
        except FileExistsError:
            raise _failure(SecureFilesystemErrorCode.ALREADY_EXISTS) from None
        except SecureFilesystemError:
            raise
        except (OSError, TypeError, ValueError):
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        finally:
            if descriptor >= 0:
                _close_descriptor(descriptor)

    def read_private_file(self, path: Path, *, maximum_bytes: int) -> bytes:
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            metadata = os.fstat(descriptor)
            if not _owner_matches(metadata) or not _private_mode(metadata, directory=False):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            return _read_descriptor(descriptor, metadata, maximum_bytes=maximum_bytes)
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None
        finally:
            if descriptor >= 0:
                _close_descriptor(descriptor)

    def private_tree_size(self, path: Path) -> int:
        total = 0
        try:
            for root, directories, files in os.walk(path, followlinks=False):
                for name in (*directories, *files):
                    candidate = Path(root) / name
                    metadata = candidate.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or not _owner_matches(metadata):
                        raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
                    if stat.S_ISREG(metadata.st_mode):
                        total += metadata.st_size
                    elif not stat.S_ISDIR(metadata.st_mode):
                        raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
            return total
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None

    def acquire_session_lock(
        self,
        path: Path,
        *,
        blocking: bool,
    ) -> SessionLockHandle | None:
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(descriptor, flags)
            except BlockingIOError:
                os.close(descriptor)
                descriptor = -1
                return None
            handle = _PosixSessionLock(_descriptor=descriptor)
            descriptor = -1
            return handle
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE) from None
        finally:
            if descriptor >= 0:
                _close_descriptor(descriptor)

    def release_session_lock(self, handle: SessionLockHandle) -> None:
        lock = self._session_lock(handle)
        if lock._released:
            return
        lock._released = True
        try:
            fcntl.flock(lock._descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(lock._descriptor)
        except OSError:
            pass

    def publish_directory_no_replace(self, source: Path, destination: Path) -> None:
        try:
            source_bytes = os.fsencode(source)
            destination_bytes = os.fsencode(destination)
            libc = ctypes.CDLL(None, use_errno=True)
            if self._publication_api == "renameat2":
                renameat2 = getattr(libc, "renameat2", None)
                if renameat2 is None:
                    raise _failure(SecureFilesystemErrorCode.CAPABILITY_UNAVAILABLE)
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
            else:
                renamex_np = getattr(libc, "renamex_np", None)
                if renamex_np is None:
                    raise _failure(SecureFilesystemErrorCode.CAPABILITY_UNAVAILABLE)
                renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
                renamex_np.restype = ctypes.c_int
                result = renamex_np(source_bytes, destination_bytes, _RENAME_EXCL)
            if result != 0:
                error_number = ctypes.get_errno()
                if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise _failure(SecureFilesystemErrorCode.ALREADY_EXISTS)
                raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED)
        except SecureFilesystemError:
            raise
        except (OSError, TypeError, ValueError):
            raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED) from None

    def remove_private_tree(self, path: Path) -> None:
        try:
            if path.is_symlink():
                raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
            shutil.rmtree(path)
        except SecureFilesystemError:
            raise
        except (OSError, ValueError):
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None

    def remove_locked_session_tree(
        self,
        path: Path,
        handle: SessionLockHandle,
    ) -> None:
        try:
            self.remove_private_tree(path)
        finally:
            self.release_session_lock(handle)

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
    ) -> tuple[str, ...]:
        try:
            if not root.is_absolute() or Path(os.path.abspath(root)) != root:
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            if root in {Path(root.anchor), Path.home().resolve()}:
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            if root.resolve(strict=True) != root:
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            self.verify_private_directory(root)
        except SecureFilesystemError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None

        removed: list[str] = []
        try:
            candidates = sorted(root.iterdir(), key=lambda item: item.name.encode("utf-8"))
        except (OSError, UnicodeError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None
        for candidate in candidates:
            if not candidate.name.startswith(session_prefix) or not self._safe_tree(candidate):
                continue
            marker = candidate / marker_name
            lock_path = candidate / lock_name
            try:
                marker_info = marker.lstat()
                if now - marker_info.st_mtime <= minimum_age_seconds:
                    continue
                marker_content = self.read_private_file(marker, maximum_bytes=1024)
                try:
                    marker_is_valid = marker_validator(marker_content)
                except Exception:
                    marker_is_valid = False
                if not marker_is_valid:
                    continue
                lock = self.acquire_session_lock(lock_path, blocking=False)
            except (OSError, SecureFilesystemError):
                continue
            if lock is None:
                continue
            try:
                before = candidate.lstat()
                if not self._safe_tree(candidate):
                    continue
                after = candidate.lstat()
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    continue
                self.remove_private_tree(candidate)
                removed.append(candidate.name)
            except (OSError, SecureFilesystemError):
                continue
            finally:
                self.release_session_lock(lock)
        return tuple(removed)

    @staticmethod
    def _pinned(handle: PinnedRootHandle) -> _PosixPinnedRoot:
        if not isinstance(handle, _PosixPinnedRoot):
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
        return handle

    @staticmethod
    def _session_lock(handle: SessionLockHandle) -> _PosixSessionLock:
        if not isinstance(handle, _PosixSessionLock):
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
        return handle

    def _safe_tree(self, directory: Path) -> bool:
        try:
            self.verify_private_directory(directory)
            for root, directories, files in os.walk(directory, followlinks=False):
                for name in (*directories, *files):
                    metadata = (Path(root) / name).lstat()
                    if stat.S_ISLNK(metadata.st_mode) or not _owner_matches(metadata):
                        return False
                    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                        return False
            return True
        except (OSError, SecureFilesystemError):
            return False


__all__ = ["PosixSecureFilesystem"]
