"""Activate this checkout's public packages for direct skill-script execution."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _activate_package(
    package_name: str,
    initializer: Path,
    search_locations: tuple[Path, ...],
) -> None:
    """Load one source package unless this exact initializer is already active."""

    if not initializer.is_file() or any(not path.is_dir() for path in search_locations):
        return

    loaded = sys.modules.get(package_name)
    loaded_file = getattr(loaded, "__file__", None)
    if isinstance(loaded_file, str) and Path(loaded_file).resolve() == initializer.resolve():
        return

    for module_name in tuple(sys.modules):
        if module_name == package_name or module_name.startswith(f"{package_name}."):
            del sys.modules[module_name]

    specification = importlib.util.spec_from_file_location(
        package_name,
        initializer,
        submodule_search_locations=[str(path) for path in search_locations],
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot construct the {package_name} source-package specification")

    module = importlib.util.module_from_spec(specification)
    sys.modules[package_name] = module
    specification.loader.exec_module(module)
    if not isinstance(module, ModuleType):
        raise RuntimeError(f"{package_name} source-package activation returned no module")


def activate_source_runtime() -> None:
    """Prefer this checkout over stale installed Defined Quant packages."""

    project_root = Path(__file__).resolve().parents[4]
    shared_root = project_root / "shared"
    categories_root = project_root / "categories"
    protocol_root = project_root / "protocol"

    _activate_package(
        "defined_quant",
        shared_root / "__init__.py",
        (shared_root, categories_root),
    )
    _activate_package(
        "defined_quant_protocol",
        protocol_root / "__init__.py",
        (protocol_root,),
    )


__all__ = ["activate_source_runtime"]
