"""Keep the authoritative shared-module tree synchronized with the package source."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
SHARED = ROOT / "shared"

_MODULE_ENTRY = re.compile(
    r"^│   [├└]── (?P<name>[A-Za-z_][A-Za-z0-9_]*\.py)"
    r"\s{2,}(?P<role>\S.*)$",
    re.MULTILINE,
)


def _documented_shared_tree() -> str:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text.split("## 2. Author-facing structure", maxsplit=1)[1]
    section = section.split("## 3. Installed Python namespaces", maxsplit=1)[0]
    shared_tree = section.split("├── shared/\n", maxsplit=1)[1]
    return shared_tree.split("├── protocol/\n", maxsplit=1)[0]


def test_every_top_level_shared_module_is_documented_with_one_role() -> None:
    entries = _MODULE_ENTRY.findall(_documented_shared_tree())
    documented = [name for name, _role in entries]
    actual = sorted(path.name for path in SHARED.glob("*.py") if path.is_file())

    assert len(documented) == len(set(documented)), "shared modules must appear once"
    assert sorted(documented) == actual
