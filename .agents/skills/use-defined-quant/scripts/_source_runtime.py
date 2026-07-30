"""Activate the current source checkout as the ``defined_quant`` package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def activate_source_runtime() -> None:
    """Prefer the checkout beside this skill over a stale installed package."""

    project_root = Path(__file__).resolve().parents[4]
    shared_root = project_root / "shared"
    categories_root = project_root / "categories"
    initializer = shared_root / "__init__.py"
    if not initializer.is_file() or not categories_root.is_dir():
        return

    loaded = sys.modules.get("defined_quant")
    loaded_file = getattr(loaded, "__file__", None)
    if isinstance(loaded_file, str) and Path(loaded_file).resolve() == initializer.resolve():
        return

    for module_name in tuple(sys.modules):
        if module_name == "defined_quant" or module_name.startswith("defined_quant."):
            del sys.modules[module_name]

    specification = importlib.util.spec_from_file_location(
        "defined_quant",
        initializer,
        submodule_search_locations=[str(shared_root), str(categories_root)],
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot construct the Defined Quant source-package specification")
    module = importlib.util.module_from_spec(specification)
    sys.modules["defined_quant"] = module
    specification.loader.exec_module(module)
    if not isinstance(module, ModuleType):
        raise RuntimeError("Defined Quant source-package activation returned no module")


__all__ = ["activate_source_runtime"]
