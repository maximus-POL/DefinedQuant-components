"""Evidence and behavior tests for ``dq.performance.drawdown``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from defined_quant import preflight, render_svg, subject_hash
from defined_quant.performance.drawdown.component import (
    COMPONENT_ID,
    FORMULA,
    Inputs,
    Output,
    drawdown,
)
from defined_quant.types import DomainError, InputRef, NumberFormat, PriceKind, Unit
from pydantic import ValidationError


def test_formula_surfaces_match() -> None:
    component_dir = Path(__file__).parent
    contract = json.loads((component_dir / "contract.yaml").read_text())
    readme = (component_dir / "README.md").read_text()
    result = drawdown((100.0, 80.0), price_kind="adjusted")

    assert contract["display"]["formula"] == FORMULA
    assert f"`{FORMULA}`" in readme
    assert FORMULA in result.transformations[0]
    assert FORMULA in (result.visualizations[0].caption or "")


def test_evidence_inv_001() -> None:
    original = drawdown((100.0, 120.0, 84.0, 120.0), price_kind="adjusted")
    scaled = drawdown((1000.0, 1200.0, 840.0, 1200.0), price_kind="adjusted")

    assert scaled.drawdowns == original.drawdowns
    assert scaled.maximum_drawdown == original.maximum_drawdown
    assert (original.peak_index, original.trough_index, original.recovery_index) == (
        1,
        2,
        3,
    )


def test_evidence_inv_002() -> None:
    result = drawdown((10.0, 10.0, 11.0, 12.0), price_kind="adjusted")

    assert result.drawdowns == (0.0, 0.0, 0.0, 0.0)
    assert result.maximum_drawdown == 0.0
    assert (
        result.peak_index,
        result.trough_index,
        result.recovery_index,
        result.recovered,
    ) == (0, 0, 0, True)


def test_evidence_inv_003() -> None:
    result = drawdown((100.0, 120.0, 90.0, 84.0, 120.0), price_kind="adjusted")
    chart = result.visualizations[0]

    assert chart.series[0].values == result.drawdowns
    assert chart.y_axis.unit is Unit.DECIMAL
    assert chart.y_axis.number_format is NumberFormat.PERCENT
    assert len(result.derivations) == len(result.drawdowns) + 1


def test_latest_equal_high_becomes_running_peak() -> None:
    result = drawdown((100.0, 120.0, 120.0, 90.0), price_kind="adjusted")

    assert result.running_peak_indices == (0, 1, 2, 2)
    assert result.peak_index == 2


def test_output_rejects_peak_that_is_not_the_trough_running_peak() -> None:
    result = drawdown((100.0, 120.0, 84.0, 120.0), price_kind="adjusted")
    invalid_maximum = result.derivations[-1].model_copy(
        update={
            "inputs": (
                InputRef(field="prices", index=result.trough_index),
                InputRef(field="prices", index=0),
            ),
            "expression": f"prices[{result.trough_index}] / prices[0] - 1",
        }
    )
    payload = result.model_dump(mode="python")
    payload["peak_index"] = 0
    payload["derivations"] = result.derivations[:-1] + (invalid_maximum,)

    with pytest.raises(
        ValidationError,
        match="selected peak index must equal the running peak at the selected trough",
    ):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    [-1.01, -1.0, 0.01, float("-inf"), float("inf"), float("nan")],
)
def test_serialized_output_rejects_drawdowns_outside_the_positive_price_range(
    value: float,
) -> None:
    result = drawdown((100.0, 80.0), price_kind="adjusted")
    payload = json.loads(result.model_dump_json())
    payload["drawdowns"][1] = value
    payload["maximum_drawdown"] = value
    payload["derivations"][1]["value"] = value
    payload["derivations"][-1]["value"] = value

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_rejects_a_non_decimal_unit() -> None:
    result = drawdown((100.0, 80.0), price_kind="adjusted")
    payload = json.loads(result.model_dump_json())
    payload["unit"] = "unitless"

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_drawdown_refuses_a_positive_ratio_that_rounds_to_total_loss() -> None:
    with pytest.raises(DomainError) as caught:
        drawdown((1.0, 1e-20), price_kind="adjusted")

    assert caught.value.details["violations"][0]["rule"] == "non_finite_result"


@pytest.mark.parametrize(
    "running_peak_indices",
    [
        [-1, 0],
        [0, 2],
        [0, 1],
    ],
)
def test_serialized_output_rejects_invalid_running_peak_indexes(
    running_peak_indices: list[int],
) -> None:
    result = drawdown((100.0, 80.0), price_kind="adjusted")
    payload = json.loads(result.model_dump_json())
    payload["running_peak_indices"] = running_peak_indices

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_rejects_a_running_peak_that_never_established_a_peak() -> None:
    result = drawdown((100.0, 90.0, 80.0), price_kind="adjusted")
    payload = json.loads(result.model_dump_json())
    payload["running_peak_indices"][2] = 1
    payload["derivations"][2]["inputs"][1]["index"] = 1
    payload["derivations"][2]["expression"] = "prices[2] / prices[1] - 1"

    with pytest.raises(ValidationError, match="observations that established a peak"):
        Output.model_validate(payload)


def test_serialized_output_rejects_recovery_index_outside_the_series() -> None:
    result = drawdown((100.0, 80.0), price_kind="adjusted")
    payload = json.loads(result.model_dump_json())
    payload["recovery_index"] = len(result.drawdowns)
    payload["recovered"] = True

    with pytest.raises(
        ValidationError,
        match="recovery index must identify a drawdown observation",
    ):
        Output.model_validate(payload)


def test_serialized_output_requires_the_first_zero_drawdown_as_recovery() -> None:
    result = drawdown((100.0, 80.0, 100.0, 90.0, 110.0), price_kind="adjusted")

    payload = json.loads(result.model_dump_json())
    payload["recovery_index"] = 4
    with pytest.raises(ValidationError, match="first zero drawdown after the trough"):
        Output.model_validate(payload)

    payload = json.loads(result.model_dump_json())
    payload["recovery_index"] = None
    payload["recovered"] = False
    with pytest.raises(ValidationError, match="first zero drawdown after the trough"):
        Output.model_validate(payload)

    unrecovered = drawdown((100.0, 80.0, 90.0), price_kind="adjusted")
    payload = json.loads(unrecovered.model_dump_json())
    payload["recovery_index"] = 2
    payload["recovered"] = True
    with pytest.raises(ValidationError, match="first zero drawdown after the trough"):
        Output.model_validate(payload)


def test_serialized_output_rejects_misaligned_drawdown_timestamps() -> None:
    result = drawdown(
        (100.0, 80.0, 100.0),
        price_kind="adjusted",
        timestamps=(
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
            "2026-01-03T00:00:00Z",
        ),
    )
    payload = json.loads(result.model_dump_json())
    payload["drawdown_timestamps"].pop()

    with pytest.raises(
        ValidationError,
        match="drawdown timestamps must align with drawdown values",
    ):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["peak_timestamp", "trough_timestamp", "recovery_timestamp"],
)
def test_serialized_output_rejects_episode_timestamp_index_mismatches(
    field: str,
) -> None:
    result = drawdown(
        (100.0, 80.0, 100.0),
        price_kind="adjusted",
        timestamps=(
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
            "2026-01-03T00:00:00Z",
        ),
    )
    payload = json.loads(result.model_dump_json())
    payload[field] = "2026-01-04T00:00:00Z"

    with pytest.raises(ValidationError, match=f"{field.removesuffix('_timestamp')} timestamp"):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    ("timestamps", "ordering_status"),
    [
        (None, "verified"),
        (["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"], "unverified"),
    ],
)
def test_serialized_output_rejects_inconsistent_timestamp_ordering_status(
    timestamps: list[str] | None,
    ordering_status: str,
) -> None:
    result = drawdown((100.0, 80.0), price_kind="adjusted")
    payload = json.loads(result.model_dump_json())
    payload["drawdown_timestamps"] = timestamps
    payload["ordering_status"] = ordering_status

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_empty_prices_are_blocked_by_contract_preflight() -> None:
    with pytest.raises(DomainError) as caught:
        preflight(
            COMPONENT_ID,
            prices=(),
            price_kind="adjusted",
            timestamps=None,
            declared_frequency=None,
        )

    assert [
        violation["rule"] for violation in caught.value.details["violations"]
    ] == ["empty_prices"]


def test_result_provenance_and_svg_are_deterministic() -> None:
    first = drawdown((100.0, 120.0, 84.0), price_kind="adjusted")
    second = drawdown((100.0, 120.0, 84.0), price_kind="adjusted")

    assert first.subject_hash == subject_hash(first.component_id)
    assert first.model_dump_json() == second.model_dump_json()
    assert render_svg(first.visualizations[0]) == render_svg(second.visualizations[0])


def test_input_model_is_strict_and_frozen() -> None:
    inputs = Inputs(prices=(100.0,), price_kind=PriceKind.ADJUSTED)
    with pytest.raises(ValidationError):
        inputs.__setattr__("prices", (90.0,))
    with pytest.raises(ValidationError):
        Inputs.model_validate({"prices": [True], "price_kind": "adjusted"})
