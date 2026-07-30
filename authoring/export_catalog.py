#!/usr/bin/env python3
"""Export the component catalog as deterministic, website-safe static JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from defined_quant import component_models, operation_protocol_schema
from defined_quant.catalog import iter_components
from defined_quant.discovery import catalog_facets

EVIDENCE_SECTIONS = ("known_answers", "invariants", "boundary_cases", "cross_checks")


class ContractLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Load dates as strings for stable JSON output."""


ContractLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _project_root(override: Path | None) -> Path:
    return override.resolve() if override else Path(__file__).resolve().parents[1]


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = yaml.load(text, Loader=ContractLoader)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one mapping")
    return value


def _category_metadata(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path} has no YAML front matter")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError(f"{path} has unclosed YAML front matter") from exc
    value = yaml.load("\n".join(lines[1:closing]), Loader=ContractLoader)
    if not isinstance(value, dict):
        raise ValueError(f"{path} front matter must be a mapping")
    result: dict[str, str] = {}
    for field in ("id", "title", "summary"):
        item = value.get(field)
        if not isinstance(item, str):
            raise ValueError(f"{path} front matter field {field!r} must be a string")
        result[field] = item
    return result


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def _evidence_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    counts = {
        section: len(value) if isinstance(value, list) else 0
        for section in EVIDENCE_SECTIONS
        for value in [evidence.get(section)]
    }
    numerical_records = sum(counts.values())
    binding = evidence.get("validated_subject_hash")
    subject = binding.removeprefix("sha256:") if isinstance(binding, str) else None
    return {
        # A source export cannot truthfully self-award an exact-commit CI result.
        "engineering": "unverified",
        "numerical": "author_supplied" if numerical_records else "none",
        "exact_sha_attestation": "none",
        "domain_review": "none",
        "subject_hash": subject,
        "evidence_hash": _canonical_hash(evidence),
    }


def _component_record(component_dir: Path) -> dict[str, Any]:
    contract = _load_mapping(component_dir / "contract.yaml")
    evidence = _load_mapping(component_dir / "evidence.yaml")
    category = _category_metadata(component_dir.parent / "README.md")
    guidance = contract.get("guidance")
    if not isinstance(guidance, Mapping):
        raise ValueError(f"{component_dir / 'contract.yaml'} guidance must be an object")
    template = contract.get("template")
    if not isinstance(template, Mapping):
        raise ValueError(f"{component_dir / 'contract.yaml'} template must be an object")
    display = contract.get("display")
    if not isinstance(display, Mapping):
        raise ValueError(f"{component_dir / 'contract.yaml'} display must be an object")
    discovery = contract.get("discovery")
    if not isinstance(discovery, Mapping):
        raise ValueError(f"{component_dir / 'contract.yaml'} discovery must be an object")
    inputs_model, output_model = component_models(component_dir)

    return {
        "id": contract["id"],
        "category": category["id"],
        "group": contract["group"],
        "slug": contract["slug"],
        "title": contract["title"],
        "version": contract["version"],
        "lifecycle": contract["lifecycle"],
        "profile": template["profile"],
        "summary": contract["summary"],
        "tags": contract["tags"],
        "discovery": {
            "aliases": discovery["aliases"],
            "intents": discovery["intents"],
            "input_concepts": discovery["input_concepts"],
            "output_concepts": discovery["output_concepts"],
        },
        "use_when": guidance["use_when"],
        "do_not_use_when": guidance["do_not_use_when"],
        "formula": display["formula"],
        "intent": display["intent"],
        "output": display["output"],
        "required_questions": guidance.get("required_questions", []),
        "assumptions": contract.get("assumptions", []),
        "limitations": contract.get("limitations", []),
        "unsupported": guidance.get("unsupported_scope", []),
        "callable": contract["callable"],
        "schemas": {
            "input": inputs_model.model_json_schema(),
            "output": output_model.model_json_schema(),
        },
        "evidence": _evidence_summary(evidence),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-sha")
    parser.add_argument("--release-version", default="development")
    parser.add_argument("--release-label", default="Development catalog")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = _project_root(args.root)
    checker = root / "authoring" / "check_component.py"
    checked = subprocess.run(
        [sys.executable, str(checker), "--root", str(root)],
        cwd=root,
        check=False,
    )
    if checked.returncode:
        print("ERROR: catalog export stopped because validation failed", file=sys.stderr)
        return checked.returncode

    commit_sha = args.commit_sha or os.environ.get("GITHUB_SHA") or _git_commit(root)
    if not commit_sha:
        parser.error("cannot determine source commit; pass --commit-sha outside a Git checkout")
    source: dict[str, str] = {"commit_sha": commit_sha}

    try:
        category_records = [
            _category_metadata(path) for path in sorted((root / "categories").glob("*/README.md"))
        ]
        records = [
            _component_record(contract.parent)
            for contract in sorted((root / "categories").glob("*/*/contract.yaml"))
        ]
        facet_counts = catalog_facets(iter_components(root=root))
    except (KeyError, OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: cannot build catalog: {exc}", file=sys.stderr)
        return 1
    records.sort(key=lambda item: item["id"])
    catalog = {
        "schema_version": 2,
        "artifact_kind": "defined_quant_catalog",
        "agent_protocol": operation_protocol_schema(),
        "release": {
            "version": args.release_version,
            "label": args.release_label,
        },
        "source": source,
        "facets": {
            name: [item.value for item in values]
            for name, values in facet_counts.items()
        },
        "categories": category_records,
        "components": records,
    }

    output = args.output.resolve() if args.output else root / "dist" / "catalog" / "catalog.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(f"Exported {len(records)} component(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
