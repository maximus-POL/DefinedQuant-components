"""Fail CI if checkout tests import production code outside installed wheels."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT_REQUIRED = frozenset(
    {
        "authoring/tests/test_agent_protocol.py",
        "authoring/tests/test_alpha_platforms.py",
        "authoring/tests/test_alpha_product_story.py",
        "authoring/tests/test_data_records.py",
        "authoring/tests/test_dataset_registry.py",
        "authoring/tests/test_local_host_platform.py",
        "authoring/tests/test_operation_records.py",
        "authoring/tests/test_operation_runtime.py",
        "authoring/tests/test_session_cas.py",
        "authoring/tests/test_stdio_framing.py",
        "authoring/tests/test_worker_runtime.py",
    }
)
WINDOWS_REQUIRED = frozenset({"authoring/tests/test_windows_local_host.py"})
MCP_REQUIRED = frozenset(
    {
        "mcp_server/tests/test_packaging.py",
        "mcp_server/tests/test_server.py",
    }
)
PRODUCTION_PREFIXES = (
    "defined_quant",
    "defined_quant_adapter_dq_native",
    "defined_quant_protocol",
    "defined_quant_mcp",
)
FORBIDDEN_SOURCE_PREFIXES = ("shared", "protocol")
_SKIPPED_REPORTS: set[str] = set()


def _checkout_root() -> Path:
    raw = os.environ.get("DEFINED_QUANT_CHECKOUT_ROOT")
    if not raw:
        raise RuntimeError("DEFINED_QUANT_CHECKOUT_ROOT is required")
    return Path(raw).resolve()


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _module_path(module: ModuleType) -> Path | None:
    raw = getattr(module, "__file__", None)
    return Path(raw).resolve() if isinstance(raw, str) else None


def _source_import_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for name, module in tuple(sys.modules.items()):
        if module is None:
            continue
        if name in FORBIDDEN_SOURCE_PREFIXES or name.startswith(
            tuple(f"{prefix}." for prefix in FORBIDDEN_SOURCE_PREFIXES)
        ):
            violations.append(f"forbidden source namespace imported: {name}")
            continue
        if not (
            name in PRODUCTION_PREFIXES
            or name.startswith(tuple(f"{prefix}." for prefix in PRODUCTION_PREFIXES))
        ):
            continue
        path = _module_path(module)
        if path is not None and path.is_relative_to(root):
            violations.append(f"production module imported from checkout: {name} ({path})")
    return violations


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    _SKIPPED_REPORTS.clear()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped or getattr(report, "wasxfail", None) is not None:
        _SKIPPED_REPORTS.add(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del exitstatus
    root = _checkout_root()
    suite = os.environ.get("DEFINED_QUANT_SUITE_KIND")
    required = (
        ROOT_REQUIRED | (WINDOWS_REQUIRED if sys.platform == "win32" else frozenset())
        if suite == "root"
        else MCP_REQUIRED if suite == "mcp" else None
    )
    violations = _source_import_violations(root)
    forbidden_markers = sorted(
        f"{item.nodeid} ({marker.name})"
        for item in session.items
        for marker in item.iter_markers()
        if marker.name in {"skip", "skipif", "xfail"}
    )
    if forbidden_markers:
        violations.append(
            "required acceptance items contain skip/xfail markers: "
            + ", ".join(forbidden_markers)
        )
    if _SKIPPED_REPORTS:
        violations.append(
            "required acceptance items skipped or xfailed: "
            + ", ".join(sorted(_SKIPPED_REPORTS))
        )
    if required is None:
        violations.append("DEFINED_QUANT_SUITE_KIND must be root or mcp")
    else:
        collected = {
            _relative(Path(str(item.path)), root)
            for item in session.items
            if Path(str(item.path)).resolve().is_relative_to(root)
        }
        missing = sorted(required - collected)
        if missing:
            violations.append(
                "required acceptance modules were not collected: " + ", ".join(missing)
            )
    if violations:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("=", "installed-wheel isolation failures")
            for violation in violations:
                reporter.write_line(violation)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


__all__ = [
    "pytest_runtest_logreport",
    "pytest_sessionfinish",
    "pytest_sessionstart",
]
