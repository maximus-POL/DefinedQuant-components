"""Evidence and behavior tests for ``dq.market_data.rebased_price_index``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from defined_quant import preflight, render_svg, subject_hash
from defined_quant.market_data.rebased_price_index.component import (
    COMPONENT_ID,
    FORMULA,
    Inputs,
    Output,
    rebased_price_index,
)
from defined_quant.types import DomainError, NumberFormat, PriceKind, Unit
from pydantic import ValidationError


def test_formula_surfaces_match() -> None:
    component_dir = Path(__file__).parent
    contract = json.loads((component_dir / "contract.yaml").read_text(encoding="utf-8"))
    readme = (component_dir / "README.md").read_text(encoding="utf-8")
    result = rebased_price_index(
        (80.0, 100.0), price_kind="adjusted", base_index=1, base_value=100.0
    )

    assert contract["display"]["formula"] == FORMULA
    assert f"`{FORMULA}`" in readme
    assert FORMULA in result.transformations[0]
    assert FORMULA in (result.visualizations[0].caption or "")


def test_evidence_inv_001() -> None:
    original = rebased_price_index(
        (80.0, 100.0, 60.0),
        price_kind="adjusted",
        base_index=1,
        base_value=100.0,
    )
    scaled = rebased_price_index(
        (800.0, 1000.0, 600.0),
        price_kind="adjusted",
        base_index=1,
        base_value=100.0,
    )

    assert original.index_values == scaled.index_values
    assert original.index_values[original.base_index] == original.base_value


def test_evidence_inv_002() -> None:
    result = rebased_price_index(
        (80.0, 100.0, 60.0, 120.0),
        price_kind="adjusted",
        base_index=1,
        base_value=100.0,
    )
    chart = result.visualizations[0]

    assert chart.series[0].values == result.index_values
    assert chart.y_axis.unit is Unit.UNITLESS
    assert chart.y_axis.number_format is NumberFormat.DECIMAL
    assert len(result.derivations) == len(result.index_values)


def test_result_provenance_and_svg_are_deterministic() -> None:
    first = rebased_price_index(
        (80.0, 100.0, 60.0),
        price_kind="adjusted",
        base_index=1,
        base_value=100.0,
    )
    second = rebased_price_index(
        (80.0, 100.0, 60.0),
        price_kind="adjusted",
        base_index=1,
        base_value=100.0,
    )

    assert first.subject_hash == subject_hash(first.component_id)
    assert first.model_dump_json() == second.model_dump_json()
    assert render_svg(first.visualizations[0]) == render_svg(second.visualizations[0])


def test_out_of_range_base_is_blocking() -> None:
    with pytest.raises(DomainError) as caught:
        rebased_price_index(
            (100.0,), price_kind="adjusted", base_index=1, base_value=100.0
        )
    assert caught.value.details["violations"][0]["rule"] == "base_index_out_of_range"


def test_out_of_range_base_is_declared_in_contract_preflight() -> None:
    with pytest.raises(DomainError) as caught:
        preflight(
            COMPONENT_ID,
            prices=(100.0,),
            price_kind="adjusted",
            base_index=1,
            base_value=100.0,
            timestamps=None,
            declared_frequency=None,
        )

    assert caught.value.details["violations"][0]["rule"] == "base_index_out_of_range"


@pytest.mark.parametrize(
    ("prices", "base_value", "expected"),
    [
        ((1e-308, 1e308), 1e-308, (1e-308, 1e308)),
        ((1e308, 1e-308), 1e308, (1e308, 1e-308)),
        ((1e308, 7e-16), 1e308, (1e308, 7e-16)),
    ],
)
def test_rebasing_recovers_representable_extreme_values(
    prices: tuple[float, float],
    base_value: float,
    expected: tuple[float, float],
) -> None:
    result = rebased_price_index(
        prices,
        price_kind="adjusted",
        base_index=0,
        base_value=base_value,
    )

    assert result.index_values == pytest.approx(expected, rel=1e-12, abs=0.0)
    assert render_svg(result.visualizations[0])


def test_rebasing_still_blocks_a_truly_unrepresentable_result() -> None:
    with pytest.raises(DomainError) as caught:
        rebased_price_index(
            (1e-308, 1e308),
            price_kind="adjusted",
            base_index=0,
            base_value=1e308,
        )

    assert caught.value.details["violations"][0]["rule"] == "non_finite_result"


def test_serialized_output_rejects_a_base_index_outside_the_index_series() -> None:
    result = rebased_price_index(
        (80.0, 100.0),
        price_kind="adjusted",
        base_index=1,
        base_value=100.0,
    )
    payload = result.model_dump(mode="json")
    payload["base_index"] = len(result.index_values)

    with pytest.raises(ValidationError, match="base index must identify an index value"):
        Output.model_validate(payload)


def test_serialized_output_rejects_a_value_that_does_not_match_its_base() -> None:
    result = rebased_price_index(
        (80.0, 100.0),
        price_kind="adjusted",
        base_index=1,
        base_value=100.0,
    )
    payload = result.model_dump(mode="json")
    payload["index_values"][result.base_index] = 200.0
    payload["derivations"][result.base_index]["value"] = 200.0

    with pytest.raises(
        ValidationError,
        match="the index value at base_index must equal base_value",
    ):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(0.0, id="zero"),
        pytest.param(-1.0, id="negative"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("nan"), id="nan"),
    ],
)
def test_serialized_output_rejects_nonpositive_or_nonfinite_index_values(
    invalid_value: float,
) -> None:
    result = rebased_price_index(
        (100.0, 80.0),
        price_kind="adjusted",
        base_index=0,
        base_value=100.0,
    )
    payload = result.model_dump(mode="json")
    payload["index_values"][1] = invalid_value
    payload["derivations"][1]["value"] = invalid_value

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_rejects_a_non_unitless_unit() -> None:
    result = rebased_price_index(
        (100.0, 80.0),
        price_kind="adjusted",
        base_index=0,
        base_value=100.0,
    )
    payload = result.model_dump(mode="json")
    payload["unit"] = "decimal"

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_rejects_misaligned_or_nonincreasing_timestamps() -> None:
    result = rebased_price_index(
        (100.0, 80.0),
        price_kind="adjusted",
        base_index=0,
        base_value=100.0,
        timestamps=("2023-01-01T00:00:00Z", "2023-01-02T00:00:00Z"),
        declared_frequency="daily",
    )
    payload = result.model_dump(mode="json")
    payload["index_timestamps"] = payload["index_timestamps"][:1]
    with pytest.raises(ValidationError, match="index timestamps must align"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="json")
    payload["index_timestamps"] = list(reversed(payload["index_timestamps"]))
    with pytest.raises(ValidationError, match="index timestamps must be strictly increasing"):
        Output.model_validate(payload)


def test_serialized_output_rejects_timestamp_interpretation_mismatches() -> None:
    without_timestamps = rebased_price_index(
        (100.0, 80.0),
        price_kind="adjusted",
        base_index=0,
        base_value=100.0,
    ).model_dump(mode="json")
    without_timestamps["ordering_status"] = "verified"
    with pytest.raises(ValidationError, match="must be unverified without timestamps"):
        Output.model_validate(without_timestamps)

    without_timestamps = rebased_price_index(
        (100.0, 80.0),
        price_kind="adjusted",
        base_index=0,
        base_value=100.0,
    ).model_dump(mode="json")
    without_timestamps["declared_frequency"] = "daily"
    with pytest.raises(ValidationError, match="declared frequency requires index timestamps"):
        Output.model_validate(without_timestamps)

    with_timestamps = rebased_price_index(
        (100.0, 80.0),
        price_kind="adjusted",
        base_index=0,
        base_value=100.0,
        timestamps=("2023-01-01T00:00:00Z", "2023-01-02T00:00:00Z"),
    ).model_dump(mode="json")
    with_timestamps["ordering_status"] = "unverified"
    with pytest.raises(ValidationError, match="must be verified when timestamps are present"):
        Output.model_validate(with_timestamps)


def test_models_reject_coercion_and_are_frozen() -> None:
    inputs = Inputs(
        prices=(100.0,),
        price_kind=PriceKind.ADJUSTED,
        base_index=0,
        base_value=100.0,
    )
    with pytest.raises(ValidationError):
        inputs.__setattr__("base_index", 1)
    with pytest.raises(ValidationError):
        Inputs.model_validate(
            {
                "prices": ["100"],
                "price_kind": "adjusted",
                "base_index": 0,
                "base_value": 100.0,
            }
        )
