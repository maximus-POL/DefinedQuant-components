"""Evidence tests for ``dq.volatility.rolling_historical_volatility``."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from defined_quant import render_svg, subject_hash
from defined_quant.types import NumberFormat, ReturnKind, Unit
from defined_quant.volatility.rolling_historical_volatility.component import (
    FORMULA,
    Inputs,
    Output,
    rolling_historical_volatility,
)
from pydantic import ValidationError


def test_formula_surfaces_match() -> None:
    component_dir = Path(__file__).parent
    contract = json.loads((component_dir / "contract.yaml").read_text())
    readme = (component_dir / "README.md").read_text()
    result = rolling_historical_volatility(
        (0.01, -0.01),
        window_length=2,
        annualization_factor=12.0,
        return_kind="log",
    )

    assert contract["display"]["formula"] == FORMULA
    assert f"`{FORMULA}`" in readme
    assert FORMULA in result.transformations[0]
    assert FORMULA in (result.visualizations[0].caption or "")


def test_evidence_inv_001() -> None:
    result = rolling_historical_volatility(
        (0.01, 0.01, 0.01, 0.01),
        window_length=3,
        annualization_factor=12.0,
        return_kind="log",
    )

    assert result.periodic_volatility == (0.0, 0.0)
    assert result.annualized_volatility == (0.0, 0.0)


def test_evidence_inv_002() -> None:
    returns = (0.01, -0.01, 0.03, 0.01, -0.02)
    result = rolling_historical_volatility(
        returns,
        window_length=3,
        annualization_factor=12.0,
        return_kind="log",
    )

    assert len(result.periodic_volatility) == len(returns) - 3 + 1
    assert result.annualized_volatility == pytest.approx(
        tuple(value * math.sqrt(12.0) for value in result.periodic_volatility)
    )
    assert len(result.derivations) == len(result.periodic_volatility) * 2


def test_evidence_inv_003() -> None:
    result = rolling_historical_volatility(
        (0.01, -0.01, 0.03, 0.01),
        window_length=3,
        annualization_factor=12.0,
        return_kind="log",
    )
    chart = result.visualizations[0]

    assert chart.series[0].values == result.annualized_volatility
    assert chart.y_axis.unit is Unit.VOLATILITY
    assert chart.y_axis.number_format is NumberFormat.PERCENT


def test_result_provenance_and_svg_are_deterministic() -> None:
    first = rolling_historical_volatility(
        (0.01, -0.01, 0.03),
        window_length=3,
        annualization_factor=12.0,
        return_kind="log",
    )
    second = rolling_historical_volatility(
        (0.01, -0.01, 0.03),
        window_length=3,
        annualization_factor=12.0,
        return_kind="log",
    )

    assert first.subject_hash == subject_hash(first.component_id)
    assert first.model_dump_json() == second.model_dump_json()
    assert render_svg(first.visualizations[0]) == render_svg(second.visualizations[0])


def test_serialized_output_rejects_a_non_volatility_unit() -> None:
    result = rolling_historical_volatility(
        (0.01, -0.01),
        window_length=2,
        annualization_factor=12.0,
        return_kind="log",
    )
    payload = json.loads(result.model_dump_json())
    payload["unit"] = "decimal"

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "derivation_index"),
    [
        ("periodic_volatility", 0),
        ("annualized_volatility", 1),
    ],
)
def test_serialized_output_rejects_negative_volatility_values(
    field: str,
    derivation_index: int,
) -> None:
    result = rolling_historical_volatility(
        (0.01, -0.01),
        window_length=2,
        annualization_factor=4.0,
        return_kind="log",
    )
    payload = result.model_dump(mode="json")
    payload[field][0] = -payload[field][0]
    payload["derivations"][derivation_index]["value"] = payload[field][0]

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Output.model_validate(payload)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
@pytest.mark.parametrize("field", ["periodic_volatility", "annualized_volatility"])
def test_serialized_output_rejects_non_finite_volatility_values(
    field: str,
    value: float,
) -> None:
    result = rolling_historical_volatility(
        (0.01, -0.01),
        window_length=2,
        annualization_factor=4.0,
        return_kind="log",
    )
    payload = result.model_dump(mode="json")
    payload[field][0] = value

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_serialized_output_rejects_annualization_underflow() -> None:
    result = rolling_historical_volatility(
        (-0.01, 0.01),
        window_length=2,
        annualization_factor=1.0,
        return_kind="log",
    )
    payload = json.loads(result.model_dump_json())
    payload["periodic_volatility"][0] = 5e-324
    payload["annualized_volatility"][0] = 0.0
    payload["annualization_factor"] = 5e-324
    payload["derivations"][0]["value"] = 5e-324
    payload["derivations"][1]["value"] = 0.0

    with pytest.raises(ValidationError, match="must use square-root scaling"):
        Output.model_validate(payload)


def test_serialized_output_rejects_misaligned_volatility_timestamps() -> None:
    result = rolling_historical_volatility(
        (0.01, -0.01, 0.03),
        window_length=2,
        annualization_factor=12.0,
        return_kind="log",
        timestamps=(
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
            "2026-01-03T00:00:00Z",
        ),
    )
    payload = json.loads(result.model_dump_json())
    payload["volatility_timestamps"].pop()

    with pytest.raises(
        ValidationError,
        match="volatility timestamps must align with both volatility series",
    ):
        Output.model_validate(payload)


@pytest.mark.parametrize(
    ("timestamps", "ordering_status"),
    [
        (None, "verified"),
        (["2026-01-02T00:00:00Z"], "unverified"),
    ],
)
def test_serialized_output_rejects_inconsistent_timestamp_ordering_status(
    timestamps: list[str] | None,
    ordering_status: str,
) -> None:
    result = rolling_historical_volatility(
        (0.01, -0.01),
        window_length=2,
        annualization_factor=12.0,
        return_kind="log",
    )
    payload = json.loads(result.model_dump_json())
    payload["volatility_timestamps"] = timestamps
    payload["ordering_status"] = ordering_status

    with pytest.raises(ValidationError):
        Output.model_validate(payload)


def test_input_model_is_strict_and_frozen() -> None:
    inputs = Inputs(
        returns=(0.01, -0.01),
        window_length=2,
        annualization_factor=12.0,
        return_kind=ReturnKind.LOG,
    )
    with pytest.raises(ValidationError):
        inputs.__setattr__("window_length", 3)
    with pytest.raises(ValidationError):
        Inputs.model_validate(
            {
                "returns": ["0.01", -0.01],
                "window_length": 2,
                "annualization_factor": 12.0,
                "return_kind": "log",
            }
        )
