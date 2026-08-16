"""Native adversarial tests for Windows configured-root and member reads."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import defined_quant._windows_local_host as windows_local
import pytest
from defined_quant._windows_local_host import WindowsSecureFilesystem
from defined_quant.data_records import cas_json_bytes
from defined_quant.dataset_registry import ConfiguredFileRoots, DatasetRegistryError
from defined_quant.local_host_platform import (
    SecureFilesystemError,
    SecureFilesystemErrorCode,
    local_host_platform,
)
from defined_quant.service import DefinedQuantService
from defined_quant.session_cas import (
    ORPHAN_SESSION_AGE_SECONDS,
    SessionCas,
    cleanup_orphan_sessions,
)

if sys.platform != "win32":
    __test__ = False

_SYMBOLIC_LINK_FLAG_DIRECTORY = 0x1
_SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE = 0x2
_HANDLE_FLAG_INHERIT = 0x1
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_MARKER_CONTENT = cas_json_bytes(
    {"application": "defined-quant-local-mcp", "schema_version": 1}
)


@pytest.fixture
def secure() -> WindowsSecureFilesystem:
    provider = local_host_platform().secure_filesystem
    assert isinstance(provider, WindowsSecureFilesystem)
    return provider


def _assert_failure(
    expected: SecureFilesystemErrorCode,
    operation: Any,
) -> SecureFilesystemError:
    with pytest.raises(SecureFilesystemError) as caught:
        operation()
    assert caught.value.code is expected
    assert caught.value.args == (expected.value,)
    assert caught.value.__cause__ is None
    assert "WinError" not in str(caught.value)
    return caught.value


def _create_symlink(link: Path, target: Path, *, directory: bool) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateSymbolicLinkW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    create.restype = ctypes.c_ubyte
    flags = _SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE
    if directory:
        flags |= _SYMBOLIC_LINK_FLAG_DIRECTORY
    assert create(str(link), str(target), flags), (
        "native Windows symlink creation is required for release validation; "
        f"error={ctypes.get_last_error()}"
    )


def _create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def _extended(path: Path) -> str:
    value = str(path)
    assert not value.startswith("\\\\")
    return f"\\\\?\\{value}"


def _set_dacl(path: Path, sddl: str, *, protected: bool) -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert.restype = ctypes.c_int
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int),
    ]
    get_dacl.restype = ctypes.c_int
    set_security = advapi32.SetNamedSecurityInfoW
    set_security.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    set_security.restype = ctypes.c_uint32
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    assert convert(sddl, 1, ctypes.byref(descriptor), None), ctypes.get_last_error()
    present = ctypes.c_int()
    defaulted = ctypes.c_int()
    dacl = ctypes.c_void_p()
    assert get_dacl(
        descriptor,
        ctypes.byref(present),
        ctypes.byref(dacl),
        ctypes.byref(defaulted),
    ), ctypes.get_last_error()
    assert present.value and dacl.value
    information = _DACL_SECURITY_INFORMATION | (
        _PROTECTED_DACL_SECURITY_INFORMATION
        if protected
        else _UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    try:
        assert set_security(str(path), 1, information, None, None, dacl, None) == 0
    finally:
        local_free(descriptor)


def _string_sid(value: str) -> ctypes.c_void_p:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi32.ConvertStringSidToSidW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    convert.restype = ctypes.c_int
    sid = ctypes.c_void_p()
    assert convert(value, ctypes.byref(sid)), ctypes.get_last_error()
    return sid


def _private_session(
    secure: WindowsSecureFilesystem,
    state: Path,
    name: str,
    *,
    marker_mtime: float,
) -> Path:
    secure.create_private_directory(state, exist_ok=True)
    session = state / name
    secure.create_private_directory(session)
    for child in ("datasets", "operations", "staging", "quarantine"):
        secure.create_private_directory(session / child)
    marker = session / ".defined-quant-session-v1"
    secure.create_private_file(marker, _MARKER_CONTENT)
    secure.create_private_file(session / ".lock", b"0")
    os.utime(marker, (marker_mtime, marker_mtime))
    return session


def _open_without_delete_sharing(path: Path) -> tuple[Any, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = create_file(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    assert handle not in {None, ctypes.c_void_p(-1).value}, ctypes.get_last_error()
    return close_handle, int(handle)


def test_windows_provider_selects_complete_secure_filesystem_capabilities(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    assert local_host_platform().provider_id == "win32"
    state = tmp_path / "state"
    secure.create_private_directory(state)
    private = state / "session-capabilities"
    secure.create_private_directory(private)
    secure.verify_private_directory(private)
    lock_path = private / ".lock"
    secure.create_private_file(lock_path, b"0")
    lock = secure.acquire_session_lock(lock_path, blocking=False)
    assert lock is not None
    secure.release_session_lock(lock)


def test_pinned_root_survives_root_name_replacement_and_uses_handle_identity(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    root = tmp_path / "MiXeD-Configured-Root"
    moved = tmp_path / "moved-root"
    root.mkdir()
    (root / "Data.JSON").write_bytes(b"original")

    case_alias = Path(str(root).swapcase())
    first = secure.pin_configured_root(root)
    alias = secure.pin_configured_root(case_alias)
    try:
        assert getattr(first, "_identity") == getattr(alias, "_identity")
        assert secure.read_file_beneath(alias, ("dATA.json",), maximum_bytes=8) == b"original"

        root.rename(moved)
        root.mkdir()
        (root / "Data.JSON").write_bytes(b"replacement")
        assert secure.read_file_beneath(first, ("DATA.JSON",), maximum_bytes=8) == b"original"
    finally:
        secure.close_pinned_root(alias)
        secure.close_pinned_root(first)


def test_windows_root_and_every_member_position_refuse_reparse_points(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.json").write_bytes(b"outside")
    real_root = tmp_path / "real-root"
    real_root.mkdir()

    root_link = tmp_path / "root-link"
    _create_symlink(root_link, real_root, directory=True)
    _assert_failure(
        SecureFilesystemErrorCode.ACCESS_DENIED,
        lambda: secure.pin_configured_root(root_link),
    )
    (real_root / "child-root").mkdir()
    _assert_failure(
        SecureFilesystemErrorCode.ACCESS_DENIED,
        lambda: secure.pin_configured_root(root_link / "child-root"),
    )

    handle = secure.pin_configured_root(real_root)
    try:
        intermediate = real_root / "intermediate"
        _create_symlink(intermediate, outside, directory=True)
        _assert_failure(
            SecureFilesystemErrorCode.UNSAFE_PATH,
            lambda: secure.read_file_beneath(
                handle,
                ("intermediate", "payload.json"),
                maximum_bytes=32,
            ),
        )
        _assert_failure(
            SecureFilesystemErrorCode.UNSAFE_PATH,
            lambda: secure.read_regular_file(
                intermediate / "payload.json",
                maximum_bytes=32,
            ),
        )

        final = real_root / "final.json"
        _create_symlink(final, outside / "payload.json", directory=False)
        _assert_failure(
            SecureFilesystemErrorCode.UNSAFE_PATH,
            lambda: secure.read_file_beneath(handle, ("final.json",), maximum_bytes=32),
        )
        _assert_failure(
            SecureFilesystemErrorCode.UNSAFE_PATH,
            lambda: secure.read_regular_file(final, maximum_bytes=32),
        )
    finally:
        secure.close_pinned_root(handle)


def test_windows_junctions_fail_at_root_intermediate_and_final_positions(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    target = tmp_path / "junction-target"
    target.mkdir()
    (target / "payload.json").write_bytes(b"outside")
    root = tmp_path / "root"
    root.mkdir()

    root_junction = tmp_path / "root-junction"
    _create_junction(root_junction, target)
    _assert_failure(
        SecureFilesystemErrorCode.ACCESS_DENIED,
        lambda: secure.pin_configured_root(root_junction),
    )
    (target / "child-root").mkdir()
    _assert_failure(
        SecureFilesystemErrorCode.ACCESS_DENIED,
        lambda: secure.pin_configured_root(root_junction / "child-root"),
    )

    handle = secure.pin_configured_root(root)
    try:
        intermediate = root / "junction"
        _create_junction(intermediate, target)
        _assert_failure(
            SecureFilesystemErrorCode.UNSAFE_PATH,
            lambda: secure.read_file_beneath(
                handle,
                ("junction", "payload.json"),
                maximum_bytes=32,
            ),
        )
        _assert_failure(
            SecureFilesystemErrorCode.NOT_REGULAR_FILE,
            lambda: secure.read_file_beneath(handle, ("junction",), maximum_bytes=32),
        )
    finally:
        secure.close_pinned_root(handle)


@pytest.mark.parametrize("swap_position", ["intermediate", "final"])
def test_windows_relative_handle_walk_resists_swap_races(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap_position: str,
) -> None:
    root = tmp_path / f"race-{swap_position}"
    nested = root / "nested"
    nested.mkdir(parents=True)
    member = nested / "payload.json"
    member.write_bytes(b"original")
    handle = secure.pin_configured_root(root)
    opened = threading.Event()
    proceed = threading.Event()
    original_open = secure._open_relative
    target_name = "nested" if swap_position == "intermediate" else "payload.json"

    def paused_open(parent: int, name: str, *, directory: bool) -> int:
        opened_handle = original_open(parent, name, directory=directory)
        if name == target_name:
            opened.set()
            assert proceed.wait(timeout=10)
        return opened_handle

    monkeypatch.setattr(secure, "_open_relative", paused_open)
    result: list[bytes] = []
    failures: list[BaseException] = []

    def read() -> None:
        try:
            result.append(
                secure.read_file_beneath(
                    handle,
                    ("nested", "payload.json"),
                    maximum_bytes=32,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted on the native lane
            failures.append(exc)

    reader = threading.Thread(target=read)
    reader.start()
    assert opened.wait(timeout=10)
    if swap_position == "intermediate":
        nested.rename(root / "opened-nested")
        nested.mkdir()
        (nested / "payload.json").write_bytes(b"replacement")
    else:
        member.rename(nested / "opened-payload.json")
        member.write_bytes(b"replacement")
    proceed.set()
    reader.join(timeout=10)
    secure.close_pinned_root(handle)

    assert not reader.is_alive()
    assert failures == []
    assert result == [b"original"]


def test_windows_unicode_and_long_paths_are_read_as_exact_bytes(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    root = tmp_path / "dane-東京-Żółć"
    root.mkdir()
    current = root
    relative_parts: list[str] = []
    for index in range(6):
        segment = f"część-{index}-" + "x" * 48
        relative_parts.append(segment)
        current = current / segment
        os.mkdir(_extended(current))
    member = current / "wynik-東京.json"
    relative_parts.append(member.name)
    payload = "zażółć-東京\r\n".encode()
    descriptor = os.open(_extended(member), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        assert os.write(descriptor, payload) == len(payload)
    finally:
        os.close(descriptor)
    assert len(str(member)) > 260

    handle = secure.pin_configured_root(root)
    try:
        assert secure.read_file_beneath(
            handle,
            tuple(relative_parts),
            maximum_bytes=len(payload),
        ) == payload
    finally:
        secure.close_pinned_root(handle)

    roots = ConfiguredFileRoots((root,))
    try:
        assert roots.read(str(member)) == payload
    finally:
        roots.close()


@pytest.mark.parametrize(
    "path_value",
    [
        r"\\server\share\input.json",
        r"\\?\C:\input.json",
        r"\\.\NUL",
        r"\\.\pipe\defined-quant-test",
        r"\Device\HarddiskVolume1\input.json",
        r"C:\CON.txt",
        r"C:\nul.json",
        r"C:\input.json:stream",
        "C:\\input.json.",
        "C:\\input.json ",
    ],
)
def test_windows_namespaces_devices_ads_and_nonportable_aliases_fail_before_open(
    secure: WindowsSecureFilesystem,
    path_value: str,
) -> None:
    _assert_failure(
        SecureFilesystemErrorCode.UNSAFE_PATH,
        lambda: secure.read_regular_file(Path(path_value), maximum_bytes=32),
    )


def test_windows_mapped_remote_drives_fail_closed(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secure._api, "GetDriveTypeW", lambda _root: 4)
    _assert_failure(
        SecureFilesystemErrorCode.ACCESS_DENIED,
        lambda: secure.pin_configured_root(tmp_path),
    )
    _assert_failure(
        SecureFilesystemErrorCode.UNSAFE_PATH,
        lambda: secure.read_regular_file(tmp_path / "input.json", maximum_bytes=32),
    )


def test_windows_opened_remote_handle_is_refused_even_after_local_drive_precheck(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def report_remote(
        _handle: Any,
        _io_status: Any,
        information: Any,
        _length: Any,
        _information_class: Any,
    ) -> int:
        ctypes.cast(information, ctypes.POINTER(ctypes.c_ubyte))[0] = 1
        return 0

    monkeypatch.setattr(secure._api, "NtQueryInformationFile", report_remote)
    _assert_failure(
        SecureFilesystemErrorCode.ACCESS_DENIED,
        lambda: secure.pin_configured_root(tmp_path),
    )


def test_windows_archive_and_nonregular_inputs_are_refused(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive-root"
    root.mkdir()
    archive = tmp_path / "payload.ZIP"
    archive.write_bytes(b"not-an-archive")
    member_archive = root / archive.name
    member_archive.write_bytes(archive.read_bytes())
    handle = secure.pin_configured_root(root)
    try:
        _assert_failure(
            SecureFilesystemErrorCode.UNSAFE_PATH,
            lambda: secure.read_file_beneath(handle, (archive.name,), maximum_bytes=32),
        )
    finally:
        secure.close_pinned_root(handle)
    roots = ConfiguredFileRoots((root,))
    try:
        with pytest.raises(DatasetRegistryError) as unsupported:
            roots.read(str(member_archive))
        assert unsupported.value.code == "unsupported_data_format"
    finally:
        roots.close()
    _assert_failure(
        SecureFilesystemErrorCode.NOT_REGULAR_FILE,
        lambda: secure.read_regular_file(tmp_path, maximum_bytes=32),
    )


def test_windows_final_volume_mismatch_fails_containment_check(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "volume-root"
    root.mkdir()
    (root / "payload.json").write_bytes(b"payload")
    handle = secure.pin_configured_root(root)
    real_identity = secure._identity

    def wrong_volume(opened_handle: int) -> Any:
        identity = real_identity(opened_handle)
        attributes = secure._attributes(opened_handle)
        if not attributes & 0x10:
            return type(identity)(volume=identity.volume + 1, file_id=identity.file_id)
        return identity

    monkeypatch.setattr(secure, "_identity", wrong_volume)
    try:
        _assert_failure(
            SecureFilesystemErrorCode.UNSAFE_PATH,
            lambda: secure.read_file_beneath(handle, ("payload.json",), maximum_bytes=32),
        )
    finally:
        secure.close_pinned_root(handle)


def test_windows_pinned_handles_are_not_inheritable(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    root = tmp_path / "handle-root"
    root.mkdir()
    handle = secure.pin_configured_root(root)
    try:
        native_handle = getattr(handle, "_handle")
        flags = ctypes.c_uint32()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_handle_information = kernel32.GetHandleInformation
        get_handle_information.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_handle_information.restype = ctypes.c_int
        assert get_handle_information(native_handle, ctypes.byref(flags))
        assert flags.value & _HANDLE_FLAG_INHERIT == 0
    finally:
        secure.close_pinned_root(handle)


def test_windows_dataset_registry_maps_provider_refusals_to_closed_wire_failures(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset-root"
    outside = tmp_path / "dataset-outside"
    root.mkdir()
    outside.mkdir()
    (outside / "payload.json").write_bytes(b"[]")
    link = root / "payload.json"
    _create_symlink(link, outside / "payload.json", directory=False)

    roots = ConfiguredFileRoots((root,))
    try:
        with pytest.raises(DatasetRegistryError) as unsafe:
            roots.read(str(link))
        assert unsafe.value.code == "unsafe_input_path"
        assert unsafe.value.details == {}
    finally:
        roots.close()


def test_windows_private_objects_override_a_permissive_inheritable_parent(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "permissive-parent"
    parent.mkdir()
    _set_dacl(parent, "D:(A;OICI;FA;;;WD)", protected=False)

    state = parent / "state"
    secure.create_private_directory(state)
    secure.verify_private_directory(state)
    member = state / "record.json"
    secure.create_private_file(member, b"record\n")
    assert secure.read_private_file(member, maximum_bytes=7) == b"record\n"


@pytest.mark.parametrize("extra_sid", ["WD", "BU"])
def test_windows_private_verification_rejects_everyone_and_users_aces(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    extra_sid: str,
) -> None:
    state = tmp_path / f"extra-ace-{extra_sid}"
    secure.create_private_directory(state)
    owner = secure._owner_sid_text
    _set_dacl(
        state,
        f"D:P(A;;FA;;;{owner})(A;;FR;;;{extra_sid})",
        protected=True,
    )

    _assert_failure(
        SecureFilesystemErrorCode.NOT_PRIVATE,
        lambda: secure.verify_private_directory(state),
    )


def test_windows_private_verification_rejects_inherited_aces(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "inheritable-parent"
    parent.mkdir()
    owner = secure._owner_sid_text
    _set_dacl(
        parent,
        f"D:(A;OICI;FA;;;{owner})(A;OICI;FR;;;WD)",
        protected=False,
    )
    inherited = parent / "inherited-child"
    inherited.mkdir()

    _assert_failure(
        SecureFilesystemErrorCode.NOT_PRIVATE,
        lambda: secure.verify_private_directory(inherited),
    )


def test_windows_private_verification_rejects_a_wrong_expected_owner_sid(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    state = tmp_path / "wrong-owner"
    secure.create_private_directory(state)
    everyone = _string_sid("S-1-1-0")
    original_owner = secure._owner_sid
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    try:
        secure._owner_sid = everyone
        _assert_failure(
            SecureFilesystemErrorCode.NOT_PRIVATE,
            lambda: secure.verify_private_directory(state),
        )
    finally:
        secure._owner_sid = original_owner
        local_free(everyone)


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_windows_private_identity_rejects_exact_acl_object_replacement(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    kind: str,
) -> None:
    state = tmp_path / f"replacement-{kind}"
    secure.create_private_directory(state)
    target = state / "target"
    if kind == "directory":
        secure.create_private_directory(target)
        target.rename(state / "original")
        target.mkdir()
    else:
        secure.create_private_file(target, b"original")
        target.rename(state / "original")
        target.write_bytes(b"replacement")
    _set_dacl(
        target,
        f"D:P(A;;FA;;;{secure._owner_sid_text})",
        protected=True,
    )

    if kind == "directory":
        def operation() -> None:
            secure.verify_private_directory(target)
    else:
        def operation() -> None:
            secure.read_private_file(target, maximum_bytes=32)
    _assert_failure(SecureFilesystemErrorCode.NOT_PRIVATE, operation)


def test_windows_private_descriptor_is_supplied_at_native_creation_and_checked_before_write(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_creates: list[tuple[str, int]] = []
    verified_handles: set[int] = set()
    native_open = secure._nt_open_relative
    verify_handle = secure._verify_private_handle
    write_handle = secure._write_handle

    def audited_native_open(
        parent: int,
        name: str,
        *,
        directory: bool,
        desired_access: int,
        disposition: int,
        security_descriptor: int | None = None,
    ) -> tuple[int, int]:
        if disposition == windows_local._FILE_CREATE:
            assert security_descriptor == secure._private_security_descriptor
            native_creates.append((name, security_descriptor))
        return native_open(
            parent,
            name,
            directory=directory,
            desired_access=desired_access,
            disposition=disposition,
            security_descriptor=security_descriptor,
        )

    def audited_verify(handle: int) -> None:
        verify_handle(handle)
        verified_handles.add(handle)

    def audited_write(handle: int, content: bytes) -> None:
        assert handle in verified_handles
        write_handle(handle, content)

    monkeypatch.setattr(secure, "_nt_open_relative", audited_native_open)
    monkeypatch.setattr(secure, "_verify_private_handle", audited_verify)
    monkeypatch.setattr(secure, "_write_handle", audited_write)
    state = tmp_path / "creation-audit"
    secure.create_private_directory(state)
    secure.create_private_file(state / "record.json", b"record\n")

    assert [name for name, _descriptor in native_creates] == [
        "creation-audit",
        "record.json",
    ]


def test_windows_all_session_state_entry_kinds_use_the_private_primitives(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    state = tmp_path / "session-state"
    secure.create_private_directory(state)
    session = state / "session-token"
    secure.create_private_directory(session)
    for name in ("datasets", "operations", "staging", "quarantine"):
        secure.create_private_directory(session / name)
    stage = session / "staging" / "stage-entry"
    secure.create_private_directory(stage)
    quarantine_entry = session / "quarantine" / "quarantine-entry"
    secure.create_private_directory(quarantine_entry)
    files = {
        session / ".lock": b"0",
        session / ".defined-quant-session-v1": b"marker\n",
        session / "datasets" / "record.json": b"dataset record\n",
        session / "operations" / "record.json": b"operation record\n",
        stage / "member.json": b"member\n",
        quarantine_entry / ".cleanup-marker": b"cleanup\n",
        quarantine_entry / ".tombstone": b"tombstone\n",
    }
    for path, content in files.items():
        secure.create_private_file(path, content)

    for directory in (
        state,
        session,
        *(session / name for name in ("datasets", "operations", "staging", "quarantine")),
        stage,
        quarantine_entry,
    ):
        secure.verify_private_directory(directory)
    for path, expected in files.items():
        assert secure.read_private_file(path, maximum_bytes=len(expected)) == expected


def test_windows_private_state_supports_unicode_long_paths_and_user_namespace(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    state = tmp_path / "stan-東京-Żółć"
    secure.create_private_directory(state)
    current = state
    for index in range(6):
        current = current / f"część-{index}-{'x' * 48}"
        secure.create_private_directory(current)
    member = current / "rekord-東京.json"
    secure.create_private_file(member, "zażółć-東京\r\n".encode())

    assert len(str(member)) > 260
    assert secure.read_private_file(member, maximum_bytes=64).endswith(b"\r\n")
    service = DefinedQuantService()
    assert service._session_state_root.name == (
        f"defined-quant-local-mcp-{secure.state_namespace}"
    )
    assert "S-1-" not in secure.state_namespace


def test_windows_lockfileex_contention_uses_external_non_deletable_namespace(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    state = tmp_path / "lock-state"
    session = _private_session(
        secure,
        state,
        "session-contention",
        marker_mtime=1.0,
    )
    ready = tmp_path / "lock-held.ready"
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from defined_quant.local_host_platform import local_host_platform",
            "secure = local_host_platform().secure_filesystem",
            "lock = secure.acquire_session_lock(Path(sys.argv[1]), blocking=True)",
            "assert lock is not None",
            "Path(sys.argv[2]).write_bytes(b'ready')",
            "sys.stdin.readline()",
            "secure.release_session_lock(lock)",
        )
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(session / ".lock"), str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not ready.exists():
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail(stderr or stdout or "lock holder did not become ready")
    assert secure.acquire_session_lock(session / ".lock", blocking=False) is None
    _assert_failure(
        SecureFilesystemErrorCode.REMOVE_FAILED,
        lambda: secure.remove_private_tree(session),
    )
    assert session.is_dir()
    stdout, stderr = process.communicate("release\n", timeout=10)
    assert process.returncode == 0, stderr or stdout

    lock = secure.acquire_session_lock(session / ".lock", blocking=False)
    assert lock is not None
    external = state / windows_local._LOCK_NAMESPACE / f"{session.name}.lck"
    assert external.is_file()
    with pytest.raises(OSError):
        external.rename(external.with_suffix(".moved"))
    secure.release_session_lock(lock)


def test_windows_session_lock_uses_one_fixed_byte_for_lock_and_unlock(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "lock-range-state"
    session = _private_session(
        secure,
        state,
        "session-lock-range",
        marker_mtime=1.0,
    )
    original_lock = secure._api.LockFileEx
    original_unlock = secure._api.UnlockFileEx
    calls: list[tuple[str, int, int, int, int]] = []

    def inspect_lock(
        handle: Any,
        flags: int,
        reserved: int,
        low: int,
        high: int,
        overlapped: Any,
    ) -> Any:
        value = ctypes.cast(
            overlapped,
            ctypes.POINTER(windows_local._OVERLAPPED),
        ).contents
        calls.append(("lock", int(flags), int(low), int(high), int(value.Offset)))
        return original_lock(handle, flags, reserved, low, high, overlapped)

    def inspect_unlock(
        handle: Any,
        reserved: int,
        low: int,
        high: int,
        overlapped: Any,
    ) -> Any:
        value = ctypes.cast(
            overlapped,
            ctypes.POINTER(windows_local._OVERLAPPED),
        ).contents
        calls.append(("unlock", 0, int(low), int(high), int(value.Offset)))
        return original_unlock(handle, reserved, low, high, overlapped)

    monkeypatch.setattr(secure._api, "LockFileEx", inspect_lock)
    monkeypatch.setattr(secure._api, "UnlockFileEx", inspect_unlock)
    lock = secure.acquire_session_lock(session / ".lock", blocking=False)
    assert lock is not None
    secure.release_session_lock(lock)

    assert calls == [
        (
            "lock",
            windows_local._LOCKFILE_EXCLUSIVE_LOCK
            | windows_local._LOCKFILE_FAIL_IMMEDIATELY,
            1,
            0,
            0,
        ),
        ("unlock", 0, 1, 0, 0),
    ]


def test_windows_live_session_lock_blocks_nonblocking_orphan_cleanup(
    tmp_path: Path,
) -> None:
    now = 2_000_000_000.0
    store = SessionCas(tmp_path / "live-state")
    marker = store.directory / ".defined-quant-session-v1"
    os.utime(
        marker,
        (
            now - ORPHAN_SESSION_AGE_SECONDS - 1,
            now - ORPHAN_SESSION_AGE_SECONDS - 1,
        ),
    )

    started = time.monotonic()
    assert cleanup_orphan_sessions(tmp_path / "live-state", now=now) == ()
    assert time.monotonic() - started < 2.0
    assert store.directory.is_dir()
    store.close()


def test_windows_concurrent_cleanup_claims_and_deletes_exactly_once(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    now = 2_000_000_000.0
    state = tmp_path / "cleanup-state"
    orphan = _private_session(
        secure,
        state,
        "session-concurrent-cleanup",
        marker_mtime=now - ORPHAN_SESSION_AGE_SECONDS - 1,
    )
    barrier = threading.Barrier(2)
    results: list[tuple[str, ...]] = []
    failures: list[BaseException] = []

    def cleanup() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(
                secure.cleanup_orphan_sessions(
                    state,
                    session_prefix="session-",
                    marker_name=".defined-quant-session-v1",
                    lock_name=".lock",
                    minimum_age_seconds=ORPHAN_SESSION_AGE_SECONDS,
                    now=now,
                    marker_validator=lambda value: value == _MARKER_CONTENT,
                )
            )
        except BaseException as exc:  # pragma: no cover - native assertion
            failures.append(exc)

    threads = [threading.Thread(target=cleanup) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert sorted(results) == [(), (orphan.name,)]
    assert not orphan.exists()
    assert not list(state.glob(f"{windows_local._CLEANUP_TOMBSTONE_PREFIX}*"))


def test_windows_cross_process_cleanup_claims_exactly_once(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    now = 2_000_000_000.0
    state = tmp_path / "process-cleanup-state"
    orphan = _private_session(
        secure,
        state,
        "session-process-cleanup",
        marker_mtime=now - ORPHAN_SESSION_AGE_SECONDS - 1,
    )
    start = tmp_path / "start-cleanup.ready"
    script = "\n".join(
        (
            "import sys, time",
            "from pathlib import Path",
            "from defined_quant.local_host_platform import local_host_platform",
            "from defined_quant.data_records import cas_json_bytes",
            "state, start, now = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])",
            "while not start.exists(): time.sleep(0.01)",
            "secure = local_host_platform().secure_filesystem",
            "marker = cas_json_bytes({",
            "    'application': 'defined-quant-local-mcp',",
            "    'schema_version': 1,",
            "})",
            "removed = secure.cleanup_orphan_sessions(",
            "    state,",
            "    session_prefix='session-',",
            "    marker_name='.defined-quant-session-v1',",
            "    lock_name='.lock',",
            "    minimum_age_seconds=86400,",
            "    now=now,",
            "    marker_validator=lambda value: value == marker,",
            ")",
            "print(','.join(removed) if removed else '-', flush=True)",
        )
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(state), str(start), str(now)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _index in range(2)
    ]
    start.write_bytes(b"go")
    outputs: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr or stdout
        outputs.append(stdout.strip())

    assert sorted(outputs) == ["-", orphan.name]
    assert not orphan.exists()
    assert not list(state.glob(f"{windows_local._CLEANUP_TOMBSTONE_PREFIX}*"))


def test_windows_concurrent_publishers_never_replace_or_publish_partial_content(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    sources = (tmp_path / "publisher-a", tmp_path / "publisher-b")
    contents = (b"first-complete", b"second-complete")
    for source, content in zip(sources, contents, strict=True):
        secure.create_private_directory(source)
        secure.create_private_file(source / "record.bin", content)
        secure.create_private_file(source / "manifest.json", b"{}\n")
    destination = tmp_path / "published"
    barrier = threading.Barrier(2)
    outcomes: list[SecureFilesystemErrorCode | None] = []

    def publish(source: Path) -> None:
        barrier.wait(timeout=10)
        try:
            secure.publish_directory_no_replace(source, destination)
            outcomes.append(None)
        except SecureFilesystemError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=publish, args=(source,)) for source in sources]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(code.value if code is not None else "success" for code in outcomes) == [
        "already_exists",
        "success",
    ]
    assert (destination / "record.bin").read_bytes() in contents
    assert (destination / "manifest.json").read_bytes() == b"{}\n"
    assert sum(source.exists() for source in sources) == 1


def test_windows_cross_process_publishers_use_native_no_replace_atomicity(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    sources = (tmp_path / "process-publisher-a", tmp_path / "process-publisher-b")
    contents = (b"process-first", b"process-second")
    for source, content in zip(sources, contents, strict=True):
        secure.create_private_directory(source)
        secure.create_private_file(source / "record.bin", content)
        secure.create_private_file(source / "manifest.json", b"{}\n")
    destination = tmp_path / "process-published"
    start = tmp_path / "start-publication.ready"
    script = "\n".join(
        (
            "import sys, time",
            "from pathlib import Path",
            "from defined_quant.local_host_platform import (",
            "    SecureFilesystemError,",
            "    local_host_platform,",
            ")",
            "source, destination, start = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])",
            "while not start.exists(): time.sleep(0.01)",
            "try:",
            "    secure = local_host_platform().secure_filesystem",
            "    secure.publish_directory_no_replace(source, destination)",
            "    print('success', flush=True)",
            "except SecureFilesystemError as exc:",
            "    print(exc.code.value, flush=True)",
        )
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(source), str(destination), str(start)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for source in sources
    ]
    start.write_bytes(b"go")
    outputs: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr or stdout
        outputs.append(stdout.strip())

    assert sorted(outputs) == ["already_exists", "success"]
    assert (destination / "record.bin").read_bytes() in contents
    assert (destination / "manifest.json").read_bytes() == b"{}\n"
    assert sum(source.exists() for source in sources) == 1


def test_windows_publication_native_request_disables_replacement(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "native-rename-source"
    destination = tmp_path / "native-rename-destination"
    secure.create_private_directory(source)
    secure.create_private_file(source / "record.bin", b"complete")
    original = secure._api.SetFileInformationByHandle
    replace_values: list[int] = []

    def inspect(
        handle: Any,
        information_class: int,
        information: Any,
        size: int,
    ) -> Any:
        if information_class == windows_local._FILE_RENAME_INFO_CLASS:
            rename = ctypes.cast(
                information,
                ctypes.POINTER(windows_local._FILE_RENAME_INFO),
            ).contents
            replace_values.append(int(rename.ReplaceIfExists))
        return original(handle, information_class, information, size)

    monkeypatch.setattr(secure._api, "SetFileInformationByHandle", inspect)
    secure.publish_directory_no_replace(source, destination)

    assert replace_values == [0]
    assert (destination / "record.bin").read_bytes() == b"complete"


def test_windows_publication_refuses_existing_and_reparse_destinations(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    secure.create_private_directory(existing)
    secure.create_private_file(existing / "record.bin", b"original")
    first_source = tmp_path / "first-source"
    secure.create_private_directory(first_source)
    secure.create_private_file(first_source / "record.bin", b"replacement")

    _assert_failure(
        SecureFilesystemErrorCode.ALREADY_EXISTS,
        lambda: secure.publish_directory_no_replace(first_source, existing),
    )
    assert (existing / "record.bin").read_bytes() == b"original"
    assert first_source.is_dir()

    outside = tmp_path / "outside-destination"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"outside")
    reparse_destination = tmp_path / "reparse-destination"
    _create_junction(reparse_destination, outside)
    second_source = tmp_path / "second-source"
    secure.create_private_directory(second_source)
    secure.create_private_file(second_source / "record.bin", b"replacement")

    _assert_failure(
        SecureFilesystemErrorCode.ALREADY_EXISTS,
        lambda: secure.publish_directory_no_replace(
            second_source,
            reparse_destination,
        ),
    )
    assert reparse_destination.exists()
    assert (outside / "sentinel").read_bytes() == b"outside"
    assert second_source.is_dir()


def test_windows_open_member_without_delete_sharing_blocks_rename_until_closed(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    source = tmp_path / "sharing-source"
    secure.create_private_directory(source)
    member = source / "member.bin"
    secure.create_private_file(member, b"complete")
    destination = tmp_path / "sharing-destination"
    close_handle, opened = _open_without_delete_sharing(member)
    try:
        _assert_failure(
            SecureFilesystemErrorCode.PUBLICATION_FAILED,
            lambda: secure.publish_directory_no_replace(source, destination),
        )
        assert source.is_dir()
        assert not destination.exists()
    finally:
        assert close_handle(ctypes.c_void_p(opened))

    secure.publish_directory_no_replace(source, destination)
    assert (destination / "member.bin").read_bytes() == b"complete"


def test_windows_cleanup_does_not_claim_tree_with_open_nondelete_member(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    now = 2_000_000_000.0
    state = tmp_path / "cleanup-sharing-state"
    session = _private_session(
        secure,
        state,
        "session-open-member",
        marker_mtime=now - ORPHAN_SESSION_AGE_SECONDS - 1,
    )
    member = session / "datasets" / "record.bin"
    secure.create_private_file(member, b"complete")
    close_handle, opened = _open_without_delete_sharing(member)
    try:
        assert secure.cleanup_orphan_sessions(
            state,
            session_prefix="session-",
            marker_name=".defined-quant-session-v1",
            lock_name=".lock",
            minimum_age_seconds=ORPHAN_SESSION_AGE_SECONDS,
            now=now,
            marker_validator=lambda value: value == _MARKER_CONTENT,
        ) == ()
        assert session.is_dir()
        assert not list(state.glob(f"{windows_local._CLEANUP_TOMBSTONE_PREFIX}*"))
    finally:
        assert close_handle(ctypes.c_void_p(opened))

    assert secure.cleanup_orphan_sessions(
        state,
        session_prefix="session-",
        marker_name=".defined-quant-session-v1",
        lock_name=".lock",
        minimum_age_seconds=ORPHAN_SESSION_AGE_SECONDS,
        now=now,
        marker_validator=lambda value: value == _MARKER_CONTENT,
    ) == (session.name,)
    assert not session.exists()


def test_windows_cleanup_recovers_tombstone_after_forced_process_crash(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
) -> None:
    now = 2_000_000_000.0
    state = tmp_path / "crash-cleanup-state"
    session = _private_session(
        secure,
        state,
        "session-forced-crash",
        marker_mtime=now - ORPHAN_SESSION_AGE_SECONDS - 1,
    )
    script = "\n".join(
        (
            "import os, sys",
            "from pathlib import Path",
            "from defined_quant.local_host_platform import local_host_platform",
            "secure = local_host_platform().secure_filesystem",
            "session = Path(sys.argv[1])",
            "drive, state_parts, name = secure._session_lock_location(session / '.lock')",
            "lock = secure._acquire_external_session_lock(drive, state_parts, name, blocking=True)",
            "assert lock is not None",
            "secure._new_tombstone(session, cleanup_session_name=name)",
            "os._exit(23)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(session)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 23, completed.stderr
    assert not session.exists()
    assert len(list(state.glob(f"{windows_local._CLEANUP_TOMBSTONE_PREFIX}*"))) == 1

    removed: tuple[str, ...] = ()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not removed:
        removed = secure.cleanup_orphan_sessions(
            state,
            session_prefix="session-",
            marker_name=".defined-quant-session-v1",
            lock_name=".lock",
            minimum_age_seconds=ORPHAN_SESSION_AGE_SECONDS,
            now=now,
            marker_validator=lambda value: value == _MARKER_CONTENT,
        )
        if not removed:
            time.sleep(0.05)

    assert removed == (session.name,)
    assert not list(state.glob(f"{windows_local._CLEANUP_TOMBSTONE_PREFIX}*"))


@pytest.mark.parametrize("crash_point", ["before", "after"])
def test_windows_publication_is_complete_or_absent_across_forced_process_crash(
    secure: WindowsSecureFilesystem,
    tmp_path: Path,
    crash_point: str,
) -> None:
    stage = tmp_path / f"crash-stage-{crash_point}"
    destination = tmp_path / f"crash-destination-{crash_point}"
    script = "\n".join(
        (
            "import os, sys",
            "from pathlib import Path",
            "from defined_quant.local_host_platform import local_host_platform",
            "secure = local_host_platform().secure_filesystem",
            "stage, destination, point = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]",
            "secure.create_private_directory(stage)",
            "secure.create_private_file(stage / 'record.bin', b'complete-record')",
            "secure.create_private_file(stage / 'manifest.json', b'{}\\n')",
            "if point == 'before': os._exit(31)",
            "secure.publish_directory_no_replace(stage, destination)",
            "os._exit(32)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(stage), str(destination), crash_point],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == (31 if crash_point == "before" else 32)
    if crash_point == "before":
        assert not destination.exists()
        assert (stage / "record.bin").read_bytes() == b"complete-record"
        secure.remove_private_tree(stage)
    else:
        assert not stage.exists()
        assert (destination / "record.bin").read_bytes() == b"complete-record"
        assert (destination / "manifest.json").read_bytes() == b"{}\n"
        secure.remove_private_tree(destination)
