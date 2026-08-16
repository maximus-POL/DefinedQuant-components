"""Make native-only acceptance collection explicit and fail closed on missing suites."""

from __future__ import annotations

import sys
from collections.abc import Sequence

import pytest

WINDOWS_LOCAL_HOST_FUNCTION_COUNT = 34
WINDOWS_WORKER_FUNCTIONS = frozenset(
    {
        "test_windows_job_prevents_child_breakaway",
        "test_windows_worker_keeps_scratch_at_the_selected_output_parent",
        "test_windows_worker_supports_unicode_and_long_local_paths",
    }
)
POSIX_SESSION_FUNCTIONS = frozenset(
    {
        "test_fifo_replacing_a_cas_member_is_quarantined_without_blocking",
        "test_session_state_root_must_be_owner_private",
    }
)


def _function_names(items: Sequence[pytest.Item], path: str) -> set[str]:
    names: set[str] = set()
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if not nodeid.startswith(path + "::"):
            continue
        name = nodeid.split("::", maxsplit=2)[1].split("[", maxsplit=1)[0]
        names.add(name)
    return names


def _is_complete_acceptance_collection(items: Sequence[pytest.Item]) -> bool:
    nodeids = tuple(item.nodeid.replace("\\", "/") for item in items)
    return all(
        any(nodeid.startswith(path + "::") for nodeid in nodeids)
        for path in (
            "authoring/tests/test_alpha_platforms.py",
            "authoring/tests/test_session_cas.py",
            "authoring/tests/test_worker_runtime.py",
        )
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if not _is_complete_acceptance_collection(items):
        return
    windows_local = _function_names(
        items,
        "authoring/tests/test_windows_local_host.py",
    )
    windows_worker = _function_names(
        items,
        "authoring/tests/test_worker_runtime.py",
    ) & WINDOWS_WORKER_FUNCTIONS
    posix_session = _function_names(
        items,
        "authoring/tests/test_session_cas.py",
    ) & POSIX_SESSION_FUNCTIONS

    if sys.platform == "win32":
        assert len(windows_local) == WINDOWS_LOCAL_HOST_FUNCTION_COUNT
        assert windows_worker == WINDOWS_WORKER_FUNCTIONS
        assert posix_session == set()
    elif sys.platform in {"darwin", "linux"}:
        assert windows_local == set()
        assert windows_worker == set()
        assert posix_session == POSIX_SESSION_FUNCTIONS
    else:
        raise AssertionError("native acceptance inventory has no provider for this platform")


def pytest_report_collectionfinish(
    config: pytest.Config,
    start_path: object,
    items: list[pytest.Item],
) -> None:
    del start_path
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or not _is_complete_acceptance_collection(items):
        return
    if sys.platform == "win32":
        active = "Windows local-host=34, Windows worker=3"
        inactive = "POSIX session=2"
    else:
        active = "POSIX session=2"
        inactive = "Windows local-host=34, Windows worker=3"
    reporter.write_line(
        f"native-suite inventory: platform={sys.platform}; active: {active}; "
        f"inactive: {inactive}"
    )


__all__ = [
    "POSIX_SESSION_FUNCTIONS",
    "WINDOWS_LOCAL_HOST_FUNCTION_COUNT",
    "WINDOWS_WORKER_FUNCTIONS",
    "pytest_collection_modifyitems",
    "pytest_report_collectionfinish",
]
