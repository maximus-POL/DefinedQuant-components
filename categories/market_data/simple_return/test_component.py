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


def test_evidence_inv_005() -> None:
    prices = (100.0, 105.0, 102.9)
    result = simple_return(prices, price_kind=PriceKind.ADJUSTED)

    assert len(result.derivations) == len(result.returns)
    for index, derivation in enumerate(result.derivations):
        assert derivation == Derivation(
            output=OutputRef(field="returns", index=index),
            inputs=(
                InputRef(field="prices", index=index),
                InputRef(field="prices", index=index + 1),
            ),
            expression=f"(prices[{index + 1}] - prices[{index}]) / prices[{index}]",
            value=result.returns[index],
        )


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
        simple_return((True, 101.0), price_kind=PriceKind.ADJUSTED)

    assert Inputs(prices=(100, 101), price_kind=PriceKind.ADJUSTED).prices == (
        100.0,
        101.0,
    )


def test_output_provenance_and_subject_binding() -> None:
    result = simple_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)

    assert result.component_id == "dq.market_data.simple_return"
    assert result.version == "0.3.4"
    assert result.subject_hash == subject_hash(result.component_id)
    assert len(result.subject_hash) == 64
    assert result.unit is Unit.DECIMAL


def test_serialized_output_rejects_a_non_decimal_unit() -> None:
    result = simple_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)
    payload = result.model_dump(mode="json")
    payload["unit"] = "unitless"

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_constant_context_is_disclosed_without_warning() -> None:
    result = simple_return(
        (100.0, 101.0),
        price_kind=PriceKind.ADJUSTED,
        timestamps=(_timestamp(24), _timestamp(25)),
    )

    assert result.warnings == ()
    assert result.disclosures == (
        "gap_check_not_assessed: This component does not apply a calendar-aware gap policy, "
        "so gaps were not assessed.",
    )
    assert result.visualizations[0].warnings == ()


def test_blank_disclosures_are_rejected() -> None:
    result = simple_return((100.0, 101.0), price_kind=PriceKind.ADJUSTED)
    payload = result.model_dump(mode="python")
    payload["disclosures"] = ("",)

    with pytest.raises(ValidationError, match="output messages must not be blank"):
        Output.model_validate(payload)


def test_simple_return_rejects_incomplete_or_reindexed_lineage() -> None:
    result = simple_return((100.0, 105.0, 102.9), price_kind=PriceKind.ADJUSTED)
    payload = result.model_dump(mode="python")
    payload["derivations"] = payload["derivations"][:-1]

    with pytest.raises(ValidationError, match="every return must have exactly one"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["derivations"][0]["inputs"][0]["index"] = 2
    with pytest.raises(ValidationError, match="two source prices"):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    "invalid_return",
    [-1.0, -1.5, math.nan, math.inf, -math.inf],
)
def test_serialized_output_rejects_impossible_simple_returns(
    invalid_return: float,
) -> None:
    result = simple_return((100.0, 105.0), price_kind=PriceKind.ADJUSTED)
    payload = result.model_dump(mode="json")
    payload["returns"][0] = invalid_return
    payload["derivations"][0]["value"] = invalid_return

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_requires_coherent_return_timestamps() -> None:
    result = simple_return(
        (100.0, 105.0, 102.9),
        price_kind=PriceKind.ADJUSTED,
        timestamps=(_timestamp(24), _timestamp(27), _timestamp(28)),
    )

    payload = result.model_dump(mode="json")
    payload["return_timestamps"] = payload["return_timestamps"][:-1]
    with pytest.raises(ValidationError, match="must align with returns"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="json")
    payload["ordering_status"] = "unverified"
    with pytest.raises(ValidationError, match="must have verified ordering"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="json")
    payload["return_timestamps"] = tuple(reversed(payload["return_timestamps"]))
    with pytest.raises(ValidationError, match="strictly increasing"):
        Output.model_validate(payload)

    payload = simple_return(
        (100.0, 105.0),
        price_kind=PriceKind.ADJUSTED,
    ).model_dump(mode="json")
    payload["ordering_status"] = "verified"
    with pytest.raises(ValidationError, match="only when timestamps are present"):
        Output.model_validate(payload)

    payload["ordering_status"] = "unverified"
    payload["declared_frequency"] = "daily"
    with pytest.raises(ValidationError, match="declared frequency requires return timestamps"):
        Output.model_validate(payload)


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


def test_large_result_always_contains_the_complete_visualization() -> None:
    result = simple_return((100.0,) * 502, price_kind=PriceKind.ADJUSTED)

    assert len(result.returns) == 501
    assert len(result.derivations) == 501
    assert result.visualizations[0].series[0].values == result.returns
    assert len(result.visualizations[0].categories) == len(result.returns)
    assert not any(value.startswith("visualization_omitted:") for value in result.warnings)


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
