"""Executable evidence and contract tests for ``dq.market_data.log_return``."""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from defined_quant import preflight, render_svg, subject_hash
from defined_quant.market_data.log_return.component import FORMULA, Inputs, Output, log_return
from defined_quant.types import (
    AmbiguousInput,
    Derivation,
    DomainError,
    InputRef,
    NumberFormat,
    OutputRef,
    PriceKind,
    Unit,
)
from pydantic import ValidationError


def _timestamp(day: int) -> datetime:
    return datetime(2026, 7, day, tzinfo=UTC)


def _rules(error: DomainError) -> set[str]:
    violations = error.details.get("violations", [])
    assert isinstance(violations, list)
    return {
        str(item["rule"])
        for item in violations
        if isinstance(item, dict) and "rule" in item
    }


def test_contract_exports_are_present() -> None:
    assert Inputs is not None
    assert Output is not None
    assert callable(log_return)


def test_formula_surfaces_match_the_executed_branches() -> None:
    component_dir = Path(__file__).parent
    contract = json.loads((component_dir / "contract.yaml").read_text(encoding="utf-8"))
    readme = (component_dir / "README.md").read_text(encoding="utf-8")
    regular = log_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)
    finite_ratio = log_return((1.0, 1e-16), price_kind=PriceKind.ADJUSTED)
    fallback = log_return((1e-308, 1e308), price_kind=PriceKind.ADJUSTED)

    expected = (
        "rₜ = log1p((Pₜ − Pₜ₋₁) / Pₜ₋₁) if Pₜ ≥ Pₜ₋₁ / 2 and Pₜ₋₁ ≥ Pₜ / 2; "
        "otherwise q = Pₜ / Pₜ₋₁ and rₜ = log(q) if 2⁻¹⁰²² ≤ q < ∞, else "
        "log(Pₜ) − log(Pₜ₋₁)"
    )
    assert FORMULA == expected
    assert contract["display"]["formula"] == FORMULA
    assert f"`{FORMULA}`" in readme
    assert FORMULA in regular.transformations[0]
    caption = regular.visualizations[0].caption
    assert caption is not None
    assert FORMULA in caption
    assert regular.derivations[0].expression.startswith("log1p(")
    assert finite_ratio.derivations[0].expression == "log(prices[1] / prices[0])"
    assert fallback.derivations[0].expression == "log(prices[1]) - log(prices[0])"


def test_evidence_inv_001() -> None:
    prices = (80.0, 84.0, 79.8, 91.77)
    result = log_return(prices, price_kind=PriceKind.ADJUSTED)

    assert math.fsum(result.returns) == pytest.approx(
        math.log(prices[-1]) - math.log(prices[0]),
        rel=1e-12,
        abs=1e-12,
    )


def test_evidence_inv_002() -> None:
    prices = (10.0, 10.5, 9.75, 11.25)
    original = log_return(prices, price_kind=PriceKind.ADJUSTED)
    scaled = log_return(
        tuple(128.0 * value for value in prices),
        price_kind=PriceKind.ADJUSTED,
    )

    assert scaled.returns == pytest.approx(original.returns, rel=1e-12, abs=1e-12)

    large_prices = (3.8128310418816054e303, 1.681130672805337e303)
    large = log_return(large_prices, price_kind=PriceKind.ADJUSTED)
    large_scaled = log_return(
        tuple(1e-302 * value for value in large_prices),
        price_kind=PriceKind.ADJUSTED,
    )
    assert large_scaled.returns == large.returns


def test_evidence_inv_003() -> None:
    result = log_return((42.0, 42.0, 42.0), price_kind=PriceKind.ADJUSTED)

    assert result.returns == (0.0, 0.0)


def test_evidence_inv_004() -> None:
    timestamps = (_timestamp(24), _timestamp(27), _timestamp(28))
    result = log_return(
        (100.0, 105.0, 102.9),
        price_kind=PriceKind.ADJUSTED,
        timestamps=timestamps,
    )
    visualization = result.visualizations[0]

    assert visualization.series[0].values == result.returns
    assert visualization.categories == tuple(value.isoformat() for value in timestamps[1:])
    assert visualization.y_axis.unit is Unit.DECIMAL
    assert visualization.y_axis.number_format is NumberFormat.DECIMAL
    assert visualization.assumptions == result.assumptions
    assert visualization.warnings == result.warnings


def test_evidence_inv_005() -> None:
    prices = (100.0, 105.0, 1e-308, 1e308)
    result = log_return(prices, price_kind=PriceKind.ADJUSTED)

    assert len(result.derivations) == len(result.returns)
    for index, derivation in enumerate(result.derivations):
        assert derivation.output == OutputRef(field="returns", index=index)
        assert derivation.inputs == (
            InputRef(field="prices", index=index),
            InputRef(field="prices", index=index + 1),
        )
        assert derivation.value == result.returns[index]
    assert result.derivations[-1].expression == "log(prices[3]) - log(prices[2])"


