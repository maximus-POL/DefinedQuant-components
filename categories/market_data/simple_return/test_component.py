"""Executable evidence and contract tests for ``dq.market_data.simple_return``."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from defined_quant import preflight, render_svg, subject_hash
from defined_quant.market_data.simple_return.component import FORMULA, Inputs, Output, simple_return
from defined_quant.types import (
    MAX_VISUALIZATION_POINTS,
    AmbiguousInput,
    DomainError,
    Frequency,
    NumberFormat,
    PriceKind,
    ReturnKind,
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
    assert callable(simple_return)


def test_formula_surfaces_match_the_executed_expression() -> None:
    component_dir = Path(__file__).parent
    contract = json.loads((component_dir / "contract.yaml").read_text(encoding="utf-8"))
    readme = (component_dir / "README.md").read_text(encoding="utf-8")
    result = simple_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)

    assert FORMULA == "rₜ = (Pₜ − Pₜ₋₁) / Pₜ₋₁"
    assert contract["display"]["formula"] == FORMULA
    assert f"`{FORMULA}`" in readme
    assert FORMULA in result.transformations[0]
    caption = result.visualizations[0].caption
    assert caption is not None
    assert FORMULA in caption


def test_evidence_ka_001() -> None:
    result = simple_return(
        [100.0, 105.0, 102.9],
        price_kind=PriceKind.ADJUSTED,
    )

    assert result.returns == pytest.approx((0.05, -0.02), rel=1e-12, abs=1e-12)
    assert result.return_kind is ReturnKind.SIMPLE


def test_evidence_inv_001() -> None:
    prices = (80.0, 84.0, 79.8, 91.77)
    result = simple_return(prices, price_kind=PriceKind.ADJUSTED)

    compounded = math.prod(1.0 + value for value in result.returns)
    assert compounded == pytest.approx(prices[-1] / prices[0], rel=1e-12, abs=1e-12)


def test_evidence_inv_002() -> None:
    prices = (10.0, 10.5, 9.75, 11.25)
    scale = 137.0
    original = simple_return(prices, price_kind=PriceKind.ADJUSTED)
    scaled = simple_return(
        tuple(scale * value for value in prices),
        price_kind=PriceKind.ADJUSTED,
    )

    assert scaled.returns == pytest.approx(original.returns, rel=1e-12, abs=1e-12)


def test_evidence_inv_003() -> None:
    result = simple_return((42.0, 42.0, 42.0), price_kind=PriceKind.ADJUSTED)

    assert result.returns == (0.0, 0.0)


def test_evidence_inv_004() -> None:
    timestamps = (_timestamp(24), _timestamp(27), _timestamp(28))
    result = simple_return(
        (100.0, 105.0, 102.9),
        price_kind=PriceKind.ADJUSTED,
        timestamps=timestamps,
    )
    visualization = result.visualizations[0]

    assert visualization.series[0].values == result.returns
    assert visualization.categories == tuple(value.isoformat() for value in timestamps[1:])
    assert visualization.y_axis.unit is Unit.DECIMAL
    assert visualization.y_axis.number_format is NumberFormat.PERCENT
    assert visualization.assumptions == result.assumptions
    assert visualization.warnings == result.warnings


def test_evidence_bc_001() -> None:
    result = simple_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)

    assert len(result.returns) == 1
    assert result.returns[0] == pytest.approx(0.01, rel=1e-12, abs=1e-12)


def test_evidence_bc_002() -> None:
    timestamps = (_timestamp(24), _timestamp(27), _timestamp(28))
    result = simple_return(
        (100.0, 101.0, 102.0),
        price_kind=PriceKind.ADJUSTED,
        timestamps=timestamps,
    )

    assert result.return_timestamps == timestamps[1:]
    assert result.ordering_status == "verified"


def test_evidence_bc_003() -> None:
    with pytest.raises(DomainError) as caught:
        simple_return(
            (100.0, 101.0, 102.0),
            price_kind=PriceKind.ADJUSTED,
            timestamps=(_timestamp(24), _timestamp(27)),
        )

    assert "timestamp_length_mismatch" in _rules(caught.value)


def test_evidence_bc_004() -> None:
    with pytest.raises(DomainError) as caught:
        simple_return(
            (100.0, 101.0),
            price_kind=PriceKind.ADJUSTED,
            timestamps=(_timestamp(24), _timestamp(24)),
        )

    assert "duplicate_timestamps" in _rules(caught.value)


def test_evidence_bc_005() -> None:
    with pytest.raises(DomainError) as caught:
        simple_return(
            (100.0, 101.0),
            price_kind=PriceKind.ADJUSTED,
            timestamps=(_timestamp(28), _timestamp(27)),
        )

    assert "non_increasing_timestamps" in _rules(caught.value)


def test_evidence_bc_006() -> None:
    result = simple_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)

    assert result.ordering_status == "unverified"
    assert result.gap_check == "not_assessed"
    assert any(message.startswith("ordering_unverified:") for message in result.warnings)
    assert any(message.startswith("gap_check_not_assessed:") for message in result.warnings)


def test_evidence_bc_007() -> None:
    with pytest.raises(DomainError) as caught:
        simple_return(
            (100.0, 101.0),
            price_kind=PriceKind.ADJUSTED,
            declared_frequency=Frequency.DAILY,
        )

    assert "frequency_without_timestamps" in _rules(caught.value)


def test_evidence_bc_008() -> None:
    result = simple_return(
        (100.0, 101.0),
        price_kind=PriceKind.ADJUSTED,
        timestamps=(_timestamp(24), _timestamp(27)),
        declared_frequency=Frequency.DAILY,
    )

    assert result.declared_frequency is Frequency.DAILY
    assert result.gap_check == "not_assessed"
    assert any(message.startswith("gap_check_not_assessed:") for message in result.warnings)


def test_evidence_bc_009() -> None:
    with pytest.raises(DomainError) as caught:
        simple_return((100.0,), price_kind=PriceKind.ADJUSTED)

    assert "insufficient_prices" in _rules(caught.value)


@pytest.mark.parametrize("price", [0.0, -1.0])
def test_evidence_bc_010(price: float) -> None:
    with pytest.raises(DomainError) as caught:
        simple_return((100.0, price), price_kind=PriceKind.ADJUSTED)

    assert "non_positive_prices" in _rules(caught.value)


@pytest.mark.parametrize("price", [math.nan, math.inf, -math.inf])
def test_evidence_bc_011(price: float) -> None:
    with pytest.raises(DomainError) as caught:
        simple_return((100.0, price), price_kind=PriceKind.ADJUSTED)

    assert "non_finite_prices" in _rules(caught.value)


def test_evidence_bc_012() -> None:
    result = simple_return((100.0, 101.0), price_kind=PriceKind.UNADJUSTED)

    assert any(
        message.startswith("unadjusted_price_interpretation:")
        for message in result.warnings
    )


def test_evidence_bc_013() -> None:
    close_prices = simple_return(
        (1e16, 1e16 + 2.0),
        price_kind=PriceKind.ADJUSTED,
    )
    assert close_prices.returns == (2e-16,)


def test_evidence_bc_014() -> None:
    with pytest.raises(DomainError) as caught:
        simple_return((1e-308, 1e308), price_kind=PriceKind.ADJUSTED)
    assert "non_finite_result" in _rules(caught.value)


def test_evidence_bc_015() -> None:
    with pytest.raises(DomainError) as caught:
        simple_return((1e308, 1e-308), price_kind=PriceKind.ADJUSTED)
    assert "non_finite_result" in _rules(caught.value)


def test_evidence_bc_016() -> None:
    at_limit = simple_return(
        (100.0,) * (MAX_VISUALIZATION_POINTS + 1),
        price_kind=PriceKind.ADJUSTED,
    )
    above_limit = simple_return(
        (100.0,) * (MAX_VISUALIZATION_POINTS + 2),
        price_kind=PriceKind.ADJUSTED,
    )

    assert at_limit.returns == (0.0,) * MAX_VISUALIZATION_POINTS
    assert len(at_limit.visualizations) == 1
    assert len(at_limit.visualizations[0].categories) == MAX_VISUALIZATION_POINTS
    assert at_limit.visualizations[0].series[0].values == at_limit.returns
    assert not any(message.startswith("visualization_omitted:") for message in at_limit.warnings)

    assert above_limit.returns == (0.0,) * (MAX_VISUALIZATION_POINTS + 1)
    assert above_limit.visualizations == ()
    omission = next(
        message
        for message in above_limit.warnings
        if message.startswith("visualization_omitted:")
    )
    assert str(MAX_VISUALIZATION_POINTS) in omission
    assert "full return series is preserved" in omission


@pytest.mark.parametrize(
    "prices",
    [(math.nan, -1.0), (-1.0, math.nan)],
)
def test_non_finite_prices_do_not_evaluate_minimum(
    prices: tuple[float, ...],
) -> None:
    with pytest.raises(DomainError) as caught:
        simple_return(prices, price_kind=PriceKind.ADJUSTED)
    assert _rules(caught.value) == {"non_finite_prices"}


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


def test_output_provenance_and_subject_binding() -> None:
    result = simple_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)

    assert result.component_id == "dq.market_data.simple_return"
    assert result.version == "0.2.2"
    assert result.subject_hash == subject_hash(result.component_id)
    assert len(result.subject_hash) == 64
    assert result.unit is Unit.DECIMAL


def test_result_and_svg_are_deterministic() -> None:
    prices = (100.0, 105.0, 102.9)
    timestamps = (_timestamp(24), _timestamp(27), _timestamp(28))
    first = simple_return(
        prices,
        price_kind=PriceKind.ADJUSTED,
        timestamps=timestamps,
    )
    second = simple_return(
        prices,
        price_kind=PriceKind.ADJUSTED,
        timestamps=timestamps,
    )

    assert first.model_dump_json() == second.model_dump_json()
    assert render_svg(first.visualizations[0]) == render_svg(second.visualizations[0])


def test_missing_price_kind_is_an_explicit_question() -> None:
    with pytest.raises(AmbiguousInput) as caught:
        preflight(
            "dq.market_data.simple_return",
            prices=(100.0, 101.0),
            timestamps=None,
            declared_frequency=None,
        )

    assert caught.value.details["questions"][0]["field"] == "price_kind"


def test_naive_timestamp_is_rejected_by_the_canonical_input_model() -> None:
    with pytest.raises(ValidationError):
        simple_return(
            (100.0, 101.0),
            price_kind=PriceKind.ADJUSTED,
            timestamps=(datetime(2026, 7, 24), datetime(2026, 7, 25)),
        )
