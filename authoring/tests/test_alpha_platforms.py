"""Keep the documented MCP-alpha platform set aligned with fail-closed runtime gates."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "LOCAL_MCP_ALPHA_DESIGN.md"
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
README = ROOT / "README.md"
SESSION_CAS = ROOT / "shared" / "session_cas.py"
DATASET_REGISTRY = ROOT / "shared" / "dataset_registry.py"

_PLATFORM_MARKER = re.compile(
    r"\*\*Supported MCP alpha platforms:\*\* (?P<platforms>[^.]+)\."
)
_DOCUMENTED_TO_RUNTIME = {"macOS": "darwin", "Linux": "linux", "Windows": "win32"}


def _documented_platforms(path: Path) -> set[str]:
    matches = _PLATFORM_MARKER.findall(path.read_text(encoding="utf-8"))
    assert len(matches) == 1, f"{path.name} must declare the platform set exactly once"
    names = matches[0].split(" and ")
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


def _method(tree: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    assert len(classes) == 1
    methods = [
        node
        for node in classes[0].body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    ]
    assert len(methods) == 1
    return methods[0]


def _has_posix_nofollow_gate(function: ast.FunctionDef) -> bool:
    return any(
        "os.name != 'posix'" in ast.unparse(node.test)
        and "not hasattr(os, 'O_NOFOLLOW')" in ast.unparse(node.test)
        for node in ast.walk(function)
        if isinstance(node, ast.If)
    )


def _atomic_publication_platforms(tree: ast.Module) -> set[str]:
    function = _function(tree, "_atomic_rename_no_replace")
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


def test_documented_alpha_platforms_match_the_implemented_gates() -> None:
    documented = {
        frozenset(_documented_platforms(path))
        for path in (DESIGN, ARCHITECTURE, README)
    }
    assert len(documented) == 1
    supported = set(next(iter(documented)))

    session_tree = ast.parse(SESSION_CAS.read_text(encoding="utf-8"))
    registry_tree = ast.parse(DATASET_REGISTRY.read_text(encoding="utf-8"))
    assert _has_posix_nofollow_gate(_method(session_tree, "SessionCas", "__init__"))
    assert _has_posix_nofollow_gate(_function(session_tree, "cleanup_orphan_sessions"))
    assert _has_posix_nofollow_gate(
        _method(registry_tree, "ConfiguredFileRoots", "__init__")
    )
    assert supported == _atomic_publication_platforms(session_tree) == {
        "darwin",
        "linux",
    }
