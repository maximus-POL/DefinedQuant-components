"""Evidence and behavior tests for ``dq.market_data.monthly_return_matrix``."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from defined_quant import preflight, render_svg, subject_hash
from defined_quant.market_data.monthly_return_matrix.component import (
    COMPONENT_ID,
    FORMULA,
    Inputs,
    Output,
    monthly_return_matrix,
)
from defined_quant.types import ChartKind, DomainError, NumberFormat, PriceKind, Unit
from pydantic import ValidationError


def test_formula_surfaces_match() -> None:
    component_dir = Path(__file__).parent
    contract = json.loads((component_dir / "contract.yaml").read_text(encoding="utf-8"))
    readme = (component_dir / "README.md").read_text(encoding="utf-8")
    result = monthly_return_matrix(
        (100.0, 110.0),
        ("2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )

    assert contract["display"]["formula"] == FORMULA
    assert f"`{FORMULA}`" in readme
    assert FORMULA in result.transformations[0]
    assert FORMULA in (result.visualizations[0].caption or "")


def test_evidence_inv_001() -> None:
    prices = (100.0, 80.0, 88.0, 79.2)
    result = monthly_return_matrix(
        prices,
        ("2022-11", "2022-12", "2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )

    assert math.prod(1.0 + value for value in result.monthly_returns) == pytest.approx(
        prices[-1] / prices[0]
    )


def test_evidence_inv_002() -> None:
    result = monthly_return_matrix(
        (100.0, 80.0, 88.0, 79.2),
        ("2022-11", "2022-12", "2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )
    chart = result.visualizations[0]

    assert chart.schema_version == 1
    assert chart.kind is ChartKind.HEATMAP
    assert chart.categories == result.return_months
    assert chart.series[0].values == result.monthly_returns
    assert chart.y_axis.unit is Unit.DECIMAL
    assert chart.y_axis.number_format is NumberFormat.PERCENT
    svg = render_svg(chart)
    assert "2022" in svg and "2023" in svg


def test_non_consecutive_months_are_blocking() -> None:
    with pytest.raises(DomainError) as caught:
        monthly_return_matrix(
            (100.0, 110.0),
            ("2023-01", "2023-03"),
            price_kind="adjusted",
            observation_kind="completed_month_end",
        )
    assert caught.value.details["violations"][0]["rule"] == "non_consecutive_months"


@pytest.mark.parametrize(
    ("months", "expected_rule"),
    [
        (("2023-00", "2023-01"), "invalid_month_labels"),
        (("2023-01", "2023-03"), "non_consecutive_months"),
    ],
)
def test_month_contract_blocks_unexecutable_labels_in_preflight(
    months: tuple[str, str],
    expected_rule: str,
) -> None:
    with pytest.raises(DomainError) as caught:
        preflight(
            COMPONENT_ID,
            prices=(100.0, 110.0),
            months=months,
            price_kind="adjusted",
            observation_kind="completed_month_end",
        )

    assert caught.value.details["violations"][0]["rule"] == expected_rule


def test_month_contract_accepts_consecutive_year_boundary() -> None:
    assert (
        preflight(
            COMPONENT_ID,
            prices=(100.0, 110.0),
            months=("2023-12", "2024-01"),
            price_kind="adjusted",
            observation_kind="completed_month_end",
        )
        == ()
    )


def test_result_provenance_and_svg_are_deterministic() -> None:
    first = monthly_return_matrix(
        (100.0, 110.0),
        ("2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )
    second = monthly_return_matrix(
        (100.0, 110.0),
        ("2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )

    assert first.subject_hash == subject_hash(first.component_id)
    assert first.model_dump_json() == second.model_dump_json()
    assert render_svg(first.visualizations[0]) == render_svg(second.visualizations[0])


@pytest.mark.parametrize(
    "invalid_return",
    [
        pytest.param(-1.0, id="total_loss"),
        pytest.param(-1.1, id="below_total_loss"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("nan"), id="nan"),
    ],
)
def test_serialized_output_rejects_returns_outside_positive_price_domain(
    invalid_return: float,
) -> None:
    result = monthly_return_matrix(
        (100.0, 110.0),
        ("2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )
    payload = result.model_dump(mode="json")
    payload["monthly_returns"][0] = invalid_return
    payload["derivations"][0]["value"] = invalid_return

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_accepts_a_representable_return_above_total_loss() -> None:
    result = monthly_return_matrix(
        (100.0, 0.0001),
        ("2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )

    assert -1.0 < result.monthly_returns[0] < 0.0
    assert Output.model_validate_json(result.model_dump_json()) == result


def test_serialized_output_rejects_a_non_decimal_unit() -> None:
    result = monthly_return_matrix(
        (100.0, 110.0),
        ("2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )
    payload = result.model_dump(mode="json")
    payload["unit"] = "unitless"

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    "return_months",
    [
        pytest.param(["2023-00", "2023-01"], id="invalid_label"),
        pytest.param(["2023-01", "2023-03"], id="missing_month"),
        pytest.param(["2023-02", "2023-01"], id="decreasing"),
    ],
)
def test_serialized_output_rejects_invalid_or_nonconsecutive_return_months(
    return_months: list[str],
) -> None:
    result = monthly_return_matrix(
        (100.0, 110.0, 120.0),
        ("2022-12", "2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )
    payload = result.model_dump(mode="json")
    payload["return_months"] = return_months

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_rejects_misaligned_return_months() -> None:
    result = monthly_return_matrix(
        (100.0, 110.0, 120.0),
        ("2022-12", "2023-01", "2023-02"),
        price_kind="adjusted",
        observation_kind="completed_month_end",
    )
    payload = result.model_dump(mode="json")
    payload["return_months"] = payload["return_months"][:1]

    with pytest.raises(ValidationError, match="return months must align"):
        Output.model_validate(payload)


def test_input_model_is_strict_and_frozen() -> None:
    inputs = Inputs(
        prices=(100.0, 110.0),
        months=("2023-01", "2023-02"),
        price_kind=PriceKind.ADJUSTED,
        observation_kind="completed_month_end",
    )
    with pytest.raises(ValidationError):
        inputs.__setattr__("months", ("2023-01",))
    with pytest.raises(ValidationError):
        Inputs.model_validate(
            {
                "prices": [100.0, "110"],
                "months": ["2023-01", "2023-02"],
                "price_kind": "adjusted",
                "observation_kind": "completed_month_end",
            }
        )
