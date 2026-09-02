"""Distribution and universal-lock boundaries for the optional MCP package."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = ROOT / "mcp_server"


def test_core_and_mcp_dependencies_remain_separate() -> None:
    core = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    transport = tomllib.loads((MCP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert core["project"]["dependencies"] == ["pydantic>=2.7,<3"]
    assert transport["project"]["dependencies"] == [
        "defined-quant-adapter-dq-native==1.0.0",
        "defined-quant==0.1.3",
        "mcp==2.0.0",
    ]
    assert transport["tool"]["uv"]["sources"]["defined-quant-adapter-dq-native"] == {
        "path": "../adapters/dq_native"
    }
    assert all(
        not dependency.startswith("mcp-types")
        for dependency in transport["project"]["dependencies"]
    )


def test_universal_lock_contains_windows_and_native_wheel_hashes() -> None:
    lock_text = (MCP_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "mcp"\nversion = "2.0.0"' in lock_text
    assert '{ name = "pywin32", marker = "sys_platform == \'win32\'" }' in lock_text
    assert 'name = "pywin32"' in lock_text
    assert "win_amd64.whl" in lock_text
    assert "macosx_11_0_arm64.whl" in lock_text
    assert "manylinux" in lock_text
    assert "hash = \"sha256:" in lock_text


def test_sdk_imports_are_isolated_to_server_module() -> None:
    package = MCP_ROOT / "src" / "defined_quant_mcp"
    importing: set[Path] = set()
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names.append(node.module)
            if any(name == "mcp" or name.startswith("mcp.") for name in names):
                importing.add(path)
    assert importing == {package / "server.py"}
