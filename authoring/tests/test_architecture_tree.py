"""Keep the authoritative shared-module tree synchronized with the package source."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
SHARED = ROOT / "shared"

_MODULE_ENTRY = re.compile(
    r"^│   [├└]── (?P<name>[A-Za-z_][A-Za-z0-9_]*\.py)"
    r"\s{2,}(?P<role>\S.*)$",
    re.MULTILINE,
)
_AGENTS_MODULE_ENTRY = re.compile(
    r"^\| `shared/(?P<name>[A-Za-z_][A-Za-z0-9_]*\.py)` "
    r"\| (?P<role>\S.*) \|$",
    re.MULTILINE,
)


def _actual_shared_modules() -> list[str]:
    return sorted(path.name for path in SHARED.glob("*.py") if path.is_file())


def _documented_shared_tree() -> str:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text.split("## 2. Author-facing structure", maxsplit=1)[1]
    section = section.split("## 3. Installed Python namespaces", maxsplit=1)[0]
    shared_tree = section.split("├── shared/\n", maxsplit=1)[1]
    return shared_tree.split("├── protocol/\n", maxsplit=1)[0]


def test_every_top_level_shared_module_is_documented_with_one_role() -> None:
    entries = _MODULE_ENTRY.findall(_documented_shared_tree())
    documented = [name for name, _role in entries]

    assert len(documented) == len(set(documented)), "shared modules must appear once"
    assert sorted(documented) == _actual_shared_modules()


def test_every_top_level_shared_module_is_in_agents_orientation() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    orientation = text.split("## Orientation", maxsplit=1)[1]
    orientation = orientation.split("## Local MCP alpha", maxsplit=1)[0]
    entries = _AGENTS_MODULE_ENTRY.findall(orientation)
    documented = [name for name, _role in entries]

    assert len(documented) == len(set(documented)), "AGENTS modules must appear once"
    assert sorted(documented) == _actual_shared_modules()
