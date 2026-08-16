"""Keep the MCP release target and temporary platform gaps explicit and synchronized."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from authoring.pytest_native_inventory import (
    POSIX_SESSION_FUNCTIONS,
    WINDOWS_LOCAL_HOST_FUNCTION_COUNT,
    WINDOWS_WORKER_FUNCTIONS,
)

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "LOCAL_MCP_ALPHA_DESIGN.md"
SESSION_CAS = ROOT / "shared" / "session_cas.py"
DATASET_REGISTRY = ROOT / "shared" / "dataset_registry.py"
OPERATION_RUNTIME = ROOT / "shared" / "operation_runtime.py"
OPERATION_RECORDS = ROOT / "shared" / "operation_records.py"
LOCAL_HOST_PLATFORM = ROOT / "shared" / "local_host_platform.py"
POSIX_PROVIDER = ROOT / "shared" / "_posix_local_host.py"
WINDOWS_PROVIDER = ROOT / "shared" / "_windows_local_host.py"
WORKER_RUNTIME = ROOT / "shared" / "worker_runtime.py"
WORKER_PROCESS = ROOT / "shared" / "worker_process.py"
POSIX_WORKER = ROOT / "shared" / "_posix_worker.py"
WINDOWS_WORKER = ROOT / "shared" / "_windows_worker.py"

_REQUIRED_PLATFORM_MARKER = re.compile(
    r"\*\*Required MCP alpha release platforms:\*\* (?P<platforms>[^.]+)\."
)
_IMPLEMENTED_PROVIDER_MARKER = re.compile(
    r"\*\*Currently implemented MCP alpha platform providers:\*\* "
    r"(?P<platforms>[^.]+)\."
)
_DOCUMENTED_TO_RUNTIME = {"macOS": "darwin", "Linux": "linux", "Windows": "win32"}
_REQUIRED_TEST_ROOTS = (
    ROOT / "categories",
    ROOT / "authoring" / "tests",
    ROOT / ".agents" / "skills" / "use-defined-quant" / "tests",
    ROOT / "mcp_server" / "tests",
)
_FORBIDDEN_PYTEST_CALLS = {
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "pytest.skip",
    "pytest.xfail",
}


def _documented_platforms(marker: re.Pattern[str]) -> set[str]:
    matches = marker.findall(DESIGN.read_text(encoding="utf-8"))
    assert len(matches) == 1, "the design must declare this platform set exactly once"
    names = matches[0].replace(", and ", ", ").replace(" and ", ", ").split(", ")
    assert all(name in _DOCUMENTED_TO_RUNTIME for name in names)
    return {_DOCUMENTED_TO_RUNTIME[name] for name in names}


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(functions) == 1
    return functions[0]


def _dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _selected_platforms(tree: ast.Module) -> set[str]:
    function = _function(tree, "_select_local_host_platform")
    platforms: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left = node.left
        comparator = node.comparators[0]
        if (
            isinstance(node.ops[0], ast.Eq)
            and isinstance(left, ast.Attribute)
            and isinstance(left.value, ast.Name)
            and left.value.id == "sys"
            and left.attr == "platform"
            and isinstance(comparator, ast.Constant)
            and isinstance(comparator.value, str)
        ):
            platforms.add(comparator.value)
    return platforms


def test_required_alpha_platforms_and_implemented_providers_are_explicit() -> None:
    required = _documented_platforms(_REQUIRED_PLATFORM_MARKER)
    implemented = _documented_platforms(_IMPLEMENTED_PROVIDER_MARKER)

    assert required == {"darwin", "linux", "win32"}
    assert implemented == {"darwin", "linux", "win32"}

    platform_tree = ast.parse(LOCAL_HOST_PLATFORM.read_text(encoding="utf-8"))
    assert _selected_platforms(platform_tree) == required


def test_provider_selection_is_the_only_production_platform_check() -> None:
    shared_paths = tuple((ROOT / "shared").glob("*.py"))
    occurrences: list[tuple[Path, ast.Attribute]] = []
    for path in shared_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        occurrences.extend(
            (path, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (node.value.id, node.attr) in {("os", "name"), ("sys", "platform")}
        )

    assert occurrences
    assert {path for path, _node in occurrences} == {LOCAL_HOST_PLATFORM}
    platform_tree = ast.parse(LOCAL_HOST_PLATFORM.read_text(encoding="utf-8"))
    selector = _function(platform_tree, "_select_local_host_platform")
    selector_checks = {
        (node.value.id, node.attr)
        for node in ast.walk(selector)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    assert {("os", "name"), ("sys", "platform")} <= selector_checks


def test_required_acceptance_suites_contain_no_skip_or_xfail_calls() -> None:
    forbidden: list[tuple[Path, int, str]] = []
    for root in _REQUIRED_TEST_ROOTS:
        for path in root.rglob("test_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            forbidden.extend(
                (path, node.lineno, dotted)
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and (dotted := _dotted_name(node.func)) in _FORBIDDEN_PYTEST_CALLS
            )
    assert forbidden == []


def test_native_only_suite_inventory_has_frozen_nonzero_counts() -> None:
    assert WINDOWS_LOCAL_HOST_FUNCTION_COUNT == 34
    assert WINDOWS_WORKER_FUNCTIONS == {
        "test_windows_job_prevents_child_breakaway",
        "test_windows_worker_keeps_scratch_at_the_selected_output_parent",
        "test_windows_worker_supports_unicode_and_long_local_paths",
    }
    assert POSIX_SESSION_FUNCTIONS == {
        "test_fifo_replacing_a_cas_member_is_quarantined_without_blocking",
        "test_session_state_root_must_be_owner_private",
    }


def test_native_secure_filesystem_mechanisms_live_only_in_platform_providers() -> None:
    callers = (SESSION_CAS, DATASET_REGISTRY, OPERATION_RUNTIME, OPERATION_RECORDS)
    forbidden = (
        "_atomic_rename_no_replace",
        "renameat2",
        "renamex_np",
        "fcntl",
        "O_NOFOLLOW",
        "O_DIRECTORY",
        "dir_fd",
    )
    for path in callers:
        source = path.read_text(encoding="utf-8")
        assert all(token not in source for token in forbidden)
        assert "local_host_platform" in source

    provider_source = POSIX_PROVIDER.read_text(encoding="utf-8")
    assert "def publish_directory_no_replace" in provider_source
    assert "renameat2" in provider_source
    assert "renamex_np" in provider_source

    windows_source = WINDOWS_PROVIDER.read_text(encoding="utf-8")
    assert "NtCreateFile" in windows_source
    assert "RootDirectory" in windows_source
    assert "FILE_OPEN_REPARSE_POINT" in windows_source
    assert "GetFileInformationByHandleEx" in windows_source
    assert "GetDriveTypeW" in windows_source
    assert "FileIsRemoteDeviceInformation" in windows_source or (
        "_FILE_IS_REMOTE_DEVICE_INFO_CLASS" in windows_source
        and "NtQueryInformationFile" in windows_source
    )
    assert "lstat" not in windows_source
    assert ".resolve(" not in windows_source
    assert "OpenProcessToken" in windows_source
    assert "GetTokenInformation" in windows_source
    assert "SecurityDescriptor=security_descriptor" in windows_source
    assert "D:P(A;;FA;;;" in windows_source
    assert "GetSecurityInfo" in windows_source
    assert "_SE_DACL_PROTECTED" in windows_source
    assert "GetAce" in windows_source
    assert "LockFileEx" in windows_source
    assert "UnlockFileEx" in windows_source
    assert "_LOCKFILE_FAIL_IMMEDIATELY" in windows_source
    assert "_LOCK_NAMESPACE" in windows_source
    assert "NtSetInformationFile" in windows_source
    assert "_FILE_RENAME_INFORMATION_CLASS" in windows_source
    assert "info.ReplaceIfExists = 0" in windows_source
    assert "_FILE_DISPOSITION_INFO_CLASS" in windows_source
    assert "_CLEANUP_TOMBSTONE_PREFIX" in windows_source


def test_native_worker_mechanisms_live_behind_one_shared_lifecycle() -> None:
    shared_source = WORKER_RUNTIME.read_text(encoding="utf-8")
    contract_source = WORKER_PROCESS.read_text(encoding="utf-8")
    posix_source = POSIX_WORKER.read_text(encoding="utf-8")
    windows_source = WINDOWS_WORKER.read_text(encoding="utf-8")

    assert "multiprocessing" not in shared_source
    assert "multiprocessing" not in contract_source
    assert "subprocess" not in shared_source
    assert "_CleanupState" in shared_source
    assert "environment = {" in shared_source
    assert "MAX_WORKER_MEMORY_BYTES = 512 * 1024 * 1024" in shared_source
    assert "MAX_WORKER_STREAM_BYTES = 1024 * 1024" in shared_source

    assert "subprocess.Popen" in posix_source
    assert '"defined_quant._posix_worker_entry"' in posix_source
    assert '"-m"' in posix_source
    assert "start_new_session=True" in posix_source
    assert "close_fds=True" in posix_source
    assert "pass_fds=(control_write,)" in posix_source
    assert "shell=False" in posix_source
    assert "os.killpg" in posix_source

    assert "_CREATE_SUSPENDED" in windows_source
    assert "_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in windows_source
    assert "_JOB_OBJECT_LIMIT_JOB_MEMORY" in windows_source
    assert "_PROC_THREAD_ATTRIBUTE_HANDLE_LIST" in windows_source
    assert "CreateProcessW" in windows_source
    assert '"defined_quant._windows_worker_entry"' in windows_source
    assert '"-m"' in windows_source
    assert "bootstrap_directory" in windows_source
    assert "inspection_work_root_parents" in posix_source
    assert "inspection_work_root_parents" in windows_source
    assert "work_root_parents" in posix_source
    assert "work_root_parents" in windows_source
    assign = windows_source.index("self._api.AssignProcessToJobObject(")
    resume = windows_source.index("self._api.ResumeThread(")
    assert assign < resume
    assert "CREATE_BREAKAWAY_FROM_JOB" not in windows_source
    assert "SILENT_BREAKAWAY_OK" not in windows_source
