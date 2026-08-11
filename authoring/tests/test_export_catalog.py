"""Tests for the stable catalog artifact consumed outside the Python project."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from defined_quant.market_data.simple_return.component import FORMULA
from defined_quant_protocol import (
    PORT_SCHEMA_KEY,
    PortConvention,
    operation_protocol_schema,
)

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
    assert record["formula"] == FORMULA
    assert record["schemas"]["input"]["type"] == "object"
    assert record["schemas"]["output"]["type"] == "object"
    assert "prices" in record["schemas"]["input"]["properties"]
    assert "derivations" in record["schemas"]["output"]["properties"]
    assert "disclosures" in record["schemas"]["output"]["properties"]
    assert "derivations" in record["schemas"]["output"]["required"]
    assert "Derivation" in record["schemas"]["output"]["$defs"]
    assert record["schemas"]["input"]["properties"]["prices"][PORT_SCHEMA_KEY][
        "direction"
    ] == "input"
    assert record["schemas"]["output"]["properties"]["returns"][PORT_SCHEMA_KEY][
        "direction"
    ] == "output"

    volatility = export_catalog._component_record(
        root / "categories" / "volatility" / "historical_volatility"
    )
    assert volatility["schemas"]["output"]["properties"]["annualized_volatility"][
        PORT_SCHEMA_KEY
    ]["convention"] == (
        PortConvention.SAMPLE_STANDARD_DEVIATION_N_MINUS_1_SQUARE_ROOT_ANNUALIZATION.value
    )


def test_catalog_v2_has_deterministic_schemas_and_top_level_facets(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "catalog.json"
    second_output = tmp_path / "catalog-again.json"

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
        "categories",
        "components",
        "facets",
        "operation_protocol",
        "release",
        "schema_version",
        "source",
    }
    assert artifact["schema_version"] == 2
    assert [component["id"] for component in artifact["components"]] == [
        "dq.market_data.log_return",
        "dq.market_data.simple_return",
        "dq.volatility.historical_volatility",
    ]
    assert [category["id"] for category in artifact["categories"]] == [
        "market_data",
        "volatility",
    ]
    protocol = artifact["operation_protocol"]
    assert protocol == operation_protocol_schema()
    assert protocol["package"] == "defined_quant_protocol"
    assert protocol["execution_mode"] == "unmanaged"
    assert protocol["protocol_version"]
    assert protocol["canonicalization_id"] == "dq-tagged-json-v1"
    assert protocol["operation_request_domain"] == "operation.request.v1"
    assert set(protocol["schemas"]) == {
        "failure",
        "manifest",
        "request",
        "result",
        "success",
    }
    assert protocol["schemas"]["request"]["type"] == "object"
    assert protocol["schemas"]["manifest"]["type"] == "object"
    assert protocol["schemas"]["failure"]["type"] == "object"
    assert protocol["schemas"]["result"]["discriminator"]["propertyName"] == "status"
    assert len(protocol["schemas"]["result"]["oneOf"]) == 2
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

    serialized = output.read_text(encoding="utf-8")
    assert str(root) not in serialized
    assert "file://" not in serialized

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
            str(second_output),
        ],
    )
    assert export_catalog.main() == 0
    assert second_output.read_bytes() == output.read_bytes()
