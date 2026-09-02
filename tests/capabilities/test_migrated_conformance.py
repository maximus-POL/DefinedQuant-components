from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from defined_quant.registry import load_registry
from defined_quant.schema_validation import schema_is_supported, schema_matches
from defined_quant_protocol import canonical_hash

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = ROOT / "registry"
MIGRATED_CAPABILITIES = {
    "performance.drawdown",
    "prices.rebase",
    "returns.log",
    "returns.monthly_calendar_matrix",
    "statistics.rolling_sample_standard_deviation",
    "statistics.sample_standard_deviation",
    "statistics.square_root_annualize",
    "statistics.square_root_annualize_series",
}


def _materialize(value: Any) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$binary64"}:
            encoded = value["$binary64"]
            assert isinstance(encoded, str)
            return float.fromhex(encoded)
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def _load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_migrated_capability_schemas_and_success_cases_are_canonical() -> None:
    registry = load_registry(root=REGISTRY_ROOT).as_protocol_registry()
    capabilities = {item.id: item for item in registry.capabilities}
    assert MIGRATED_CAPABILITIES <= set(capabilities)

    for capability_id in MIGRATED_CAPABILITIES:
        capability = capabilities[capability_id]
        assert schema_is_supported(capability.input_schema)
        assert schema_is_supported(capability.output_schema)
        conformance_path = next(
            path
            for path in REGISTRY_ROOT.glob("capabilities/*/*/conformance.yaml")
            if _load(path)["capability"]["id"] == capability_id
        )
        conformance = _load(conformance_path)
        canonical_hash(conformance, domain="registry.evidence.content")
        assert conformance["invariants"]
        assert any(case["kind"] == "known_answer" for case in conformance["cases"])
        for case in conformance["cases"]:
            if case["expected"]["outcome"] != "success":
                continue
            assert schema_matches(
                _materialize(case["input"]), capability.input_schema
            ), case["id"]
            assert schema_matches(
                _materialize(case["expected"]["output"]), capability.output_schema
            ), case["id"]


def test_monthly_calendar_invariants_are_contract_owned() -> None:
    registry = load_registry(root=REGISTRY_ROOT).as_protocol_registry()
    capability = next(
        item for item in registry.capabilities if item.id == "returns.monthly_calendar_matrix"
    )
    constraints = {item.id: item for item in capability.constraints}
    assert {
        "duplicate_months",
        "invalid_month_labels",
        "non_consecutive_months",
        "non_increasing_months",
        "return_count_mismatch",
    } <= set(constraints)
    assert "is_consecutive_calendar_months" in str(
        constraints["non_consecutive_months"].model_dump(mode="json")
    )
    assert "interval_count" in str(
        constraints["return_count_mismatch"].model_dump(mode="json")
    )


def test_conformance_records_do_not_claim_an_external_backend() -> None:
    for path in REGISTRY_ROOT.glob("capabilities/*/*/conformance.yaml"):
        conformance = _load(path)
        if conformance["capability"]["id"] not in MIGRATED_CAPABILITIES:
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert all(
            name not in text
            for name in ("openbb", "lseg", "quantlib", "reuters", "statsmodels")
        )

