from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "adapters/dq_native"


def test_dq_native_adapter_is_an_independent_distribution() -> None:
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["name"] == "defined-quant-adapter-dq-native"
    assert project["project"]["version"] == "1.0.0"
    assert project["project"]["dependencies"] == []
    assert project["project"]["entry-points"]["defined_quant.adapters"] == {
        "dq_native_drawdown": "defined_quant_adapter_dq_native.drawdown.adapter:execute",
        "dq_native_log_return": "defined_quant_adapter_dq_native.log_return.adapter:execute",
        "dq_native_monthly_calendar_matrix": (
            "defined_quant_adapter_dq_native.monthly_calendar_matrix.adapter:execute"
        ),
        "dq_native_rebased_price_index": (
            "defined_quant_adapter_dq_native.rebased_price_index.adapter:execute"
        ),
        "dq_native_rolling_sample_standard_deviation": (
            "defined_quant_adapter_dq_native.statistics."
            "rolling_sample_standard_deviation:execute"
        ),
        "dq_native_sample_standard_deviation": (
            "defined_quant_adapter_dq_native.statistics.sample_standard_deviation:execute"
        ),
        "dq_native_simple_return": (
            "defined_quant_adapter_dq_native.simple_return.adapter:execute"
        ),
        "dq_native_square_root_annualize": (
            "defined_quant_adapter_dq_native.statistics.square_root_annualize:execute"
        ),
        "dq_native_square_root_annualize_series": (
            "defined_quant_adapter_dq_native.statistics.square_root_annualize_series:execute"
        ),
    }


def test_core_wheel_does_not_include_or_depend_on_the_adapter() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = project["project"]
    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert all("defined-quant-adapter" not in item for item in core["dependencies"])
    assert "adapters" not in wheel["only-include"]
