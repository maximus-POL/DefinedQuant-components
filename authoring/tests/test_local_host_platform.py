"""Contract tests for the transport-neutral local host platform facade."""

from __future__ import annotations

import inspect
import os
import stat
import sys
from pathlib import Path

import defined_quant.local_host_platform as host_platform
import pytest
from defined_quant.local_host_platform import (
    PinnedRootHandle,
    SecureFilesystem,
    SecureFilesystemError,
    SecureFilesystemErrorCode,
    SessionLockHandle,
    implemented_provider_id,
    local_host_platform,
)
from defined_quant.service import DefinedQuantService

SECURE_FILESYSTEM_SURFACE = {
    "pin_configured_root",
    "close_pinned_root",
    "read_file_beneath",
    "read_regular_file",
    "create_private_directory",
    "verify_private_directory",
    "create_private_file",
    "read_private_file",
    "private_tree_size",
    "acquire_session_lock",
    "release_session_lock",
    "publish_directory_no_replace",
    "remove_private_tree",
    "remove_locked_session_tree",
    "cleanup_orphan_sessions",
}


def test_secure_filesystem_facade_has_only_the_frozen_minimal_surface() -> None:
    methods = {
        name
        for name, member in inspect.getmembers(SecureFilesystem, inspect.isfunction)
        if not name.startswith("_")
    }

    assert methods == SECURE_FILESYSTEM_SURFACE
    assert implemented_provider_id() in {"darwin", "linux", "win32"}
    platform = local_host_platform()
    assert platform.provider_id == implemented_provider_id()
    assert platform.state_namespace
    assert all(
        character.isascii() and (character.isalnum() or character == "-")
        for character in platform.state_namespace
    )


def test_unimplemented_platform_selection_fails_with_only_the_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_platform.os, "name", "java")
    monkeypatch.setattr(host_platform.sys, "platform", "java")
    assert host_platform._select_local_host_platform() is None
    monkeypatch.setattr(host_platform, "_LOCAL_HOST_PLATFORM", None)

    with pytest.raises(SecureFilesystemError) as unavailable:
        host_platform.local_host_platform()

    assert unavailable.value.code is SecureFilesystemErrorCode.CAPABILITY_UNAVAILABLE
    assert unavailable.value.args == ("capability_unavailable",)
    assert unavailable.value.__cause__ is None


def test_configured_root_handles_are_opaque_and_reads_return_only_bytes(
    tmp_path: Path,
) -> None:
    secure = local_host_platform().secure_filesystem
    configured_root = tmp_path / "configured"
    configured_root.mkdir()
    payload = b"timestamp,price\n2024-01-01,100\n"
    (configured_root / "input.csv").write_bytes(payload)

    handle = secure.pin_configured_root(configured_root)
    assert isinstance(handle, PinnedRootHandle)
    assert repr(handle) == "<PinnedRootHandle>"
    assert not [name for name in dir(handle) if not name.startswith("_")]
    assert secure.read_file_beneath(
        handle,
        ("input.csv",),
        maximum_bytes=len(payload),
    ) == payload
    secure.close_pinned_root(handle)

    with pytest.raises(SecureFilesystemError) as closed:
        secure.read_file_beneath(handle, ("input.csv",), maximum_bytes=len(payload))
    assert closed.value.code is SecureFilesystemErrorCode.ACCESS_DENIED
    assert closed.value.__cause__ is None
    assert closed.value.args == ("access_denied",)


