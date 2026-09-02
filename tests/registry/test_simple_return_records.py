from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from defined_quant_protocol import canonical_hash
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "registry"
LEGACY_SUBJECT_HASH = "ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a"
ARTIFACT_HASH = "134285caaa1af8f3755721defa30efda12bdf556e26d3ed5477efdfee869a48c"


def _load(relative: str) -> dict[str, Any]:
    value = yaml.safe_load((REGISTRY / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_simple_return_registry_references_are_exact_and_provider_neutral() -> None:
    method = _load("methods/market_data/simple_return/method.yaml")
    capability = _load("capabilities/returns/simple/capability.yaml")
    backend = _load("backends/dq_native.yaml")
    adapter = _load("adapters/dq_native/simple_return.yaml")
    implementation = _load("implementations/dq_native/simple_return.yaml")

    assert method["id"] == "dq.market_data.simple_return"
    assert capability["id"] == "returns.simple"
    assert method["recipe"]["steps"][0]["capability"] == {
        "id": capability["id"],
        "version": capability["version"],
    }
    assert adapter["backend"] == {"id": backend["id"], "version": backend["version"]}
    assert implementation["capability"] == {
        "id": capability["id"],
        "version": capability["version"],
    }
    assert implementation["backend_bindings"] == [
        {
            "role": "runtime",
            "backend": adapter["backend"],
            "transport": "in_process",
            "locality": "local",
        }
    ]
    assert implementation["adapter"] == {
        "id": adapter["id"],
        "version": adapter["version"],
    }
    assert adapter["dispatch_key"] == "dq_native_simple_return"
    assert adapter["transports"] == ["in_process"]
    assert implementation["support_level"] == "full"
    assert implementation["restrictions"] == []

    recipe_text = yaml.safe_dump(method["recipe"], sort_keys=True).lower()
    assert all(word not in recipe_text for word in ("backend", "adapter", "provider", "dq_native"))


def test_method_defaults_have_one_authored_authority() -> None:
    method = _load("methods/market_data/simple_return/method.yaml")
    capability = _load("capabilities/returns/simple/capability.yaml")

    assert method["defaults"] == [
        {
            "field": "declared_frequency",
            "value": None,
            "rationale": "Frequency is never inferred.",
        },
        {
            "field": "timestamps",
            "value": None,
            "rationale": "Timestamps are optional; absence is disclosed as unverified chronology.",
        },
    ]
    assert "default" not in yaml.safe_dump(method["input_schema"])
    assert "default" not in yaml.safe_dump(capability["input_schema"])


def test_method_reuses_capability_fields_without_schema_copies() -> None:
    method = _load("methods/market_data/simple_return/method.yaml")
    directive = "x-defined-quant-capability-field"
    expected = {
        "input": {"prices", "timestamps"},
        "output": {
            "ordering_status",
            "return_kind",
            "return_timestamps",
            "returns",
        },
    }

    for direction, fields in expected.items():
        properties = method[f"{direction}_schema"]["properties"]
        for field in fields:
            assert set(properties[field]) == {directive}
            binding = properties[field][directive]
            assert binding == {
                "capability": {"id": "returns.simple", "version": "1.0.0"},
                "direction": direction,
                "field": field,
            }


def test_protocol_native_authored_shapes_are_used() -> None:
    method = _load("methods/market_data/simple_return/method.yaml")
    capability = _load("capabilities/returns/simple/capability.yaml")
    implementation = _load("implementations/dq_native/simple_return.yaml")

    assert method["schema_version"] == 2
    assert capability["schema_version"] == 2
    assert implementation["schema_version"] == 2
    assert method["lifecycle"] == "draft"
    assert method["recipe"]["steps"][0]["step_id"] == "calculate"
    for record in (method, capability):
        for constraint in record["constraints"]:
            assert "target" in constraint
            assert "expression" in constraint
            assert "when" not in constraint
            assert "applies_to" not in constraint


def test_installed_manifest_exactly_binds_adapter_distribution_resources() -> None:
    manifest_path = REGISTRY / "artifacts/defined_quant_adapter_dq_native/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    members = manifest["members"]

    assert [member["path"] for member in members] == sorted(member["path"] for member in members)
    assert [item["name"] for item in manifest["entry_points"]] == [
        "dq_native_drawdown",
        "dq_native_log_return",
        "dq_native_monthly_calendar_matrix",
        "dq_native_rebased_price_index",
        "dq_native_rolling_sample_standard_deviation",
        "dq_native_sample_standard_deviation",
        "dq_native_simple_return",
        "dq_native_square_root_annualize",
        "dq_native_square_root_annualize_series",
    ]
    assert all(member["path"].startswith("defined_quant_adapter_dq_native/") for member in members)
    assert all("/src/" not in member["path"] for member in members)
    assert all(not member["path"].endswith(("README.md", "pyproject.toml")) for member in members)
    assert any(member["path"].endswith("/adapter.py") for member in members)
    assert any(member["path"].endswith("/kernel.py") for member in members)
    for member in members:
        content = (ROOT / "adapters/dq_native/src" / member["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == member["sha256"]

    assert (
        canonical_hash(
            manifest,
            domain="registry.artifact.installed_distribution_manifest",
        )
        == ARTIFACT_HASH
    )
    adapter = _load("adapters/dq_native/simple_return.yaml")
    implementation = _load("implementations/dq_native/simple_return.yaml")
    assert "artifact_requirement" not in adapter
    assert implementation["artifact"] == {
        "distribution": "defined-quant-adapter-dq-native",
        "version": "1.0.0",
        "artifact_hash": ARTIFACT_HASH,
    }


def test_authored_json_schemas_are_valid_and_closed() -> None:
    method = _load("methods/market_data/simple_return/method.yaml")
    capability = _load("capabilities/returns/simple/capability.yaml")

    for schema in (
        method["input_schema"],
        method["output_schema"],
        capability["input_schema"],
        capability["output_schema"],
    ):
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


def test_legacy_subject_hash_is_migration_provenance_only() -> None:
    occurrences = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(REGISTRY.rglob("*.yaml"))
        if LEGACY_SUBJECT_HASH in path.read_text(encoding="utf-8")
    ]

    assert occurrences == ["registry/evidence/implementations/dq_native/simple_return.yaml"]


def test_evidence_subjects_match_their_registry_records() -> None:
    cases = (
        (
            "evidence/methods/market_data/simple_return.yaml",
            "method",
            "dq.market_data.simple_return",
        ),
        (
            "evidence/adapters/dq_native/simple_return.yaml",
            "adapter",
            "dq_native.simple_return",
        ),
        (
            "evidence/implementations/dq_native/simple_return.yaml",
            "implementation",
            "dq_native.simple_return",
        ),
    )
    for relative, kind, identifier in cases:
        evidence = _load(relative)
        assert evidence["subject"] == {
            "kind": kind,
            "id": identifier,
            "version": "1.0.0",
        }
        assert evidence["status"]["label"] == "Experimental Technical Preview"
        assert evidence["non_claims"]
