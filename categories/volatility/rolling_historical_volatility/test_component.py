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