def test_provider_exposes_stable_errors_without_native_paths_or_messages(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "caller-secret-root"

    with pytest.raises(SecureFilesystemError) as refused:
        local_host_platform().secure_filesystem.pin_configured_root(missing)

    assert refused.value.code is SecureFilesystemErrorCode.ACCESS_DENIED
    assert refused.value.__cause__ is None
    assert str(refused.value) == "access_denied"
    assert os.fspath(missing) not in str(refused.value)
    assert "Errno" not in str(refused.value)
    assert "WinError" not in str(refused.value)


def test_private_directory_and_file_creation_is_verified_through_the_facade(
    tmp_path: Path,
) -> None:
    secure = local_host_platform().secure_filesystem
    private = tmp_path / "private-state"
    secure.create_private_directory(private)
    secure.verify_private_directory(private)
    member = private / "member.json"
    secure.create_private_file(member, b"{}\n")

    assert secure.read_private_file(member, maximum_bytes=3) == b"{}\n"
    assert secure.private_tree_size(private) == 3
    if sys.platform != "win32":
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
        assert stat.S_IMODE(member.stat().st_mode) == 0o600


def test_default_state_root_uses_the_provider_user_namespace() -> None:
    platform = local_host_platform()
    service = DefinedQuantService()

    assert service._session_state_root.name == (
        f"defined-quant-local-mcp-{platform.state_namespace}"
    )


def test_private_creation_locking_and_no_replace_publication_contract(
    tmp_path: Path,
) -> None:
    secure = local_host_platform().secure_filesystem
    state = tmp_path / "state"
    secure.create_private_directory(state)
    private = state / "session-contract"
    secure.create_private_directory(private)
    secure.verify_private_directory(private)
    member = private / "member.json"
    secure.create_private_file(member, b"{}\n")
    assert secure.read_private_file(member, maximum_bytes=3) == b"{}\n"
    assert secure.private_tree_size(private) == 3

    lock_path = private / ".lock"
    secure.create_private_file(lock_path, b"0")
    lock = secure.acquire_session_lock(lock_path, blocking=True)
    assert isinstance(lock, SessionLockHandle)
    assert repr(lock) == "<SessionLockHandle>"
    assert not [name for name in dir(lock) if not name.startswith("_")]
    assert secure.acquire_session_lock(lock_path, blocking=False) is None
    secure.release_session_lock(lock)

    source = tmp_path / "source"
    secure.create_private_directory(source)
    secure.create_private_file(source / "record.json", b"first")
    destination = tmp_path / "destination"
    secure.publish_directory_no_replace(source, destination)
    assert not source.exists()
    assert (destination / "record.json").read_bytes() == b"first"

    competing = tmp_path / "competing"
    secure.create_private_directory(competing)
    with pytest.raises(SecureFilesystemError) as exists:
        secure.publish_directory_no_replace(competing, destination)
    assert exists.value.code is SecureFilesystemErrorCode.ALREADY_EXISTS
    assert exists.value.__cause__ is None
    secure.remove_private_tree(competing)
    secure.remove_private_tree(destination)
    secure.remove_private_tree(state)


def test_orphan_cleanup_returns_names_and_preserves_locked_sessions(tmp_path: Path) -> None:
    secure = local_host_platform().secure_filesystem
    root = tmp_path / "state"
    secure.create_private_directory(root)
    marker_content = b'{"application":"defined-quant-local-mcp","schema_version":1}\n'

    def orphan(name: str) -> tuple[Path, Path]:
        directory = root / f"session-{name}"
        secure.create_private_directory(directory)
        marker = directory / ".defined-quant-session-v1"
        secure.create_private_file(marker, marker_content)
        lock_path = directory / ".lock"
        secure.create_private_file(lock_path, b"0")
        os.utime(marker, (1.0, 1.0))
        return directory, lock_path

    old, _old_lock = orphan("old")
    live, live_lock_path = orphan("live")
    live_lock = secure.acquire_session_lock(live_lock_path, blocking=True)
    assert live_lock is not None

    removed = secure.cleanup_orphan_sessions(
        root,
        session_prefix="session-",
        marker_name=".defined-quant-session-v1",
        lock_name=".lock",
        minimum_age_seconds=10,
        now=100.0,
        marker_validator=lambda content: content == marker_content,
    )

    assert removed == ("session-old",)
    assert all(isinstance(name, str) for name in removed)
    assert not old.exists()
    assert live.exists()
    secure.release_session_lock(live_lock)
    secure.remove_private_tree(live)
    secure.remove_private_tree(root)
