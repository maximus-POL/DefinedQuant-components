"""Tests for the stable catalog artifact consumed outside the Python project."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from authoring import export_catalog

COMPONENT_KEYS = {
    "assumptions",
    "callable",
    "category",
    "discovery",
    "do_not_use_when",
    "evidence",
    "formula",
    "group",
    "id",
    "intent",
    "lifecycle",
    "limitations",
    "output",
    "profile",
    "required_questions",
    "schemas",
    "slug",
    "summary",
    "tags",
    "title",
    "unsupported",
    "use_when",
    "version",
}


def test_component_export_shape_contains_agent_routing_and_boundaries() -> None:
    root = Path(__file__).resolve().parents[2]
    record = export_catalog._component_record(
        root / "categories" / "market_data" / "simple_return"
    )

    assert set(record) == COMPONENT_KEYS
    assert set(record["discovery"]) == {
        "aliases",
        "intents",
        "input_concepts",
        "output_concepts",
    }
    assert record["use_when"]
    assert record["do_not_use_when"]
    assert record["limitations"]
    assert record["unsupported"]
    assert record["schemas"]["input"]["type"] == "object"
    assert record["schemas"]["output"]["type"] == "object"
    assert "prices" in record["schemas"]["input"]["properties"]


def test_catalog_v1_has_deterministic_top_level_facet_values(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "catalog.json"

    def successful_check(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(export_catalog.subprocess, "run", successful_check)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        sys,
        "argv",
        [
            "export_catalog.py",
            "--root",
            str(root),
            "--commit-sha",
            "a" * 40,
            "--output",
            str(output),
        ],
    )

    assert export_catalog.main() == 0
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert set(artifact) == {
        "artifact_kind",
        "agent_protocol",
        "categories",
        "components",
        "facets",
        "release",
        "schema_version",
        "source",
    }
    assert artifact["schema_version"] == 2
    assert artifact["agent_protocol"]["name"] == "defined_quant_operation"
    assert artifact["agent_protocol"]["request_schema"]["type"] == "object"
    assert artifact["agent_protocol"]["manifest_schema"]["type"] == "object"
    assert list(artifact["facets"]) == sorted(artifact["facets"])
    assert set(artifact["facets"]) == {
        "categories",
        "groups",
        "input_concepts",
        "intents",
        "lifecycles",
        "output_concepts",
        "profiles",
        "tags",
    }
    for values in artifact["facets"].values():
        assert values == sorted(set(values))