def test_close_prices_preserve_relative_precision() -> None:
    result = log_return(
        (1e16, 1.0000000000000002e16),
        price_kind=PriceKind.ADJUSTED,
    )

    assert result.returns == pytest.approx((math.log1p(2e-16),), rel=1e-12, abs=0.0)
    assert result.returns[0] != 0.0


@pytest.mark.parametrize("current", [0.5, 2.0])
def test_factor_of_two_boundaries_use_log1p(current: float) -> None:
    result = log_return((1.0, current), price_kind=PriceKind.ADJUSTED)

    assert result.derivations[0].expression == (
        "log1p((prices[1] - prices[0]) / prices[0])"
    )


@pytest.mark.parametrize(
    ("prices", "expected"),
    [
        ((1e-308, 1e308), 1418.3924172843322),
        ((1e308, 1e-308), -1418.3924172843322),
    ],
)
def test_extreme_ratios_use_finite_log_difference_fallback(
    prices: tuple[float, float],
    expected: float,
) -> None:
    result = log_return(prices, price_kind=PriceKind.ADJUSTED)

    assert result.returns == pytest.approx((expected,), rel=1e-12, abs=1e-12)
    assert result.derivations[0] == Derivation(
        output=OutputRef(field="returns", index=0),
        inputs=(InputRef(field="prices", index=0), InputRef(field="prices", index=1)),
        expression="log(prices[1]) - log(prices[0])",
        value=result.returns[0],
    )


def test_large_representable_decline_uses_finite_ratio_branch() -> None:
    result = log_return((1.0, 1e-16), price_kind=PriceKind.ADJUSTED)

    assert result.returns == pytest.approx((math.log(1e-16),), rel=1e-15, abs=1e-15)
    assert result.derivations[0].expression == "log(prices[1] / prices[0])"


def test_nonzero_subnormal_ratio_uses_log_difference_fallback() -> None:
    previous = 1.4751157857084535e66
    current = 4.301863441684936e-258
    ratio = current / previous
    result = log_return((previous, current), price_kind=PriceKind.ADJUSTED)
    expected = -744.9672583282089

    assert 0.0 < ratio < sys.float_info.min
    assert result.returns[0] == pytest.approx(
        expected,
        rel=0.0,
        abs=math.ulp(expected),
    )
    assert result.derivations[0].expression == (
        "log(prices[1]) - log(prices[0])"
    )


def test_large_same_scale_decline_avoids_log_difference_cancellation() -> None:
    previous = 3.8128310418816054e303
    current = 1.681130672805337e303
    scale = 1e-302
    expected = -0.8189053822553604

    result = log_return((previous, current), price_kind=PriceKind.ADJUSTED)
    scaled = log_return(
        (previous * scale, current * scale),
        price_kind=PriceKind.ADJUSTED,
    )

    assert result.returns[0] == pytest.approx(
        expected,
        rel=0.0,
        abs=3.0 * math.ulp(expected),
    )
    assert scaled.returns == result.returns
    assert result.derivations[0].expression == "log(prices[1] / prices[0])"
    assert scaled.derivations[0].expression == "log(prices[1] / prices[0])"


@pytest.mark.parametrize(
    "prices",
    [(math.nan, -1.0), (-1.0, math.nan), (100.0, math.inf)],
)
def test_non_finite_prices_do_not_evaluate_minimum(
    prices: tuple[float, ...],
) -> None:
    with pytest.raises(DomainError) as caught:
        log_return(prices, price_kind=PriceKind.ADJUSTED)
    assert _rules(caught.value) == {"non_finite_prices"}


@pytest.mark.parametrize("invalid_price", [0.0, -1.0])
def test_non_positive_prices_are_refused(invalid_price: float) -> None:
    with pytest.raises(DomainError) as caught:
        log_return((100.0, invalid_price), price_kind=PriceKind.ADJUSTED)
    assert _rules(caught.value) == {"non_positive_prices"}


def test_models_are_frozen_and_reject_extra_fields() -> None:
    inputs = Inputs(prices=(100.0, 101.0), price_kind=PriceKind.ADJUSTED)

    with pytest.raises(ValidationError):
        inputs.__setattr__("prices", (100.0, 102.0))
    with pytest.raises(ValidationError):
        Inputs.model_validate(
            {
                "prices": [100.0, 101.0],
                "price_kind": "adjusted",
                "undeclared": True,
            }
        )


