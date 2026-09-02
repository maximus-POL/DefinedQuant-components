from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from defined_quant.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = ROOT / "registry"

MIGRATED_METHODS = {
    "dq.market_data.log_return": (
        "log-return",
        ("returns.log",),
    ),
    "dq.market_data.monthly_return_matrix": (
        "monthly-return-matrix",
        ("returns.simple", "returns.monthly_calendar_matrix"),
    ),
    "dq.market_data.rebased_price_index": (
        "rebased-price-index",
        ("prices.rebase",),
    ),
    "dq.performance.drawdown": (
        "drawdown",
        ("performance.drawdown",),
    ),
    "dq.volatility.historical_volatility": (
        "historical-volatility",
        (
            "statistics.sample_standard_deviation",
            "statistics.square_root_annualize",
        ),
    ),
    "dq.volatility.rolling_historical_volatility": (
        "rolling-historical-volatility",
        (
            "statistics.rolling_sample_standard_deviation",
            "statistics.square_root_annualize_series",
        ),
    ),
}


def _load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_all_seven_public_method_ids_and_slugs_are_stable() -> None:
    registry = load_registry(root=REGISTRY_ROOT)
    methods = {item.id: item.spec for item in registry.methods}

    assert set(methods) == {*MIGRATED_METHODS, "dq.market_data.simple_return"}
    for method_id, (website_slug, _) in MIGRATED_METHODS.items():
        method = methods[method_id]
        assert method is not None
        assert method.version == "1.0.0"
        assert method.discovery.website_slug == website_slug
        assert method.lifecycle.value == "draft"


def test_migrated_recipes_reference_capabilities_only() -> None:
    registry = load_registry(root=REGISTRY_ROOT).as_protocol_registry()
    methods = {item.id: item for item in registry.methods}

    for method_id, (_, expected_capabilities) in MIGRATED_METHODS.items():
        method = methods[method_id]
        assert tuple(step.capability.id for step in method.recipe.steps) == expected_capabilities
        authored_recipe = _load(
            next(
                path
                for path in REGISTRY_ROOT.glob("methods/*/*/method.yaml")
                if _load(path)["id"] == method_id
            )
        )["recipe"]
        recipe_text = yaml.safe_dump(authored_recipe, sort_keys=True).lower()
        assert all(
            token not in recipe_text
            for token in ("adapter", "backend", "dq_native", "provider", "transport")
        )


def test_migrated_method_fields_use_auditable_capability_bindings() -> None:
    registry = load_registry(root=REGISTRY_ROOT).as_protocol_registry()
    methods = {item.id: item for item in registry.methods}

    for method_id in MIGRATED_METHODS:
        method = methods[method_id]
        assert method.schema_bindings
        recipe_capabilities = {step.capability for step in method.recipe.steps}
        assert {binding.capability for binding in method.schema_bindings} <= recipe_capabilities
        for binding in method.schema_bindings:
            schema = (
                method.input_schema
                if binding.direction.value == "input"
                else method.output_schema
            )
            properties = schema["properties"]
            capability = next(
                item
                for item in registry.capabilities
                if item.ref == binding.capability
            )
            capability_schema = (
                capability.input_schema
                if binding.direction.value == "input"
                else capability.output_schema
            )
            assert properties[binding.method_field] == capability_schema["properties"][
                binding.capability_field
            ]


def test_method_evidence_does_not_reuse_legacy_subject_hash_as_identity() -> None:
    for method_id in MIGRATED_METHODS:
        evidence_path = next(
            path
            for path in REGISTRY_ROOT.glob("evidence/methods/*/*.yaml")
            if _load(path)["subject"]["id"] == method_id
        )
        evidence = _load(evidence_path)
        assert evidence["subject"] == {
            "kind": "method",
            "id": method_id,
            "version": "1.0.0",
        }
        text = evidence_path.read_text(encoding="utf-8")
        assert "subject_hash" not in text
        assert evidence["review"]["domain"] == "none"
        assert evidence["non_claims"]

