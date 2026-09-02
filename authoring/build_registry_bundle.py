#!/usr/bin/env python3
"""Compile authored registry YAML into one deterministic runtime JSON bundle."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml  # type: ignore[import-untyped]

_ENTITY_GLOBS = {
    "method": "methods/*/*/method.yaml",
    "capability": "capabilities/*/*/capability.yaml",
    "backend": "backends/**/*.yaml",
    "adapter": "adapters/**/*.yaml",
    "implementation": "implementations/**/*.yaml",
}
_EVIDENCE_GLOBS = (
    "evidence/**/*.yaml",
    "capabilities/*/*/conformance.yaml",
    "methods/*/*/evidence.yaml",
)


class RegistryLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Keep timestamp-looking scalar values as authored strings."""


RegistryLoader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag != "tag:yaml.org,2002:timestamp"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _mapping(path: Path) -> dict[str, Any]:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=RegistryLoader)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must contain one string-keyed object")
    json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return value


def _paths(paths: Iterable[Path], root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            set(paths),
            key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
        )
    )


def _entry(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "record": _mapping(path),
    }


def _bootstrap_source_package(name: str, paths: tuple[Path, ...]) -> ModuleType:
    """Expose a source-layout package during an isolated wheel build.

    Hatch maps ``shared/`` and ``protocol/`` to their installed package names only after the wheel
    is built. The registry validator needs those modules before packaging, so the build hook creates
    namespace package shells over the source directories without importing either public package
    initializer or depending on a previously installed wheel.
    """

    if name in sys.modules:
        return sys.modules[name]
    package = ModuleType(name)
    package.__package__ = name
    package.__path__ = [str(path) for path in paths]
    sys.modules[name] = package
    return package


def _validate_registry(root: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    protocol_package = _bootstrap_source_package(
        "defined_quant_protocol",
        (project_root / "protocol",),
    )
    from defined_quant_protocol.canonical import canonical_hash, canonical_json_bytes

    protocol_package.__dict__["canonical_hash"] = canonical_hash
    protocol_package.__dict__["canonical_json_bytes"] = canonical_json_bytes
    _bootstrap_source_package(
        "defined_quant",
        (project_root / "shared", project_root / "categories"),
    )
    from defined_quant.registry import load_registry

    load_registry(root=root)


def registry_bundle(root: Path) -> dict[str, Any]:
    """Validate and return authored records in a deterministic JSON envelope."""

    root = root.resolve()
    # The build must never package records that only happen to be valid YAML. The authoring/build
    # environment applies the same schema, identity, evidence, and cross-reference checks as the
    # service; the installed runtime still consumes inert JSON without PyYAML.
    _validate_registry(root)
    taxonomy = root / "taxonomy" / "categories.yaml"
    if not taxonomy.is_file():
        raise ValueError("registry taxonomy/categories.yaml is required")
    entities = {
        kind: [_entry(path, root) for path in _paths(root.glob(pattern), root)]
        for kind, pattern in _ENTITY_GLOBS.items()
    }
    evidence_paths = {
        path for pattern in _EVIDENCE_GLOBS for path in root.glob(pattern)
    }
    return {
        "schema_version": 1,
        "taxonomy": _entry(taxonomy, root),
        "entities": entities,
        "evidence": [
            _entry(path, root) for path in _paths(evidence_paths, root)
        ],
    }


def write_registry_bundle(value: dict[str, Any], output: Path) -> None:
    """Atomically write canonical, byte-stable compiled registry JSON."""

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("registry"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = registry_bundle(args.root)
    write_registry_bundle(value, args.output.resolve())
    print(f"Compiled registry metadata to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
