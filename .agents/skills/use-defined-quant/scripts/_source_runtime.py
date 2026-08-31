"""Activate this checkout's public packages for direct skill-script execution."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _resolve_checkout_root(runtime_file: str | Path = __file__) -> Path:
    """Resolve the exact checkout encoded by the canonical skill-script layout."""

    runtime = Path(runtime_file).resolve()
    try:
        project_root = runtime.parents[4]
    except IndexError:
        raise RuntimeError("source-runtime checkout layout is incomplete") from None
    expected_runtime = (
        project_root
        / ".agents"
        / "skills"
        / "use-defined-quant"
        / "scripts"
        / "_source_runtime.py"
    )
    required = (
        project_root / "shared" / "__init__.py",
        project_root / "categories",
        project_root / "protocol" / "__init__.py",
    )
    if runtime != expected_runtime.resolve() or any(not path.exists() for path in required):
        raise RuntimeError(
            f"source-runtime checkout layout mismatch: expected {project_root}; actual {runtime}"
        )
    return project_root


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


def _assert_package_checkout(
    package_name: str,
    initializer: Path,
    search_locations: tuple[Path, ...],
) -> None:
    """Refuse a mixed package whose import paths escape the intended checkout."""

    expected_initializer = initializer.resolve()
    expected_locations = tuple(path.resolve() for path in search_locations)
    expected_description = " or ".join(str(path) for path in expected_locations)
    loaded = sys.modules.get(package_name)
    loaded_file = getattr(loaded, "__file__", None)
    if not isinstance(loaded, ModuleType) or not isinstance(loaded_file, str):
        raise RuntimeError(
            f"{package_name} source-package activation path mismatch: "
            f"expected {expected_initializer}; actual <missing>"
        )

    actual_initializer = Path(loaded_file).resolve()
    if actual_initializer != expected_initializer:
        raise RuntimeError(
            f"{package_name} source-package activation path mismatch: "
            f"expected {expected_initializer}; actual {actual_initializer}"
        )

    def assert_inside(actual_path: str | Path) -> None:
        actual = Path(actual_path).resolve()
        if not any(actual == root or actual.is_relative_to(root) for root in expected_locations):
            raise RuntimeError(
                f"{package_name} source-package activation path mismatch: "
                f"expected {expected_description}; actual {actual}"
            )

    loaded_paths = getattr(loaded, "__path__", ())
    for loaded_path in loaded_paths:
        assert_inside(loaded_path)

    for module_name, module in tuple(sys.modules.items()):
        if not module_name.startswith(f"{package_name}."):
            continue
        module_file = getattr(module, "__file__", None)
        if isinstance(module_file, str):
            assert_inside(module_file)


def activate_source_runtime() -> None:
    """Prefer this checkout over stale installed Defined Quant packages."""

    project_root = _resolve_checkout_root()
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
    _assert_package_checkout(
        "defined_quant",
        shared_root / "__init__.py",
        (shared_root, categories_root),
    )
    _assert_package_checkout(
        "defined_quant_protocol",
        protocol_root / "__init__.py",
        (protocol_root,),
    )


__all__ = ["activate_source_runtime"]
