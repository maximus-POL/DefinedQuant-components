from __future__ import annotations

from pathlib import Path

from defined_quant.registry import load_registry
from defined_quant.schema_validation import schema_is_supported, schema_matches

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = ROOT / "registry"


def test_migrated_method_schemas_and_examples_are_executable_by_core() -> None:
    registry = load_registry(root=REGISTRY_ROOT)
    migrated = {
        item.id: item.spec
        for item in registry.methods
        if item.id != "dq.market_data.simple_return"
    }

    for method_id, method in migrated.items():
        assert method is not None
        assert schema_is_supported(method.input_schema)
        assert schema_is_supported(method.output_schema)
        examples_path = next(
            path
            for path in REGISTRY_ROOT.glob("methods/*/*/examples.yaml")
            if method_id in path.read_text(encoding="utf-8")
        )
        import yaml  # type: ignore[import-untyped]

        examples = yaml.safe_load(examples_path.read_text(encoding="utf-8"))
        assert isinstance(examples, dict)
        for example in examples["examples"]:
            assert schema_matches(example["input"], method.input_schema), example["id"]


def test_required_financial_conventions_are_explicit_and_undefaulted() -> None:
    registry = load_registry(root=REGISTRY_ROOT).as_protocol_registry()
    methods = {item.id: item for item in registry.methods}

    expected = {
        "dq.market_data.log_return": {"price_kind"},
        "dq.market_data.monthly_return_matrix": {"observation_kind", "price_kind"},
        "dq.market_data.rebased_price_index": {"base_index", "base_value", "price_kind"},
        "dq.performance.drawdown": {"price_kind"},
        "dq.volatility.historical_volatility": {"annualization_factor", "return_kind"},
        "dq.volatility.rolling_historical_volatility": {
            "annualization_factor",
            "return_kind",
            "window_length",
        },
    }
    for method_id, required_fields in expected.items():
        method = methods[method_id]
        required_conventions = {
            item.field for item in method.conventions if item.required_from_user
        }
        default_fields = {item.field for item in method.defaults}
        assert required_conventions == required_fields
        assert required_fields.isdisjoint(default_fields)


def test_volatility_methods_are_decomposed_into_atomic_reusable_capabilities() -> None:
    registry = load_registry(root=REGISTRY_ROOT).as_protocol_registry()
    methods = {item.id: item for item in registry.methods}

    historical = methods["dq.volatility.historical_volatility"]
    assert [step.step_id for step in historical.recipe.steps] == ["estimate", "annualize"]
    assert [step.capability.id for step in historical.recipe.steps] == [
        "statistics.sample_standard_deviation",
        "statistics.square_root_annualize",
    ]

    rolling = methods["dq.volatility.rolling_historical_volatility"]
    assert [step.step_id for step in rolling.recipe.steps] == [
        "estimate_windows",
        "annualize",
    ]
    assert [step.capability.id for step in rolling.recipe.steps] == [
        "statistics.rolling_sample_standard_deviation",
        "statistics.square_root_annualize_series",
    ]


def test_monthly_method_composes_simple_returns_with_calendar_alignment() -> None:
    registry = load_registry(root=REGISTRY_ROOT).as_protocol_registry()
    method = next(
        item for item in registry.methods if item.id == "dq.market_data.monthly_return_matrix"
    )
    assert [step.step_id for step in method.recipe.steps] == ["calculate", "align_calendar"]
    assert [step.capability.id for step in method.recipe.steps] == [
        "returns.simple",
        "returns.monthly_calendar_matrix",
    ]
    assert method.recipe.steps[1].depends_on == ("calculate",)

