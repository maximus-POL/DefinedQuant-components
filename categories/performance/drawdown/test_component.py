"""Evidence and behavior tests for ``dq.performance.drawdown``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from defined_quant import render_svg, subject_hash
from defined_quant.performance.drawdown.component import FORMULA, Inputs, drawdown
from defined_quant.types import NumberFormat, PriceKind, Unit
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
    assert result.trough_index == 0


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