def test_price_inputs_reject_boolean_and_numeric_string_coercion() -> None:
    for invalid_price in (True, "100.0"):
        with pytest.raises(ValidationError):
            Inputs.model_validate(
                {
                    "prices": [invalid_price, 101.0],
                    "price_kind": "adjusted",
                }
            )

    with pytest.raises(ValidationError):
        log_return((True, 101.0), price_kind=PriceKind.ADJUSTED)

    assert Inputs(prices=(100, 101), price_kind=PriceKind.ADJUSTED).prices == (
        100.0,
        101.0,
    )


def test_semantic_port_schema_marks_log_return_convention() -> None:
    input_properties = Inputs.model_json_schema()["properties"]
    output_properties = Output.model_json_schema()["properties"]

    assert input_properties["prices"]["x-defined-quant-port"]["concept"] == "price_series"
    assert input_properties["prices"]["x-defined-quant-port"]["provenance_requirement"] == (
        "not_required"
    )
    assert output_properties["returns"]["x-defined-quant-port"]["convention"] == (
        "log_periodic_return"
    )
    assert output_properties["return_kind"]["x-defined-quant-port"]["direction"] == "output"


def test_output_provenance_and_subject_binding() -> None:
    result = log_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)

    assert result.component_id == "dq.market_data.log_return"
    assert result.version == "0.1.1"
    assert result.subject_hash == subject_hash(result.component_id)
    assert len(result.subject_hash) == 64
    assert result.unit is Unit.DECIMAL


def test_timestamp_frequency_and_adjustment_limits_are_disclosed() -> None:
    result = log_return(
        (100.0, 101.0),
        price_kind=PriceKind.ADJUSTED,
        timestamps=(_timestamp(24), _timestamp(25)),
    )

    assert result.warnings == ()
    assert tuple(value.split(":", 1)[0] for value in result.disclosures) == (
        "gap_check_not_assessed",
        "frequency_not_inferred",
        "adjustment_method_not_audited",
    )
    assert result.visualizations[0].warnings == ()


def test_output_rejects_incomplete_reindexed_or_false_branch_lineage() -> None:
    result = log_return((100.0, 105.0, 102.9), price_kind=PriceKind.ADJUSTED)
    payload = result.model_dump(mode="python")
    payload["derivations"] = payload["derivations"][:-1]
    with pytest.raises(ValidationError, match="every return must have exactly one"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["derivations"][0]["inputs"][0]["index"] = 2
    with pytest.raises(ValidationError, match="two source prices"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["derivations"][0]["expression"] = "log(prices[99])"
    with pytest.raises(ValidationError, match="executed branch"):
        Output.model_validate(payload)


def test_large_result_always_contains_the_complete_visualization() -> None:
    result = log_return((100.0,) * 502, price_kind=PriceKind.ADJUSTED)

    assert len(result.returns) == 501
    assert len(result.derivations) == 501
    assert result.visualizations[0].series[0].values == result.returns
    assert len(result.visualizations[0].categories) == len(result.returns)
    assert not any(value.startswith("visualization_omitted:") for value in result.warnings)


def test_return_timestamps_align_to_interval_ends() -> None:
    timestamps = (_timestamp(24), _timestamp(27), _timestamp(28))
    result = log_return(
        (100.0, 101.0, 102.0),
        price_kind=PriceKind.ADJUSTED,
        timestamps=timestamps,
        declared_frequency="daily",
    )

    assert result.return_timestamps == timestamps[1:]
    assert result.declared_frequency is not None
    assert result.declared_frequency.value == "daily"
    assert result.ordering_status == "verified"


def test_result_and_svg_are_deterministic() -> None:
    prices = (100.0, 105.0, 102.9)
    timestamps = (_timestamp(24), _timestamp(27), _timestamp(28))
    first = log_return(prices, price_kind=PriceKind.ADJUSTED, timestamps=timestamps)
    second = log_return(prices, price_kind=PriceKind.ADJUSTED, timestamps=timestamps)

    assert first.model_dump_json() == second.model_dump_json()
    assert render_svg(first.visualizations[0]) == render_svg(second.visualizations[0])


def test_missing_price_kind_is_an_explicit_question() -> None:
    with pytest.raises(AmbiguousInput) as caught:
        preflight(
            "dq.market_data.log_return",
            prices=(100.0, 101.0),
            timestamps=None,
            declared_frequency=None,
        )

    assert caught.value.details["questions"][0]["field"] == "price_kind"


def test_naive_timestamp_is_rejected_by_the_canonical_input_model() -> None:
    with pytest.raises(ValidationError):
        log_return(
            (100.0, 101.0),
            price_kind=PriceKind.ADJUSTED,
            timestamps=(datetime(2026, 7, 24), datetime(2026, 7, 25)),
        )
