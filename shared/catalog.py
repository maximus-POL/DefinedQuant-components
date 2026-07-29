"""Filesystem discovery, safe component loading, and deterministic subject binding."""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from defined_quant.types.errors import (
    ComponentContractError,
    ComponentLoadError,
    ComponentNotFound,
)
from pydantic import BaseModel

CANONICALIZATION_VERSION = 2
CONTRACT_SCHEMA_VERSION = 1
PACKAGE_ROOT = Path(__file__).resolve().parent

_BEHAVIOUR_COMPONENT_FIELDS = (
    "id",
    "slug",
    "title",
    "category",
    "group",
    "version",
    "lifecycle",
    "callable",
    "template",
    "summary",
    "tags",
    "discovery",
    "assumptions",
    "limitations",
    "depends_on",
    "supported_python",
)
_SEMANTIC_DISPLAY_FIELDS = ("formula", "intent", "output")


@dataclass(frozen=True, slots=True)
class ComponentRecord:
    """A discovered component contract without importing its Python module."""

    component_id: str
    category: str
    slug: str
    version: str
    callable_path: str
    path: Path
    metadata: Mapping[str, Any]


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ComponentContractError("contract data cannot contain non-finite numbers")
        return value
    if isinstance(value, Enum):
        return _json_compatible(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, BaseModel):
        return _json_compatible(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible(asdict(value))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ComponentContractError("contract object keys must be strings")
            result[key] = _json_compatible(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    raise ComponentContractError(
        f"contract value of type {type(value).__name__} is not JSON-compatible"
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _json_compatible(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _read_mapping(path: Path) -> dict[str, Any]:
    """Read a JSON-compatible YAML contract.

    JSON is valid YAML and is the dependency-free installed-package format.  PyYAML is an
    optional development dependency used only as a fallback for conventional YAML syntax.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ComponentContractError(f"cannot read required contract: {path}") from exc

    try:
        loaded: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ComponentContractError(
                f"{path} is not JSON-compatible YAML; install the development 'pyyaml' "
                "dependency to read conventional YAML syntax"
            ) from exc
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ComponentContractError(f"malformed YAML contract: {path}") from exc

    if not isinstance(loaded, dict) or any(not isinstance(key, str) for key in loaded):
        raise ComponentContractError(f"contract must contain one object with string keys: {path}")
    return dict(loaded)


def _record_from_contract(contract_path: Path) -> ComponentRecord:
    metadata = _read_mapping(contract_path)
    category_path = contract_path.parent.parent
    component_path = contract_path.parent
    category = category_path.name
    slug = component_path.name
    component_id = metadata.get("id")
    declared_category = metadata.get("category")
    declared_slug = metadata.get("slug")
    version = metadata.get("version")
    callable_path = metadata.get("callable")

    expected_id = f"dq.{category}.{slug}"
    if component_id != expected_id:
        raise ComponentContractError(
            f"component id must be {expected_id!r}, got {component_id!r}",
            component_id=component_id if isinstance(component_id, str) else None,
        )
    if declared_category != category:
        raise ComponentContractError(
            f"component category must match directory {category!r}",
            component_id=component_id,
        )
    if declared_slug != slug:
        raise ComponentContractError(
            f"component slug must match directory {slug!r}",
            component_id=component_id,
        )
    if not isinstance(version, str):
        raise ComponentContractError(
            "component version must be a string",
            component_id=component_id,
        )
    if not isinstance(callable_path, str) or callable_path.count(":") != 1:
        raise ComponentContractError(
            "component callable must use 'module:attribute' syntax",
            component_id=component_id,
        )
    expected_module = f"defined_quant.{category}.{slug}"
    module_name, _, _ = callable_path.partition(":")
    if module_name != expected_module and not module_name.startswith(expected_module + "."):
        raise ComponentContractError(
            f"component callable must be inside {expected_module}",
            component_id=component_id,
        )
    return ComponentRecord(
        component_id=component_id,
        category=category,
        slug=slug,
        version=version,
        callable_path=callable_path,
        path=component_path,
        metadata=metadata,
    )


def _catalog_root(root: str | Path | None = None) -> Path:
    """Resolve either the source checkout or the merged installed-package catalog.

    In a checkout, shared runtime files and category folders are deliberately separate for
    readability.  The wheel merges both into ``defined_quant``.  Accepting the project root,
    the ``categories`` directory, or the installed package root keeps discovery predictable in
    all three contexts.
    """

    if root is not None:
        candidate = Path(root).resolve()
        nested_categories = candidate / "categories"
        return nested_categories if nested_categories.is_dir() else candidate

    source_categories = PACKAGE_ROOT.parent / "categories"
    return source_categories if source_categories.is_dir() else PACKAGE_ROOT


def iter_components(*, root: str | Path | None = None) -> Iterator[ComponentRecord]:
    """Yield installed components in stable category/slug order without importing them."""

    catalog_root = _catalog_root(root)
    seen: set[str] = set()
    for contract_path in sorted(catalog_root.glob("*/*/contract.yaml")):
        relative = contract_path.relative_to(catalog_root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        record = _record_from_contract(contract_path)
        if record.component_id in seen:
            raise ComponentContractError(
                f"duplicate component id: {record.component_id}",
                component_id=record.component_id,
            )
        seen.add(record.component_id)
        yield record


def _resolve_record(
    identifier: str | Path | ComponentRecord,
    *,
    root: str | Path | None = None,
) -> ComponentRecord:
    if isinstance(identifier, ComponentRecord):
        return identifier
    candidate = Path(identifier)
    if isinstance(identifier, Path) or candidate.exists():
        path = candidate.resolve()
        if path.is_file():
            path = path.parent
        contract_path = path / "contract.yaml"
        if not contract_path.is_file():
            raise ComponentNotFound(f"no contract.yaml under {path}")
        return _record_from_contract(contract_path)

    component_id = str(identifier)
    for record in iter_components(root=root):
        if record.component_id == component_id:
            return record
    raise ComponentNotFound(
        f"component is not installed: {component_id}",
        component_id=component_id,
    )


def component_record(
    identifier: str | Path | ComponentRecord,
    *,
    root: str | Path | None = None,
) -> ComponentRecord:
    """Resolve a stable ID or component directory without importing component code."""

    return _resolve_record(identifier, root=root)


def load_component(
    identifier: str | Path | ComponentRecord,
    *,
    root: str | Path | None = None,
) -> Callable[..., Any]:
    """Import and return a discovered component callable."""

    record = _resolve_record(identifier, root=root)
    module_name, attribute = record.callable_path.split(":", maxsplit=1)
    try:
        module = importlib.import_module(module_name)
        component = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise ComponentLoadError(
            f"cannot load callable {record.callable_path}",
            component_id=record.component_id,
        ) from exc
    if not callable(component):
        raise ComponentLoadError(
            f"declared component attribute is not callable: {record.callable_path}",
            component_id=record.component_id,
        )
    return cast(Callable[..., Any], component)


def _component_models(record: ComponentRecord) -> tuple[type[BaseModel], type[BaseModel]]:
    component = load_component(record)
    implementation_module = inspect.getmodule(component)
    if implementation_module is None:
        raise ComponentLoadError(
            "cannot inspect component implementation module",
            component_id=record.component_id,
        )
    inputs = getattr(implementation_module, "Inputs", None)
    output = getattr(implementation_module, "Output", None)
    if (
        not isinstance(inputs, type)
        or not issubclass(inputs, BaseModel)
        or not isinstance(output, type)
        or not issubclass(output, BaseModel)
    ):
        raise ComponentContractError(
            "component implementation must expose Pydantic Inputs and Output models",
            component_id=record.component_id,
        )
    return inputs, output


def component_models(
    identifier: str | Path | ComponentRecord,
    *,
    root: str | Path | None = None,
) -> tuple[type[BaseModel], type[BaseModel]]:
    """Return the canonical input and output models for a component."""

    return _component_models(_resolve_record(identifier, root=root))


def _normalised_text_hash(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ComponentContractError(f"cannot hash UTF-8 source file: {path}") from exc
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _behaviour_files(record: ComponentRecord) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(record.path.rglob("*.py")):
        relative = path.relative_to(record.path)
        if (
            path.name.startswith("test_")
            or "examples" in relative.parts
            or "__pycache__" in relative.parts
        ):
            continue
        files[relative.as_posix()] = _normalised_text_hash(path)
    if not files:
        raise ComponentContractError(
            "component has no behaviour-defining Python files",
            component_id=record.component_id,
        )
    return files


def _component_imports_rendering(record: ComponentRecord) -> bool:
    for path in sorted(record.path.rglob("*.py")):
        if path.name.startswith("test_") or "examples" in path.relative_to(record.path).parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ComponentContractError(
                f"cannot inspect imports in {path}",
                component_id=record.component_id,
            ) from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "defined_quant.charts" for alias in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom) and node.module == "defined_quant.charts":
                return True
    return False


def _shared_runtime_hashes(record: ComponentRecord) -> dict[str, str]:
    paths = [
        PACKAGE_ROOT / "validation.py",
        *sorted((PACKAGE_ROOT / "types").glob("*.py")),
    ]
    if _component_imports_rendering(record):
        paths.append(PACKAGE_ROOT / "charts.py")
    hashes: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            raise ComponentContractError(
                f"required shared runtime module is missing: {path}",
                component_id=record.component_id,
            )
        hashes[path.relative_to(PACKAGE_ROOT).as_posix()] = _normalised_text_hash(path)
    return hashes


def _public_symbols(record: ComponentRecord) -> list[str]:
    """Return the small public surface guaranteed by every component module."""

    _, attribute = record.callable_path.split(":", maxsplit=1)
    symbols = ["Inputs", "Output", attribute]
    if len(set(symbols)) != len(symbols):
        raise ComponentContractError(
            "component callable may not be named Inputs or Output",
            component_id=record.component_id,
        )
    return sorted(symbols)


def _subject_manifest(record: ComponentRecord, stack: tuple[str, ...]) -> dict[str, Any]:
    if record.component_id in stack:
        cycle = " -> ".join((*stack, record.component_id))
        raise ComponentContractError(
            f"component dependency cycle: {cycle}",
            component_id=record.component_id,
        )
    current_stack = (*stack, record.component_id)
    guidance = record.metadata.get("guidance")
    if not isinstance(guidance, Mapping):
        raise ComponentContractError(
            "contract guidance must be an object",
            component_id=record.component_id,
        )
    display = record.metadata.get("display")
    if not isinstance(display, Mapping):
        raise ComponentContractError(
            "contract display must be an object",
            component_id=record.component_id,
        )
    inputs, output = _component_models(record)

    dependencies = record.metadata.get("depends_on", [])
    if not isinstance(dependencies, list) or any(
        not isinstance(dependency, str) for dependency in dependencies
    ):
        raise ComponentContractError(
            "depends_on must be a list of dependency specifications",
            component_id=record.component_id,
        )
    component_dependencies: dict[str, str] = {}
    external_dependencies: list[str] = []
    for dependency in dependencies:
        if dependency.startswith("dq."):
            dependency_record = _resolve_record(dependency)
            dependency_manifest = _subject_manifest(dependency_record, current_stack)
            component_dependencies[dependency] = hashlib.sha256(
                _canonical_json(dependency_manifest)
            ).hexdigest()
        else:
            external_dependencies.append(dependency)

    component_projection = {
        field: record.metadata.get(field) for field in _BEHAVIOUR_COMPONENT_FIELDS
    }
    semantic_display = {
        field: display.get(field) for field in _SEMANTIC_DISPLAY_FIELDS
    }
    return {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "behaviour_files": _behaviour_files(record),
        "component": component_projection,
        "guidance": guidance,
        "semantic_display": semantic_display,
        "schemas": {
            "inputs": inputs.model_json_schema(),
            "output": output.model_json_schema(),
        },
        "public_symbols": _public_symbols(record),
        "shared_runtime": _shared_runtime_hashes(record),
        "component_dependencies": component_dependencies,
        "external_dependency_specifications": sorted(external_dependencies),
    }


def subject_manifest(
    identifier: str | Path | ComponentRecord,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the canonical behaviour-and-contract manifest used by ``subject_hash``."""

    record = _resolve_record(identifier, root=root)
    return _subject_manifest(record, ())


def subject_hash(
    identifier: str | Path | ComponentRecord,
    *,
    root: str | Path | None = None,
) -> str:
    """Return the SHA-256 identity of component behaviour and enforceable contract."""

    return hashlib.sha256(_canonical_json(subject_manifest(identifier, root=root))).hexdigest()


__all__ = [
    "CANONICALIZATION_VERSION",
    "CONTRACT_SCHEMA_VERSION",
    "ComponentRecord",
    "component_models",
    "component_record",
    "iter_components",
    "load_component",
    "subject_hash",
    "subject_manifest",
]
