#!/usr/bin/env python3
"""Export deterministic, registry-only website Component pages.

This is the canonical methods-first website export. ``export_catalog.py`` remains a deprecated
migration-only exporter for the old five-file components. Delete that legacy exporter when all
seven native methods have migrated and the website consumes ``component-pages.json``; it must not
survive removal of the legacy component runtime.

The exporter reads inert registry metadata and Markdown only. It never imports adapter,
implementation, provider, or component calculation code, and it reports registered support rather
than installation-specific availability.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from defined_quant.registry import (
    EvidenceRecord,
    MethodInspection,
    Registry,
    RegistryRecord,
    inspect_method,
    load_registry,
)

_EXECUTABLE_ADAPTER_FIELDS = frozenset(
    {"callable", "dispatch", "dispatch_key", "entry_point", "import_path"}
)
_STATUS_LABELS = {
    "draft": "Experimental Technical Preview",
    "experimental": "Experimental Technical Preview",
    "stable": "Stable",
    "deprecated": "Deprecated",
}


def _project_root(override: Path | None) -> Path:
    return override.resolve() if override else Path(__file__).resolve().parents[1]


def _git_commit(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return None
    value = result.stdout.strip()
    return value or None


def _without_keys(value: Any, prohibited: frozenset[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_keys(item, prohibited)
            for key, item in value.items()
            if str(key) not in prohibited
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_without_keys(item, prohibited) for item in value]
    return value


def _record(record: RegistryRecord) -> dict[str, Any]:
    value = record.as_dict()
    value["record_hash"] = record.record_hash
    for name, item in record.ref.items():
        if name not in {"id", "version"}:
            value[name] = item
    return value


def _evidence(record: EvidenceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "version": record.version,
        "evidence_hash": record.evidence_hash,
        "subject": {
            "kind": record.subject_kind,
            "id": record.subject_id,
            "version": record.subject_version,
        },
        "record": record.as_dict(),
    }


def _evidence_groups(inspection: MethodInspection) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "method": [],
        "capability_conformance": [],
        "implementation": [],
        "adapter": [],
        "backend": [],
    }
    for record in inspection.evidence:
        name = (
            "capability_conformance"
            if record.subject_kind == "capability"
            else record.subject_kind
        )
        groups[name].append(_evidence(record))
    return groups


def _method_readme(method: RegistryRecord) -> str | None:
    path = method.source_path.parent / "README.md"
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8")
    return value if value.strip() else None


def component_page(registry: Registry, method: RegistryRecord) -> dict[str, Any]:
    """Build one deterministic website-safe Component page from registry metadata."""

    inspection = inspect_method(
        method.id,
        version=method.version,
        registry=registry,
    )
    method_record = _record(method)
    discovery = method_record.get("discovery", {})
    if not isinstance(discovery, Mapping):
        raise ValueError(f"method {method.id} discovery must be an object")
    website_slug = discovery.get("website_slug")
    if not isinstance(website_slug, str):
        raise ValueError(f"method {method.id} has no website slug")

    backends = {item.key: item for item in inspection.backends}
    adapters = {item.key: item for item in inspection.adapters}
    implementations: list[dict[str, Any]] = []
    for implementation in inspection.implementations:
        value = _record(implementation)
        adapter_ref = value.get("adapter")
        adapter_key = _reference_key(adapter_ref, name="implementation adapter")
        adapter = adapters[adapter_key]
        backend_bindings = value.get("backend_bindings")
        if not isinstance(backend_bindings, Sequence) or isinstance(
            backend_bindings, str | bytes | bytearray
        ):
            raise ValueError("implementation backend_bindings must be an array")
        joined_backends: list[dict[str, Any]] = []
        for binding in backend_bindings:
            if not isinstance(binding, Mapping):
                raise ValueError("implementation backend binding must be an object")
            backend_key = _reference_key(
                binding.get("backend"),
                name="implementation backend",
            )
            joined_backends.append(
                {"role": binding.get("role"), "backend": _record(backends[backend_key])}
            )
        implementations.append(
            {
                "implementation": value,
                "backends": joined_backends,
                "adapter": _without_keys(
                    _record(adapter),
                    _EXECUTABLE_ADAPTER_FIELDS,
                ),
                "support_status": "registered",
                "runtime_availability": "installation_specific_not_exported",
            }
        )

    evidence = _evidence_groups(inspection)
    page: dict[str, Any] = {
        "id": method.id,
        "version": method.version,
        "slug": website_slug,
        "url": f"/components/{website_slug}",
        "title": method_record["title"],
        "category": method_record["category"],
        "summary": method_record["summary"],
        "lifecycle": method_record["lifecycle"],
        "status_label": _STATUS_LABELS[str(method_record["lifecycle"])],
        "description_markdown": _method_readme(method),
        "method": method_record,
        "recipe": method_record["recipe"],
        "capabilities": [_record(item) for item in inspection.capabilities],
        "registered_implementations": implementations,
        "evidence": evidence,
        "trust_boundaries": {
            "method_correctness": "method_evidence_only",
            "capability_conformance": "capability_conformance_only",
            "implementation_evidence": "implementation_specific_only",
            "adapter_review": "adapter_specific_only",
            "provider_authentication": "runtime_not_exported",
            "dataset_provenance": "run_specific_not_exported",
            "execution_integrity": "run_specific_not_exported",
            "independent_domain_review": "separate_evidence_claim",
            "independent_reproduction": "separate_evidence_claim",
        },
    }
    return page


def _reference_key(value: Any, *, name: str) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an exact reference")
    identifier = value.get("id")
    version = value.get("version")
    if not isinstance(identifier, str) or not isinstance(version, str):
        raise ValueError(f"{name} must contain string id and version")
    return (identifier, version)


def component_pages(
    registry: Registry,
    *,
    commit_sha: str,
    release_version: str = "development",
    release_label: str = "Development component pages",
) -> dict[str, Any]:
    """Build the complete canonical Component-page artifact."""

    return {
        "schema_version": 1,
        "artifact_kind": "defined_quant_component_pages",
        "release": {"version": release_version, "label": release_label},
        "source": {
            "commit_sha": commit_sha,
            "registry_hash": registry.registry_hash,
        },
        "taxonomy": _without_keys(registry.taxonomy, frozenset()),
        "component_pages": [component_page(registry, item) for item in registry.methods],
    }


def export_component_pages(value: Mapping[str, Any], output: Path) -> None:
    """Atomically publish canonical, byte-stable JSON."""

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-sha")
    parser.add_argument("--release-version", default="development")
    parser.add_argument("--release-label", default="Development component pages")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = _project_root(args.root)
    commit_sha = args.commit_sha or os.environ.get("GITHUB_SHA") or _git_commit(root)
    if not commit_sha:
        parser.error("cannot determine source commit; pass --commit-sha outside a Git checkout")
    registry = load_registry(root=root / "registry")
    value = component_pages(
        registry,
        commit_sha=commit_sha,
        release_version=args.release_version,
        release_label=args.release_label,
    )
    output = (
        args.output.resolve()
        if args.output
        else root / "dist" / "catalog" / "component-pages.json"
    )
    export_component_pages(value, output)
    print(f"Exported {len(registry.methods)} component page(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
