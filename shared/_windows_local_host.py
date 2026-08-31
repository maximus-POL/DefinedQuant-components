"""Native Windows configured-root reads and owner-private session storage."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import secrets
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, NoReturn

from defined_quant.local_host_platform import (
    PinnedRootHandle,
    SecureFilesystemError,
    SecureFilesystemErrorCode,
    SessionLockHandle,
)

_ARCHIVE_SUFFIXES = (
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
)
_WINDOWS_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
        *(f"com{index}" for index in ("¹", "²", "³")),
        *(f"lpt{index}" for index in ("¹", "²", "³")),
    }
)
_DRIVE_PATH = re.compile(r"^[A-Za-z]:\\")
_INVALID_SEGMENT_CHARACTERS = frozenset('<>:"/\\|?*')

_FILE_LIST_DIRECTORY = 0x0001
_FILE_READ_DATA = 0x0001
_FILE_WRITE_DATA = 0x0002
_FILE_ADD_FILE = 0x0002
_FILE_ADD_SUBDIRECTORY = 0x0004
_FILE_TRAVERSE = 0x0020
_FILE_READ_ATTRIBUTES = 0x0080
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_SYNCHRONIZE = 0x00100000
_FILE_ALL_ACCESS = 0x001F01FF
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000

_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_ALL_SHARING = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE

_OPEN_EXISTING = 3
_FILE_OPEN = 1
_FILE_CREATE = 2
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_FILE_NON_DIRECTORY_FILE = 0x00000040
_FILE_OPEN_REPARSE_POINT = 0x00200000
_OBJ_CASE_INSENSITIVE = 0x00000040

_FILE_TYPE_DISK = 0x0001
_DRIVE_FIXED = 3
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_ID_INFO_CLASS = 18
_FILE_IS_REMOTE_DEVICE_INFO_CLASS = 51
_FILE_BASIC_INFO_CLASS = 0
_FILE_RENAME_INFORMATION_CLASS = 10
_FILE_DISPOSITION_INFO_CLASS = 4
_ERROR_HANDLE_EOF = 38
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_LOCK_VIOLATION = 33
_ERROR_IO_PENDING = 997
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_STATUS_FILE_IS_A_DIRECTORY = 0xC00000BA
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035
_STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
_STATUS_OBJECT_PATH_NOT_FOUND = 0xC000003A

_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1
_SDDL_REVISION_1 = 1
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_SE_DACL_PROTECTED = 0x1000
_ACL_SIZE_INFORMATION_CLASS = 2
_ACCESS_ALLOWED_ACE_TYPE = 0
_INHERITED_ACE = 0x10

_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_LOCK_RANGE_LOW = 1
_LOCK_NAMESPACE = ".defined-quant-session-locks-v1"
_CLEANUP_TOMBSTONE_PREFIX = ".defined-quant-cleanup-"
_DELETE_TOMBSTONE_PREFIX = ".defined-quant-delete-"
_WINDOWS_EPOCH_SECONDS = 11_644_473_600

_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_READ_CHUNK_BYTES = 1024 * 1024


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IO_STATUS_BLOCK_VALUE(ctypes.Union):
    _fields_ = [("Status", ctypes.c_int32), ("Pointer", ctypes.c_void_p)]


class _IO_STATUS_BLOCK(ctypes.Structure):
    _anonymous_ = ("Value",)
    _fields_ = [("Value", _IO_STATUS_BLOCK_VALUE), ("Information", ctypes.c_size_t)]


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


class _FILE_ID_128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", _FILE_ID_128)]


class _FILE_IS_REMOTE_DEVICE_INFORMATION(ctypes.Structure):
    _fields_ = [("IsRemote", ctypes.c_ubyte)]


class _FILE_BASIC_INFO(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
    ]


class _OVERLAPPED_OFFSET(ctypes.Structure):
    _fields_ = [("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD)]


class _OVERLAPPED_VALUE(ctypes.Union):
    _anonymous_ = ("Position",)
    _fields_ = [("Position", _OVERLAPPED_OFFSET), ("Pointer", ctypes.c_void_p)]


class _OVERLAPPED(ctypes.Structure):
    _anonymous_ = ("Value",)
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Value", _OVERLAPPED_VALUE),
        ("hEvent", wintypes.HANDLE),
    ]


class _FILE_RENAME_FIELD(ctypes.Union):
    _fields_ = [("ReplaceIfExists", ctypes.c_ubyte), ("Flags", wintypes.DWORD)]


class _FILE_RENAME_INFO(ctypes.Structure):
    _anonymous_ = ("Rename",)
    _fields_ = [
        ("Rename", _FILE_RENAME_FIELD),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


class _FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.USHORT),
    ]


class _ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [
        ("Header", _ACE_HEADER),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class _NativePathMissing(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _HandleIdentity:
    volume: int
    file_id: bytes


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    identity: _HandleIdentity
    size: int


@dataclass(slots=True, repr=False)
class _WindowsPinnedRoot(PinnedRootHandle):
    _handle: int
    _identity: _HandleIdentity
    _closed: bool = False
    _lock: RLock = field(default_factory=RLock)

    def __repr__(self) -> str:
        return "<PinnedRootHandle>"


@dataclass(slots=True, repr=False)
class _WindowsSessionLock(SessionLockHandle):
    _handle: int
    _overlapped: _OVERLAPPED
    _state_key: tuple[str, ...]
    _session_name: str
    _identity: _HandleIdentity
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


def _stable_unavailable() -> NoReturn:
    raise _failure(SecureFilesystemErrorCode.CAPABILITY_UNAVAILABLE)


def _validate_segment(segment: str) -> None:
    if (
        not segment
        or segment in {".", ".."}
        or segment.endswith((".", " "))
        or any(character in _INVALID_SEGMENT_CHARACTERS for character in segment)
        or any(ord(character) < 32 for character in segment)
        or segment.casefold().split(".", 1)[0] in _WINDOWS_DEVICE_NAMES
    ):
        raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)


def _validate_member_parts(parts: Sequence[str]) -> tuple[str, ...]:
    if not parts:
        raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
    normalized = tuple(parts)
    for part in normalized:
        if not isinstance(part, str):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        _validate_segment(part)
    if normalized[-1].casefold().endswith(_ARCHIVE_SUFFIXES):
        raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
    return normalized


def _absolute_drive_parts(path: Path, *, reject_archive: bool) -> tuple[str, tuple[str, ...]]:
    value = str(path)
    folded = value.casefold()
    if (
        "\x00" in value
        or not _DRIVE_PATH.match(value)
        or value.startswith(("\\\\", "//"))
        or folded.startswith(("\\\\?\\", "\\\\.\\", "\\??\\", "\\device\\"))
    ):
        raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
    drive = value[:2]
    tail = value[3:]
    parts = () if not tail else tuple(tail.split("\\"))
    for part in parts:
        _validate_segment(part)
    if reject_archive and parts and parts[-1].casefold().endswith(_ARCHIVE_SUFFIXES):
        raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
    return drive, parts


class _WindowsApi:
    def __init__(self) -> None:
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            _stable_unavailable()
        self.kernel32: Any = loader("kernel32", use_last_error=True)
        self.ntdll: Any = loader("ntdll", use_last_error=True)
        self.advapi32: Any = loader("advapi32", use_last_error=True)

        self.CreateFileW = self.kernel32.CreateFileW
        self.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.CreateFileW.restype = wintypes.HANDLE

        self.CloseHandle = self.kernel32.CloseHandle
        self.CloseHandle.argtypes = [wintypes.HANDLE]
        self.CloseHandle.restype = wintypes.BOOL

        self.GetDriveTypeW = self.kernel32.GetDriveTypeW
        self.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.GetDriveTypeW.restype = wintypes.UINT

        self.GetFileType = self.kernel32.GetFileType
        self.GetFileType.argtypes = [wintypes.HANDLE]
        self.GetFileType.restype = wintypes.DWORD

        self.GetFileInformationByHandleEx = self.kernel32.GetFileInformationByHandleEx
        self.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.GetFileInformationByHandleEx.restype = wintypes.BOOL

        self.GetFileSizeEx = self.kernel32.GetFileSizeEx
        self.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
        self.GetFileSizeEx.restype = wintypes.BOOL

        self.ReadFile = self.kernel32.ReadFile
        self.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.ReadFile.restype = wintypes.BOOL

        self.WriteFile = self.kernel32.WriteFile
        self.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.WriteFile.restype = wintypes.BOOL

        self.FlushFileBuffers = self.kernel32.FlushFileBuffers
        self.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        self.FlushFileBuffers.restype = wintypes.BOOL

        self.LockFileEx = self.kernel32.LockFileEx
        self.LockFileEx.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_OVERLAPPED),
        ]
        self.LockFileEx.restype = wintypes.BOOL

        self.UnlockFileEx = self.kernel32.UnlockFileEx
        self.UnlockFileEx.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_OVERLAPPED),
        ]
        self.UnlockFileEx.restype = wintypes.BOOL

        self.SetFileInformationByHandle = self.kernel32.SetFileInformationByHandle
        self.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.SetFileInformationByHandle.restype = wintypes.BOOL

        self.GetCurrentProcess = self.kernel32.GetCurrentProcess
        self.GetCurrentProcess.argtypes = []
        self.GetCurrentProcess.restype = wintypes.HANDLE

        self.LocalFree = self.kernel32.LocalFree
        self.LocalFree.argtypes = [ctypes.c_void_p]
        self.LocalFree.restype = ctypes.c_void_p

        self.OpenProcessToken = self.advapi32.OpenProcessToken
        self.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self.OpenProcessToken.restype = wintypes.BOOL

        self.GetTokenInformation = self.advapi32.GetTokenInformation
        self.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.GetTokenInformation.restype = wintypes.BOOL

        self.GetLengthSid = self.advapi32.GetLengthSid
        self.GetLengthSid.argtypes = [ctypes.c_void_p]
        self.GetLengthSid.restype = wintypes.DWORD

        self.ConvertSidToStringSidW = self.advapi32.ConvertSidToStringSidW
        self.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self.ConvertSidToStringSidW.restype = wintypes.BOOL

        self.ConvertStringSecurityDescriptorToSecurityDescriptorW = (
            self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        )
        self.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

        self.GetSecurityInfo = self.advapi32.GetSecurityInfo
        self.GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.GetSecurityInfo.restype = wintypes.DWORD

        self.GetSecurityDescriptorControl = self.advapi32.GetSecurityDescriptorControl
        self.GetSecurityDescriptorControl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.USHORT),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.GetSecurityDescriptorControl.restype = wintypes.BOOL

        self.GetAclInformation = self.advapi32.GetAclInformation
        self.GetAclInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_int,
        ]
        self.GetAclInformation.restype = wintypes.BOOL

        self.GetAce = self.advapi32.GetAce
        self.GetAce.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.GetAce.restype = wintypes.BOOL

        self.EqualSid = self.advapi32.EqualSid
        self.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.EqualSid.restype = wintypes.BOOL

        self.NtCreateFile = self.ntdll.NtCreateFile
        self.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_OBJECT_ATTRIBUTES),
            ctypes.POINTER(_IO_STATUS_BLOCK),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
        ]
        self.NtCreateFile.restype = ctypes.c_int32

        self.NtQueryInformationFile = self.ntdll.NtQueryInformationFile
        self.NtQueryInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IO_STATUS_BLOCK),
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        self.NtQueryInformationFile.restype = ctypes.c_int32

        self.NtSetInformationFile = self.ntdll.NtSetInformationFile
        self.NtSetInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IO_STATUS_BLOCK),
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        self.NtSetInformationFile.restype = ctypes.c_int32


class WindowsSecureFilesystem:
    """Handle-contained Windows reads and owner-private session objects."""

    def __init__(self) -> None:
        try:
            self._api = _WindowsApi()
            self._owner_sid, self._owner_sid_text = self._current_user_sid()
            self._private_security_descriptor = self._make_private_security_descriptor()
            self._known_private_objects: dict[
                tuple[str, ...], tuple[_HandleIdentity, bool]
            ] = {}
            self._private_lock = RLock()
        except SecureFilesystemError:
            raise
        except Exception:
            _stable_unavailable()

    @property
    def state_namespace(self) -> str:
        """Return a stable, non-SID user-local namespace for noncanonical state paths."""

        sid_bytes = ctypes.string_at(self._owner_sid, self._api.GetLengthSid(self._owner_sid))
        return f"sid-{hashlib.sha256(sid_bytes).hexdigest()[:16]}"

    def __del__(self) -> None:
        descriptor = getattr(self, "_private_security_descriptor", None)
        if descriptor:
            try:
                self._api.LocalFree(descriptor)
            except Exception:
                pass

    def _current_user_sid(self) -> tuple[ctypes.Array[ctypes.c_char], str]:
        token = wintypes.HANDLE()
        if not self._api.OpenProcessToken(
            self._api.GetCurrentProcess(),
            _TOKEN_QUERY,
            ctypes.byref(token),
        ):
            _stable_unavailable()
        try:
            required = wintypes.DWORD()
            self._api.GetTokenInformation(
                token,
                _TOKEN_USER_CLASS,
                None,
                0,
                ctypes.byref(required),
            )
            get_last_error = getattr(ctypes, "get_last_error")
            if required.value == 0 or get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
                _stable_unavailable()
            token_info = ctypes.create_string_buffer(required.value)
            if not self._api.GetTokenInformation(
                token,
                _TOKEN_USER_CLASS,
                token_info,
                required,
                ctypes.byref(required),
            ):
                _stable_unavailable()
            user = ctypes.cast(token_info, ctypes.POINTER(_TOKEN_USER)).contents
            sid_length = int(self._api.GetLengthSid(user.User.Sid))
            if sid_length <= 0:
                _stable_unavailable()
            sid = ctypes.create_string_buffer(sid_length)
            ctypes.memmove(sid, user.User.Sid, sid_length)
            sid_text_pointer = wintypes.LPWSTR()
            if not self._api.ConvertSidToStringSidW(sid, ctypes.byref(sid_text_pointer)):
                _stable_unavailable()
            try:
                sid_text = str(sid_text_pointer.value)
            finally:
                self._api.LocalFree(ctypes.cast(sid_text_pointer, ctypes.c_void_p))
            return sid, sid_text
        finally:
            self._close(int(token.value or 0))

    def _make_private_security_descriptor(self) -> int:
        descriptor = ctypes.c_void_p()
        sddl = f"O:{self._owner_sid_text}D:P(A;;FA;;;{self._owner_sid_text})"
        if not self._api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            _SDDL_REVISION_1,
            ctypes.byref(descriptor),
            None,
        ) or not descriptor.value:
            _stable_unavailable()
        return int(descriptor.value)

    def _close(self, handle: int) -> None:
        if handle not in {0, _INVALID_HANDLE_VALUE}:
            self._api.CloseHandle(wintypes.HANDLE(handle))

    def _verify_private_handle(self, handle: int) -> None:
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        result = self._api.GetSecurityInfo(
            wintypes.HANDLE(handle),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result != 0 or not descriptor.value:
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
        try:
            if not owner.value or not self._api.EqualSid(owner, self._owner_sid):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            control = wintypes.USHORT()
            revision = wintypes.DWORD()
            if not self._api.GetSecurityDescriptorControl(
                descriptor,
                ctypes.byref(control),
                ctypes.byref(revision),
            ) or not control.value & _SE_DACL_PROTECTED:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            if not dacl.value:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            acl_info = _ACL_SIZE_INFORMATION()
            if not self._api.GetAclInformation(
                dacl,
                ctypes.byref(acl_info),
                ctypes.sizeof(acl_info),
                _ACL_SIZE_INFORMATION_CLASS,
            ) or acl_info.AceCount != 1:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            ace_pointer = ctypes.c_void_p()
            if not self._api.GetAce(dacl, 0, ctypes.byref(ace_pointer)) or not ace_pointer.value:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)).contents
            if (
                ace.Header.AceType != _ACCESS_ALLOWED_ACE_TYPE
                or ace.Header.AceFlags != 0
                or ace.Header.AceFlags & _INHERITED_ACE
                or ace.Mask != _FILE_ALL_ACCESS
            ):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            sid_pointer = ctypes.c_void_p(
                int(ace_pointer.value) + _ACCESS_ALLOWED_ACE.SidStart.offset
            )
            if not self._api.EqualSid(sid_pointer, self._owner_sid):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
        finally:
            self._api.LocalFree(descriptor)

    @staticmethod
    def _path_key(drive: str, parts: Sequence[str]) -> tuple[str, ...]:
        return (drive.casefold(), *(part.casefold() for part in parts))

    @staticmethod
    def _path_from_parts(drive: str, parts: Sequence[str]) -> Path:
        return Path(f"{drive}\\").joinpath(*parts)

    def _remember_private(
        self,
        drive: str,
        parts: Sequence[str],
        identity: _HandleIdentity,
        *,
        directory: bool,
    ) -> None:
        key = self._path_key(drive, parts)
        with self._private_lock:
            known = self._known_private_objects.get(key)
            if known is not None and known != (identity, directory):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            self._known_private_objects[key] = (identity, directory)

    def _move_known_private_subtree(
        self,
        source_drive: str,
        source_parts: Sequence[str],
        destination_drive: str,
        destination_parts: Sequence[str],
    ) -> None:
        source_key = self._path_key(source_drive, source_parts)
        destination_key = self._path_key(destination_drive, destination_parts)
        with self._private_lock:
            moved: dict[tuple[str, ...], tuple[_HandleIdentity, bool]] = {}
            for key, value in tuple(self._known_private_objects.items()):
                if key[: len(source_key)] != source_key:
                    continue
                target = destination_key + key[len(source_key) :]
                existing = self._known_private_objects.get(target)
                if existing is not None and existing != value:
                    raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED)
                moved[target] = value
                del self._known_private_objects[key]
            self._known_private_objects.update(moved)

    def _forget_known_private_subtree(self, drive: str, parts: Sequence[str]) -> None:
        prefix = self._path_key(drive, parts)
        with self._private_lock:
            for key in tuple(self._known_private_objects):
                if key[: len(prefix)] == prefix:
                    del self._known_private_objects[key]

    def _verify_known_private(
        self,
        drive: str,
        parts: Sequence[str],
        handle: int,
        *,
        directory: bool,
        boundary_active: bool,
    ) -> bool:
        key = self._path_key(drive, parts)
        with self._private_lock:
            known = self._known_private_objects.get(key)
        if known is None and not boundary_active:
            return False
        self._verify_private_handle(handle)
        identity = self._identity(handle)
        if known is not None and known != (identity, directory):
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
        if known is None:
            self._remember_private(drive, parts, identity, directory=directory)
        return True

    def _attributes(self, handle: int) -> int:
        info = _FILE_ATTRIBUTE_TAG_INFO()
        if not self._api.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle),
            _FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        return int(info.FileAttributes)

    def _identity(self, handle: int) -> _HandleIdentity:
        info = _FILE_ID_INFO()
        if not self._api.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle),
            _FILE_ID_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        return _HandleIdentity(
            volume=int(info.VolumeSerialNumber),
            file_id=bytes(info.FileId.Identifier),
        )

    def _verify_local_disk_handle(self, handle: int) -> None:
        if self._api.GetFileType(wintypes.HANDLE(handle)) != _FILE_TYPE_DISK:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        io_status = _IO_STATUS_BLOCK()
        remote = _FILE_IS_REMOTE_DEVICE_INFORMATION()
        status = self._api.NtQueryInformationFile(
            wintypes.HANDLE(handle),
            ctypes.byref(io_status),
            ctypes.byref(remote),
            ctypes.sizeof(remote),
            _FILE_IS_REMOTE_DEVICE_INFO_CLASS,
        )
        if status < 0 or remote.IsRemote:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)

    def _verify_directory(self, handle: int, *, volume: int | None = None) -> _HandleIdentity:
        attributes = self._attributes(handle)
        self._verify_local_disk_handle(handle)
        if (
            attributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or not attributes & _FILE_ATTRIBUTE_DIRECTORY
        ):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        identity = self._identity(handle)
        if volume is not None and identity.volume != volume:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        return identity

    def _file_snapshot(self, handle: int, *, volume: int) -> _FileSnapshot:
        attributes = self._attributes(handle)
        self._verify_local_disk_handle(handle)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        if attributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
        identity = self._identity(handle)
        if identity.volume != volume:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        size = ctypes.c_longlong()
        if not self._api.GetFileSizeEx(wintypes.HANDLE(handle), ctypes.byref(size)):
            raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
        if size.value < 0:
            raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
        return _FileSnapshot(identity=identity, size=int(size.value))

    def _last_write_ticks(self, handle: int) -> int:
        info = _FILE_BASIC_INFO()
        if not self._api.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle),
            _FILE_BASIC_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        return int(info.LastWriteTime)

    def _open_drive_root(self, drive: str) -> int:
        if self._api.GetDriveTypeW(f"{drive}\\") != _DRIVE_FIXED:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        native_path = f"\\\\?\\{drive}\\"
        raw_handle = self._api.CreateFileW(
            native_path,
            _FILE_LIST_DIRECTORY | _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _ALL_SHARING,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        handle = int(raw_handle) if raw_handle is not None else 0
        if handle in {0, _INVALID_HANDLE_VALUE}:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        try:
            self._verify_directory(handle)
        except SecureFilesystemError:
            self._close(handle)
            raise
        return handle

    def _nt_open_relative(
        self,
        parent: int,
        name: str,
        *,
        directory: bool,
        desired_access: int,
        disposition: int,
        security_descriptor: int | None = None,
        share_access: int = _ALL_SHARING,
    ) -> tuple[int, int]:
        encoded = name.encode("utf-16-le")
        if len(encoded) > 0xFFFC:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _UNICODE_STRING(
            Length=len(encoded),
            MaximumLength=len(encoded) + 2,
            Buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = _OBJECT_ATTRIBUTES(
            Length=ctypes.sizeof(_OBJECT_ATTRIBUTES),
            RootDirectory=wintypes.HANDLE(parent),
            ObjectName=ctypes.pointer(unicode_name),
            Attributes=_OBJ_CASE_INSENSITIVE,
            SecurityDescriptor=security_descriptor,
            SecurityQualityOfService=None,
        )
        io_status = _IO_STATUS_BLOCK()
        output = wintypes.HANDLE()
        options = (
            _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
        ) | _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
        status = self._api.NtCreateFile(
            ctypes.byref(output),
            desired_access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            _FILE_ATTRIBUTE_NORMAL,
            share_access,
            disposition,
            options,
            None,
            0,
        )
        unsigned_status = ctypes.c_uint32(status).value
        if status < 0 or output.value is None:
            if not directory and unsigned_status == _STATUS_FILE_IS_A_DIRECTORY:
                raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
            return 0, unsigned_status
        return int(output.value), unsigned_status

    def _open_relative(self, parent: int, name: str, *, directory: bool) -> int:
        desired_access = (
            _FILE_LIST_DIRECTORY | _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
            if directory
            else _FILE_READ_DATA | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        )
        handle, _status = self._nt_open_relative(
            parent,
            name,
            directory=directory,
            desired_access=desired_access,
            disposition=_FILE_OPEN,
        )
        if not handle:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        return handle

    def _open_private_relative(
        self,
        parent: int,
        name: str,
        *,
        directory: bool,
        writable: bool = False,
        delete: bool = False,
        share_access: int = _ALL_SHARING,
    ) -> int:
        desired_access = (
            _FILE_LIST_DIRECTORY
            | _FILE_TRAVERSE
            | _FILE_READ_ATTRIBUTES
            | _READ_CONTROL
            | _SYNCHRONIZE
            | (_FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY if writable else 0)
            | (_DELETE if delete else 0)
            if directory
            else _FILE_READ_DATA
            | _FILE_READ_ATTRIBUTES
            | _READ_CONTROL
            | _SYNCHRONIZE
            | (_DELETE if delete else 0)
        )
        handle, _status = self._nt_open_relative(
            parent,
            name,
            directory=directory,
            desired_access=desired_access,
            disposition=_FILE_OPEN,
            share_access=share_access,
        )
        if not handle and _status in {
            _STATUS_OBJECT_NAME_NOT_FOUND,
            _STATUS_OBJECT_PATH_NOT_FOUND,
        }:
            raise _NativePathMissing
        if not handle:
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
        return handle

    def _create_private_relative(self, parent: int, name: str, *, directory: bool) -> int:
        desired_access = (
            _FILE_LIST_DIRECTORY
            | _FILE_ADD_FILE
            | _FILE_ADD_SUBDIRECTORY
            | _FILE_TRAVERSE
            | _FILE_READ_ATTRIBUTES
            | _READ_CONTROL
            | _SYNCHRONIZE
            if directory
            else _FILE_READ_DATA
            | _FILE_WRITE_DATA
            | _FILE_READ_ATTRIBUTES
            | _READ_CONTROL
            | _SYNCHRONIZE
        )
        handle, status = self._nt_open_relative(
            parent,
            name,
            directory=directory,
            desired_access=desired_access,
            disposition=_FILE_CREATE,
            security_descriptor=self._private_security_descriptor,
        )
        if handle:
            return handle
        if status == _STATUS_OBJECT_NAME_COLLISION:
            raise _failure(SecureFilesystemErrorCode.ALREADY_EXISTS)
        raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)

    def _open_directory_path(self, drive: str, parts: Sequence[str]) -> tuple[int, _HandleIdentity]:
        current = self._open_drive_root(drive)
        root_identity: _HandleIdentity | None = None
        try:
            root_identity = self._verify_directory(current)
            for part in parts:
                child = self._open_relative(current, part, directory=True)
                try:
                    self._verify_directory(child, volume=root_identity.volume)
                except SecureFilesystemError:
                    self._close(child)
                    raise
                self._close(current)
                current = child
            return current, root_identity
        except Exception:
            self._close(current)
            raise

    def _open_private_directory_path(
        self,
        drive: str,
        parts: Sequence[str],
        *,
        writable_final: bool = False,
    ) -> tuple[int, _HandleIdentity]:
        current = self._open_drive_root(drive)
        try:
            root_identity = self._verify_directory(current)
            boundary_active = False
            opened_parts: list[str] = []
            for index, part in enumerate(parts):
                child = self._open_private_relative(
                    current,
                    part,
                    directory=True,
                    writable=writable_final and index == len(parts) - 1,
                )
                try:
                    self._verify_directory(child, volume=root_identity.volume)
                    opened_parts.append(part)
                    boundary_active = self._verify_known_private(
                        drive,
                        opened_parts,
                        child,
                        directory=True,
                        boundary_active=boundary_active,
                    )
                except Exception:
                    self._close(child)
                    raise
                self._close(current)
                current = child
            return current, root_identity
        except Exception:
            self._close(current)
            raise

    def _ensure_private_parent(self, drive: str, parts: Sequence[str]) -> None:
        for length in range(1, len(parts) + 1):
            handle = 0
            try:
                handle, _root_identity = self._open_private_directory_path(
                    drive,
                    parts[:length],
                )
            except _NativePathMissing:
                parent = 0
                created = 0
                try:
                    if length == 1:
                        raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
                    parent, root_identity = self._open_private_directory_path(
                        drive,
                        parts[: length - 1],
                        writable_final=True,
                    )
                    created = self._create_private_relative(
                        parent,
                        parts[length - 1],
                        directory=True,
                    )
                    identity = self._verify_directory(
                        created,
                        volume=root_identity.volume,
                    )
                    self._verify_private_handle(created)
                    self._remember_private(
                        drive,
                        parts[:length],
                        identity,
                        directory=True,
                    )
                finally:
                    self._close(created)
                    self._close(parent)
            finally:
                self._close(handle)

    def _read_handle(
        self,
        handle: int,
        snapshot: _FileSnapshot,
        *,
        maximum_bytes: int,
    ) -> bytes:
        if maximum_bytes < 0:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
        if snapshot.size > maximum_bytes:
            raise _failure(
                SecureFilesystemErrorCode.BYTE_LIMIT_EXCEEDED,
                actual=snapshot.size,
                maximum=maximum_bytes,
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            request = min(_READ_CHUNK_BYTES, maximum_bytes + 1 - total)
            buffer = ctypes.create_string_buffer(request)
            received = wintypes.DWORD()
            if not self._api.ReadFile(
                wintypes.HANDLE(handle),
                buffer,
                request,
                ctypes.byref(received),
                None,
            ):
                get_last_error = getattr(ctypes, "get_last_error")
                if get_last_error() == _ERROR_HANDLE_EOF:
                    break
                raise _failure(SecureFilesystemErrorCode.FILE_CHANGED)
            count = int(received.value)
            if count == 0:
                break
            chunks.append(buffer.raw[:count])
            total += count
            if total > maximum_bytes:
                raise _failure(
                    SecureFilesystemErrorCode.BYTE_LIMIT_EXCEEDED,
                    actual=total,
                    maximum=maximum_bytes,
                )
        after = self._file_snapshot(handle, volume=snapshot.identity.volume)
        if after != snapshot or total != snapshot.size:
            raise _failure(SecureFilesystemErrorCode.FILE_CHANGED)
        return b"".join(chunks)

    def _write_handle(self, handle: int, content: bytes) -> None:
        view = memoryview(content)
        while view:
            chunk = bytes(view[:_READ_CHUNK_BYTES])
            buffer = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            if not self._api.WriteFile(
                wintypes.HANDLE(handle),
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            ) or written.value <= 0:
                raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
            view = view[int(written.value) :]
        if not self._api.FlushFileBuffers(wintypes.HANDLE(handle)):
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)

    def pin_configured_root(self, root: Path) -> PinnedRootHandle:
        handle = 0
        try:
            drive, parts = _absolute_drive_parts(root, reject_archive=False)
            handle, _drive_identity = self._open_directory_path(drive, parts)
            identity = self._verify_directory(handle)
            pinned = _WindowsPinnedRoot(_handle=handle, _identity=identity)
            handle = 0
            return pinned
        except SecureFilesystemError:
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        except Exception:
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        finally:
            self._close(handle)

    def close_pinned_root(self, handle: PinnedRootHandle) -> None:
        pinned = self._pinned(handle)
        with pinned._lock:
            if pinned._closed:
                return
            pinned._closed = True
            self._close(pinned._handle)

    def read_file_beneath(
        self,
        handle: PinnedRootHandle,
        parts: Sequence[str],
        *,
        maximum_bytes: int,
    ) -> bytes:
        pinned = self._pinned(handle)
        opened: list[int] = []
        try:
            names = _validate_member_parts(parts)
            with pinned._lock:
                if pinned._closed:
                    raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
                if self._verify_directory(pinned._handle) != pinned._identity:
                    raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
                current = pinned._handle
                for name in names[:-1]:
                    current = self._open_relative(current, name, directory=True)
                    opened.append(current)
                    self._verify_directory(current, volume=pinned._identity.volume)
                member = self._open_relative(current, names[-1], directory=False)
                opened.append(member)
                snapshot = self._file_snapshot(member, volume=pinned._identity.volume)
                content = self._read_handle(member, snapshot, maximum_bytes=maximum_bytes)
                if self._verify_directory(pinned._handle) != pinned._identity:
                    raise _failure(SecureFilesystemErrorCode.FILE_CHANGED)
                return content
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None
        finally:
            for opened_handle in reversed(opened):
                self._close(opened_handle)

    def read_regular_file(self, path: Path, *, maximum_bytes: int) -> bytes:
        directory_handle = 0
        member_handle = 0
        try:
            # Absolute regular-member reads also serve immutable operation bundles.  Archive
            # refusal belongs to configured-input traversal so declared member semantics remain
            # identical to the POSIX provider.
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.NOT_REGULAR_FILE)
            directory_handle, root_identity = self._open_directory_path(drive, parts[:-1])
            directory_identity = self._verify_directory(
                directory_handle,
                volume=root_identity.volume,
            )
            member_handle = self._open_relative(directory_handle, parts[-1], directory=False)
            snapshot = self._file_snapshot(member_handle, volume=directory_identity.volume)
            return self._read_handle(
                member_handle,
                snapshot,
                maximum_bytes=maximum_bytes,
            )
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None
        finally:
            self._close(member_handle)
            self._close(directory_handle)

    @staticmethod
    def _pinned(handle: PinnedRootHandle) -> _WindowsPinnedRoot:
        if not isinstance(handle, _WindowsPinnedRoot):
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
        return handle

    def create_private_directory(
        self,
        path: Path,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        parent = 0
        created = 0
        existing = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            if parents:
                self._ensure_private_parent(drive, parts[:-1])
            parent, root_identity = self._open_private_directory_path(
                drive,
                parts[:-1],
                writable_final=True,
            )
            try:
                created = self._create_private_relative(
                    parent,
                    parts[-1],
                    directory=True,
                )
            except SecureFilesystemError as exc:
                if exc.code is not SecureFilesystemErrorCode.ALREADY_EXISTS or not exist_ok:
                    raise
                existing = self._open_private_relative(
                    parent,
                    parts[-1],
                    directory=True,
                )
                identity = self._verify_directory(existing, volume=root_identity.volume)
                self._verify_private_handle(existing)
                self._remember_private(drive, parts, identity, directory=True)
                return
            identity = self._verify_directory(created, volume=root_identity.volume)
            self._verify_private_handle(created)
            self._remember_private(drive, parts, identity, directory=True)
        except _NativePathMissing:
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        finally:
            self._close(existing)
            self._close(created)
            self._close(parent)

    def ensure_directory_path(self, path: Path) -> None:
        handle = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            self._ensure_private_parent(drive, parts)
            handle, _root_identity = self._open_private_directory_path(
                drive,
                parts,
                writable_final=True,
            )
            self._verify_directory(handle)
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        finally:
            self._close(handle)

    def verify_directory_path(self, path: Path) -> None:
        handle = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            handle, _root_identity = self._open_directory_path(drive, parts)
            self._verify_directory(handle)
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None
        finally:
            self._close(handle)

    def verify_private_directory(self, path: Path) -> None:
        handle = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            handle, _root_identity = self._open_private_directory_path(drive, parts)
            identity = self._verify_directory(handle)
            self._verify_private_handle(handle)
            self._remember_private(drive, parts, identity, directory=True)
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE) from None
        finally:
            self._close(handle)

    def create_private_file(self, path: Path, content: bytes) -> None:
        parent = 0
        created = 0
        try:
            if not isinstance(content, bytes):
                raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            parent, root_identity = self._open_private_directory_path(
                drive,
                parts[:-1],
                writable_final=True,
            )
            created = self._create_private_relative(
                parent,
                parts[-1],
                directory=False,
            )
            snapshot = self._file_snapshot(created, volume=root_identity.volume)
            self._verify_private_handle(created)
            if snapshot.size != 0:
                raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED)
            self._write_handle(created, content)
            after = self._file_snapshot(created, volume=root_identity.volume)
            self._verify_private_handle(created)
            if after.size != len(content):
                raise _failure(SecureFilesystemErrorCode.FILE_CHANGED)
            self._remember_private(drive, parts, after.identity, directory=False)
        except _NativePathMissing:
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.ACCESS_DENIED) from None
        finally:
            self._close(created)
            self._close(parent)

    def read_private_file(self, path: Path, *, maximum_bytes: int) -> bytes:
        parent = 0
        member = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            parent, root_identity = self._open_private_directory_path(drive, parts[:-1])
            member = self._open_private_relative(
                parent,
                parts[-1],
                directory=False,
            )
            snapshot = self._file_snapshot(member, volume=root_identity.volume)
            self._verify_private_handle(member)
            key = self._path_key(drive, parts)
            with self._private_lock:
                known = self._known_private_objects.get(key)
            if known is not None and known != (snapshot.identity, False):
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            self._remember_private(drive, parts, snapshot.identity, directory=False)
            return self._read_handle(member, snapshot, maximum_bytes=maximum_bytes)
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE) from None
        finally:
            self._close(member)
            self._close(parent)

    def _private_file_size(self, path: Path) -> int:
        parent = 0
        member = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            parent, root_identity = self._open_private_directory_path(drive, parts[:-1])
            member = self._open_private_relative(
                parent,
                parts[-1],
                directory=False,
            )
            snapshot = self._file_snapshot(member, volume=root_identity.volume)
            self._verify_private_handle(member)
            self._remember_private(drive, parts, snapshot.identity, directory=False)
            return snapshot.size
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE) from None
        finally:
            self._close(member)
            self._close(parent)

    def private_tree_size(self, path: Path) -> int:
        total = 0
        try:
            self.verify_private_directory(path)
            for root, directories, files in os.walk(path, followlinks=False):
                root_path = Path(root)
                self.verify_private_directory(root_path)
                for name in tuple(directories):
                    self.verify_private_directory(root_path / name)
                for name in files:
                    total += self._private_file_size(root_path / name)
            return total
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE) from None

    def _session_lock_location(
        self,
        path: Path,
    ) -> tuple[str, tuple[str, ...], str]:
        drive, parts = _absolute_drive_parts(path, reject_archive=False)
        if len(parts) < 3 or parts[-1] != ".lock":
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
        session_name = parts[-2]
        _validate_segment(session_name)
        return drive, parts[:-2], session_name

    def _ensure_external_lock_namespace(
        self,
        drive: str,
        state_parts: Sequence[str],
    ) -> tuple[str, ...]:
        if not state_parts:
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
        state_root = self._path_from_parts(drive, state_parts)
        self.verify_private_directory(state_root)
        namespace_parts = (*state_parts, _LOCK_NAMESPACE)
        self.create_private_directory(
            self._path_from_parts(drive, namespace_parts),
            exist_ok=True,
        )
        return namespace_parts

    def _acquire_external_session_lock(
        self,
        drive: str,
        state_parts: Sequence[str],
        session_name: str,
        *,
        blocking: bool,
    ) -> _WindowsSessionLock | None:
        namespace_parts = self._ensure_external_lock_namespace(drive, state_parts)
        lock_name = f"{session_name}.lck"
        _validate_segment(lock_name)
        lock_path = self._path_from_parts(drive, (*namespace_parts, lock_name))
        try:
            self.create_private_file(lock_path, b"0")
        except SecureFilesystemError as exc:
            if exc.code is not SecureFilesystemErrorCode.ALREADY_EXISTS:
                raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE) from None

        parent = 0
        lock_file = 0
        try:
            parent, root_identity = self._open_private_directory_path(
                drive,
                namespace_parts,
            )
            lock_file, _status = self._nt_open_relative(
                parent,
                lock_name,
                directory=False,
                desired_access=(
                    _GENERIC_READ
                    | _GENERIC_WRITE
                    | _FILE_READ_ATTRIBUTES
                    | _READ_CONTROL
                    | _SYNCHRONIZE
                ),
                disposition=_FILE_OPEN,
                share_access=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
            )
            if not lock_file:
                raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
            snapshot = self._file_snapshot(lock_file, volume=root_identity.volume)
            self._verify_private_handle(lock_file)
            if snapshot.size != 1:
                raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
            overlapped = _OVERLAPPED()
            flags = _LOCKFILE_EXCLUSIVE_LOCK | (
                0 if blocking else _LOCKFILE_FAIL_IMMEDIATELY
            )
            set_last_error = getattr(ctypes, "set_last_error")
            get_last_error = getattr(ctypes, "get_last_error")
            set_last_error(0)
            if not self._api.LockFileEx(
                wintypes.HANDLE(lock_file),
                flags,
                0,
                _LOCK_RANGE_LOW,
                0,
                ctypes.byref(overlapped),
            ):
                error = get_last_error()
                if not blocking and error in {
                    _ERROR_LOCK_VIOLATION,
                    _ERROR_IO_PENDING,
                }:
                    self._close(lock_file)
                    lock_file = 0
                    return None
                raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
            if self._read_handle(lock_file, snapshot, maximum_bytes=1) != b"0":
                self._api.UnlockFileEx(
                    wintypes.HANDLE(lock_file),
                    0,
                    _LOCK_RANGE_LOW,
                    0,
                    ctypes.byref(overlapped),
                )
                raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
            self._remember_private(
                drive,
                (*namespace_parts, lock_name),
                snapshot.identity,
                directory=False,
            )
            result = _WindowsSessionLock(
                _handle=lock_file,
                _overlapped=overlapped,
                _state_key=self._path_key(drive, state_parts),
                _session_name=session_name,
                _identity=snapshot.identity,
            )
            lock_file = 0
            return result
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE) from None
        finally:
            self._close(lock_file)
            self._close(parent)

    def _read_private_file_with_ticks(
        self,
        path: Path,
        *,
        maximum_bytes: int,
    ) -> tuple[bytes, int]:
        parent = 0
        member = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE)
            parent, root_identity = self._open_private_directory_path(drive, parts[:-1])
            member = self._open_private_relative(
                parent,
                parts[-1],
                directory=False,
            )
            snapshot = self._file_snapshot(member, volume=root_identity.volume)
            self._verify_private_handle(member)
            before_ticks = self._last_write_ticks(member)
            content = self._read_handle(member, snapshot, maximum_bytes=maximum_bytes)
            if self._last_write_ticks(member) != before_ticks:
                raise _failure(SecureFilesystemErrorCode.FILE_CHANGED)
            self._remember_private(drive, parts, snapshot.identity, directory=False)
            return content, before_ticks
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.NOT_PRIVATE) from None
        finally:
            self._close(member)
            self._close(parent)

    def _relative_entry_exists(self, parent: int, name: str) -> bool:
        desired_access = _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        for directory in (True, False):
            handle = 0
            try:
                handle, _status = self._nt_open_relative(
                    parent,
                    name,
                    directory=directory,
                    desired_access=desired_access,
                    disposition=_FILE_OPEN,
                )
                if handle:
                    return True
            except SecureFilesystemError:
                continue
            finally:
                self._close(handle)
        return False

    def _set_rename_information(
        self,
        source: int,
        destination_parent: int,
        destination_name: str,
    ) -> int:
        encoded = destination_name.encode("utf-16-le")
        offset = _FILE_RENAME_INFO.FileName.offset
        size = ctypes.sizeof(_FILE_RENAME_INFO) + len(encoded)
        buffer = ctypes.create_string_buffer(size)
        info = ctypes.cast(buffer, ctypes.POINTER(_FILE_RENAME_INFO)).contents
        info.ReplaceIfExists = 0
        info.RootDirectory = wintypes.HANDLE(destination_parent)
        info.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        io_status = _IO_STATUS_BLOCK()
        return int(
            self._api.NtSetInformationFile(
                wintypes.HANDLE(source),
                ctypes.byref(io_status),
                buffer,
                size,
                _FILE_RENAME_INFORMATION_CLASS,
            )
        )

    def _rename_directory_no_replace(self, source: Path, destination: Path) -> None:
        source_parent = 0
        source_handle = 0
        destination_parent = 0
        destination_handle = 0
        try:
            source_drive, source_parts = _absolute_drive_parts(
                source,
                reject_archive=False,
            )
            destination_drive, destination_parts = _absolute_drive_parts(
                destination,
                reject_archive=False,
            )
            if not source_parts or not destination_parts:
                raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED)
            source_key = self._path_key(source_drive, source_parts)
            destination_key = self._path_key(destination_drive, destination_parts)
            if source_key == destination_key:
                raise _failure(SecureFilesystemErrorCode.ALREADY_EXISTS)
            self.private_tree_size(source)
            self._verify_private_tree_deletable(source)
            source_parent, source_root = self._open_private_directory_path(
                source_drive,
                source_parts[:-1],
            )
            source_handle = self._open_private_relative(
                source_parent,
                source_parts[-1],
                directory=True,
                delete=True,
            )
            source_identity = self._verify_directory(
                source_handle,
                volume=source_root.volume,
            )
            self._verify_private_handle(source_handle)
            self._remember_private(
                source_drive,
                source_parts,
                source_identity,
                directory=True,
            )
            destination_parent, destination_root = self._open_private_directory_path(
                destination_drive,
                destination_parts[:-1],
                writable_final=True,
            )
            if source_identity.volume != destination_root.volume:
                raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED)
            with self._private_lock:
                if any(
                    key[: len(destination_key)] == destination_key
                    for key in self._known_private_objects
                ) or self._relative_entry_exists(
                    destination_parent,
                    destination_parts[-1],
                ):
                    raise _failure(SecureFilesystemErrorCode.ALREADY_EXISTS)
                status = self._set_rename_information(
                    source_handle,
                    destination_parent,
                    destination_parts[-1],
                )
                if status < 0:
                    if (
                        ctypes.c_uint32(status).value == _STATUS_OBJECT_NAME_COLLISION
                        or self._relative_entry_exists(
                            destination_parent,
                            destination_parts[-1],
                        )
                    ):
                        raise _failure(SecureFilesystemErrorCode.ALREADY_EXISTS)
                    raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED)
                destination_handle = self._open_private_relative(
                    destination_parent,
                    destination_parts[-1],
                    directory=True,
                )
                destination_identity = self._verify_directory(
                    destination_handle,
                    volume=source_identity.volume,
                )
                self._verify_private_handle(destination_handle)
                if destination_identity != source_identity:
                    raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED)
                self._move_known_private_subtree(
                    source_drive,
                    source_parts,
                    destination_drive,
                    destination_parts,
                )
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED) from None
        finally:
            self._close(destination_handle)
            self._close(destination_parent)
            self._close(source_handle)
            self._close(source_parent)

    def _mark_delete(self, handle: int) -> None:
        disposition = _FILE_DISPOSITION_INFO(DeleteFile=1)
        if not self._api.SetFileInformationByHandle(
            wintypes.HANDLE(handle),
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)

    def _open_delete_child(
        self,
        parent: int,
        name: str,
    ) -> tuple[int, bool]:
        directory_access = (
            _DELETE
            | _FILE_LIST_DIRECTORY
            | _FILE_TRAVERSE
            | _FILE_READ_ATTRIBUTES
            | _READ_CONTROL
            | _SYNCHRONIZE
        )
        handle, _status = self._nt_open_relative(
            parent,
            name,
            directory=True,
            desired_access=directory_access,
            disposition=_FILE_OPEN,
        )
        if handle:
            return handle, True
        file_access = _DELETE | _FILE_READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE
        handle, _status = self._nt_open_relative(
            parent,
            name,
            directory=False,
            desired_access=file_access,
            disposition=_FILE_OPEN,
        )
        if not handle:
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
        return handle, False

    def _verify_deletable_directory_handle(
        self,
        drive: str,
        parts: Sequence[str],
        handle: int,
        *,
        volume: int,
    ) -> None:
        identity = self._verify_directory(handle, volume=volume)
        self._verify_private_handle(handle)
        self._remember_private(drive, parts, identity, directory=True)
        path = self._path_from_parts(drive, parts)
        try:
            with os.scandir(path) as scanned:
                names = sorted(
                    (entry.name for entry in scanned),
                    key=lambda name: name.encode("utf-8"),
                )
        except (OSError, UnicodeError):
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        for name in names:
            child = 0
            try:
                _validate_segment(name)
                child_parts = (*parts, name)
                child, directory = self._open_delete_child(handle, name)
                if directory:
                    self._verify_deletable_directory_handle(
                        drive,
                        child_parts,
                        child,
                        volume=volume,
                    )
                else:
                    snapshot = self._file_snapshot(child, volume=volume)
                    self._verify_private_handle(child)
                    self._remember_private(
                        drive,
                        child_parts,
                        snapshot.identity,
                        directory=False,
                    )
            except SecureFilesystemError:
                raise
            except Exception:
                raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
            finally:
                self._close(child)

    def _verify_private_tree_deletable(self, path: Path) -> None:
        parent = 0
        directory = 0
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
            parent, root_identity = self._open_private_directory_path(
                drive,
                parts[:-1],
            )
            directory = self._open_private_relative(
                parent,
                parts[-1],
                directory=True,
                delete=True,
            )
            self._verify_deletable_directory_handle(
                drive,
                parts,
                directory,
                volume=root_identity.volume,
            )
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        finally:
            self._close(directory)
            self._close(parent)

    def _delete_directory_handle(
        self,
        drive: str,
        parts: Sequence[str],
        handle: int,
        *,
        volume: int,
    ) -> None:
        identity = self._verify_directory(handle, volume=volume)
        self._verify_private_handle(handle)
        self._remember_private(drive, parts, identity, directory=True)
        path = self._path_from_parts(drive, parts)
        try:
            with os.scandir(path) as scanned:
                entries = sorted(
                    scanned,
                    key=lambda entry: entry.name.encode("utf-8"),
                )
        except (OSError, UnicodeError):
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        for entry in entries:
            child = 0
            try:
                _validate_segment(entry.name)
                child_parts = (*parts, entry.name)
                child, directory = self._open_delete_child(handle, entry.name)
                if directory:
                    self._delete_directory_handle(
                        drive,
                        child_parts,
                        child,
                        volume=volume,
                    )
                else:
                    snapshot = self._file_snapshot(child, volume=volume)
                    self._verify_private_handle(child)
                    self._remember_private(
                        drive,
                        child_parts,
                        snapshot.identity,
                        directory=False,
                    )
                    self._mark_delete(child)
            except SecureFilesystemError:
                raise
            except Exception:
                raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
            finally:
                self._close(child)
        try:
            with os.scandir(path) as remaining:
                if next(remaining, None) is not None:
                    raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
        except SecureFilesystemError:
            raise
        except OSError:
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        self._mark_delete(handle)

    def _delete_private_tombstone(self, path: Path) -> None:
        parent = 0
        tombstone = 0
        drive = ""
        parts: tuple[str, ...] = ()
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts or parts[-1] == _LOCK_NAMESPACE:
                raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
            parent, root_identity = self._open_private_directory_path(
                drive,
                parts[:-1],
            )
            tombstone = self._open_private_relative(
                parent,
                parts[-1],
                directory=True,
                delete=True,
            )
            self._delete_directory_handle(
                drive,
                parts,
                tombstone,
                volume=root_identity.volume,
            )
        except SecureFilesystemError:
            raise
        except Exception:
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        finally:
            self._close(tombstone)
            self._close(parent)
        if os.path.lexists(path):
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
        self._forget_known_private_subtree(drive, parts)

    def _new_tombstone(
        self,
        path: Path,
        *,
        cleanup_session_name: str | None,
    ) -> Path:
        prefix = (
            f"{_CLEANUP_TOMBSTONE_PREFIX}{cleanup_session_name}-"
            if cleanup_session_name is not None
            else _DELETE_TOMBSTONE_PREFIX
        )
        for _attempt in range(100):
            tombstone = path.parent / f"{prefix}{secrets.token_hex(16)}"
            try:
                self._rename_directory_no_replace(path, tombstone)
                return tombstone
            except SecureFilesystemError as exc:
                if exc.code is not SecureFilesystemErrorCode.ALREADY_EXISTS:
                    raise
        raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)

    @staticmethod
    def _cleanup_tombstone_session(name: str, *, session_prefix: str) -> str | None:
        if not name.startswith(_CLEANUP_TOMBSTONE_PREFIX):
            return None
        value = name[len(_CLEANUP_TOMBSTONE_PREFIX) :]
        session_name, separator, token = value.rpartition("-")
        if (
            separator != "-"
            or not session_name.startswith(session_prefix)
            or len(token) != 32
            or any(character not in "0123456789abcdef" for character in token)
        ):
            return None
        return session_name

    def _valid_cleanup_candidate(
        self,
        path: Path,
        *,
        marker_name: str,
        lock_name: str,
        minimum_age_seconds: int,
        now: float,
        marker_validator: Callable[[bytes], bool],
    ) -> bool:
        try:
            self.verify_private_directory(path)
            marker_content, marker_ticks = self._read_private_file_with_ticks(
                path / marker_name,
                maximum_bytes=1024,
            )
            modified = marker_ticks / 10_000_000 - _WINDOWS_EPOCH_SECONDS
            if now - modified <= minimum_age_seconds:
                return False
            if self.read_private_file(path / lock_name, maximum_bytes=1) != b"0":
                return False
            try:
                if not marker_validator(marker_content):
                    return False
            except Exception:
                return False
            self.private_tree_size(path)
            self._verify_private_tree_deletable(path)
            return True
        except (OSError, SecureFilesystemError, ValueError):
            return False

    def acquire_session_lock(
        self,
        path: Path,
        *,
        blocking: bool,
    ) -> SessionLockHandle | None:
        lock: _WindowsSessionLock | None = None
        try:
            drive, state_parts, session_name = self._session_lock_location(path)
            if self.read_private_file(path, maximum_bytes=1) != b"0":
                raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
            lock = self._acquire_external_session_lock(
                drive,
                state_parts,
                session_name,
                blocking=blocking,
            )
            if lock is None:
                return None
            if self.read_private_file(path, maximum_bytes=1) != b"0":
                self.release_session_lock(lock)
                lock = None
                raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
            return lock
        except SecureFilesystemError as exc:
            if lock is not None:
                self.release_session_lock(lock)
                lock = None
            if exc.code is SecureFilesystemErrorCode.LOCK_UNAVAILABLE:
                raise
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE) from None
        except Exception:
            if lock is not None:
                self.release_session_lock(lock)
                lock = None
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE) from None

    def release_session_lock(self, handle: SessionLockHandle) -> None:
        lock = self._session_lock(handle)
        if lock._released:
            return
        lock._released = True
        try:
            self._api.UnlockFileEx(
                wintypes.HANDLE(lock._handle),
                0,
                _LOCK_RANGE_LOW,
                0,
                ctypes.byref(lock._overlapped),
            )
        finally:
            self._close(lock._handle)

    def publish_directory_no_replace(self, source: Path, destination: Path) -> None:
        try:
            self._rename_directory_no_replace(source, destination)
        except SecureFilesystemError as exc:
            if exc.code is SecureFilesystemErrorCode.ALREADY_EXISTS:
                raise
            raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED) from None
        except Exception:
            raise _failure(SecureFilesystemErrorCode.PUBLICATION_FAILED) from None

    def remove_private_tree(self, path: Path) -> None:
        lock: _WindowsSessionLock | None = None
        try:
            cleanup_name: str | None = None
            if path.name.startswith("session-"):
                drive, parts = _absolute_drive_parts(path, reject_archive=False)
                if len(parts) < 2:
                    raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
                cleanup_name = parts[-1]
                lock = self._acquire_external_session_lock(
                    drive,
                    parts[:-1],
                    cleanup_name,
                    blocking=False,
                )
                if lock is None:
                    raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
            tombstone = self._new_tombstone(
                path,
                cleanup_session_name=cleanup_name,
            )
            self._delete_private_tombstone(tombstone)
        except SecureFilesystemError as exc:
            if exc.code is SecureFilesystemErrorCode.REMOVE_FAILED:
                raise
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        except Exception:
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        finally:
            if lock is not None:
                self.release_session_lock(lock)

    def remove_locked_session_tree(
        self,
        path: Path,
        handle: SessionLockHandle,
    ) -> None:
        lock = self._session_lock(handle)
        try:
            drive, parts = _absolute_drive_parts(path, reject_archive=False)
            if not parts:
                raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
            if (
                lock._state_key != self._path_key(drive, parts[:-1])
                or lock._session_name.casefold() != parts[-1].casefold()
            ):
                raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED)
            tombstone = self._new_tombstone(
                path,
                cleanup_session_name=lock._session_name,
            )
            self._delete_private_tombstone(tombstone)
        except SecureFilesystemError as exc:
            if exc.code is SecureFilesystemErrorCode.REMOVE_FAILED:
                raise
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        except Exception:
            raise _failure(SecureFilesystemErrorCode.REMOVE_FAILED) from None
        finally:
            self.release_session_lock(lock)

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
            drive, root_parts = _absolute_drive_parts(root, reject_archive=False)
            if not root_parts:
                raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH)
            self.verify_private_directory(root)
            self._ensure_external_lock_namespace(drive, root_parts)
            candidates = sorted(root.iterdir(), key=lambda item: item.name.encode("utf-8"))
        except SecureFilesystemError:
            raise
        except (OSError, UnicodeError, ValueError):
            raise _failure(SecureFilesystemErrorCode.UNSAFE_PATH) from None

        removed: list[str] = []
        for candidate in candidates:
            if candidate.name.startswith(session_prefix):
                session_name = candidate.name
                claimed = False
            else:
                recovered = self._cleanup_tombstone_session(
                    candidate.name,
                    session_prefix=session_prefix,
                )
                if recovered is None:
                    continue
                session_name = recovered
                claimed = True
            lock: _WindowsSessionLock | None = None
            try:
                lock = self._acquire_external_session_lock(
                    drive,
                    root_parts,
                    session_name,
                    blocking=False,
                )
                if lock is None:
                    continue
                if not self._valid_cleanup_candidate(
                    candidate,
                    marker_name=marker_name,
                    lock_name=lock_name,
                    minimum_age_seconds=minimum_age_seconds,
                    now=now,
                    marker_validator=marker_validator,
                ):
                    continue
                tombstone = (
                    candidate
                    if claimed
                    else self._new_tombstone(
                        candidate,
                        cleanup_session_name=session_name,
                    )
                )
                self._delete_private_tombstone(tombstone)
                self._forget_known_private_subtree(
                    drive,
                    (*root_parts, session_name),
                )
                if session_name not in removed:
                    removed.append(session_name)
            except (OSError, SecureFilesystemError, ValueError):
                continue
            finally:
                if lock is not None:
                    self.release_session_lock(lock)
        return tuple(removed)

    @staticmethod
    def _session_lock(handle: SessionLockHandle) -> _WindowsSessionLock:
        if not isinstance(handle, _WindowsSessionLock):
            raise _failure(SecureFilesystemErrorCode.LOCK_UNAVAILABLE)
        return handle


__all__ = ["WindowsSecureFilesystem"]
