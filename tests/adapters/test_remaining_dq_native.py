from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from defined_quant_adapter_dq_native._boundary import AdapterFailure
from defined_quant_adapter_dq_native.drawdown.adapter import execute as drawdown
from defined_quant_adapter_dq_native.log_return.adapter import execute as log_return
from defined_quant_adapter_dq_native.monthly_calendar_matrix.adapter import execute as monthly
from defined_quant_adapter_dq_native.rebased_price_index.adapter import execute as rebase
from defined_quant_adapter_dq_native.statistics.rolling_sample_standard_deviation import (
    execute as rolling_standard_deviation,
)
from defined_quant_adapter_dq_native.statistics.sample_standard_deviation import (
    execute as sample_standard_deviation,
)
from defined_quant_adapter_dq_native.statistics.square_root_annualize import (
    execute as annualize,
)
from defined_quant_adapter_dq_native.statistics.square_root_annualize_series import (
    execute as annualize_series,
)

Adapter = Callable[[Mapping[str, object]], dict[str, object]]


def test_log_return_adapter_maps_interval_end_timestamps() -> None:
    result = log_return(
        {
            "prices": [100.0, 101.0, 102.0],
            "timestamps": [
                "2026-07-24T00:00:00Z",
                "2026-07-27T00:00:00Z",
                "2026-07-28T00:00:00Z",
            ],
        }
    )
    assert result["returns"] == pytest.approx(
        [0.009950330853168083, 0.00985229644301164],
        rel=1e-15,
    )
    assert result["return_kind"] == "log"
    assert result["return_timestamps"] == [
        "2026-07-27T00:00:00Z",
        "2026-07-28T00:00:00Z",
    ]
    assert result["ordering_status"] == "verified"


def test_monthly_adapter_maps_calendar_output_without_recalculating_returns() -> None:
    result = monthly(
        {
            "returns": [-0.2, 0.1],
            "observation_months": ["2022-11", "2022-12", "2023-01"],
        }
    )
    assert result == {
        "monthly_returns": [-0.2, 0.1],
        "return_months": ["2022-12", "2023-01"],
        "gap_check": "verified_consecutive_months",
    }


def test_rebase_and_drawdown_adapters_map_full_timestamp_outputs() -> None:
    timestamps = ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]
    rebased = rebase(
        {
            "prices": [100.0, 80.0],
            "base_index": 0,
            "base_value": 100.0,
            "timestamps": timestamps,
        }
    )
    assert rebased == {
        "index_values": [100.0, 80.0],
        "base_index": 0,
        "base_value": 100.0,
        "index_timestamps": timestamps,
        "ordering_status": "verified",
    }

    episode = drawdown({"prices": [100.0, 80.0], "timestamps": timestamps})
    assert episode["drawdown_timestamps"] == timestamps
    assert episode["peak_timestamp"] == timestamps[0]
    assert episode["trough_timestamp"] == timestamps[1]
    assert episode["recovery_timestamp"] is None
    assert episode["recovered"] is False


def test_statistical_adapters_map_atomic_outputs() -> None:
    scalar = sample_standard_deviation({"values": [-0.01, 0.01]})
    assert scalar["standard_deviation"] == pytest.approx(0.01414213562373095)
    assert scalar["sample_size"] == 2
    assert scalar["degrees_of_freedom_adjustment"] == 1

    scaled = annualize(
        {"periodic_value": scalar["standard_deviation"], "annualization_factor": 4.0}
    )
    assert scaled["annualized_value"] == pytest.approx(0.0282842712474619)

    rolling = rolling_standard_deviation(
        {
            "values": [0.01, -0.02, 0.03, 0.04],
            "window_length": 3,
            "timestamps": [
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
                "2026-01-03T00:00:00Z",
                "2026-01-04T00:00:00Z",
            ],
        }
    )
    assert rolling["window_end_timestamps"] == [
        "2026-01-03T00:00:00Z",
        "2026-01-04T00:00:00Z",
    ]
    assert rolling["ordering_status"] == "verified"
    series = annualize_series(
        {
            "periodic_values": rolling["standard_deviations"],
            "annualization_factor": 4.0,
        }
    )
    assert series["annualized_values"] == pytest.approx(
        [value * 2.0 for value in rolling["standard_deviations"]]
    )


@pytest.mark.parametrize(
    ("adapter", "payload"),
    [
        (log_return, {"prices": [100.0, 101.0]}),
        (monthly, {"returns": [0.01], "observation_months": ["2026-01", "2026-02"]}),
        (rebase, {"prices": [100.0], "base_index": 0, "base_value": 100.0}),
        (drawdown, {"prices": [100.0]}),
        (sample_standard_deviation, {"values": [0.0, 0.1]}),
        (annualize, {"periodic_value": 0.1, "annualization_factor": 252.0}),
        (
            rolling_standard_deviation,
            {"values": [0.0, 0.1], "window_length": 2},
        ),
        (annualize_series, {"periodic_values": [0.1], "annualization_factor": 252.0}),
    ],
)
def test_adapters_refuse_undeclared_resolution_controls(
    adapter: Adapter,
    payload: dict[str, object],
) -> None:
    payload["fallback"] = "automatic"
    with pytest.raises(AdapterFailure) as caught:
        adapter(payload)
    assert caught.value.code == "invalid_adapter_input"
    assert caught.value.details == {"fields": ["fallback"]}


def test_adapter_failures_are_closed_and_do_not_fallback() -> None:
    with pytest.raises(AdapterFailure) as caught:
        monthly(
            {
                "returns": [0.1],
                "observation_months": ["2026-01", "2026-03"],
            }
        )
    assert caught.value.code == "non_consecutive_months"
    assert caught.value.details == {"violation_ids": ["non_consecutive_months"]}
    assert caught.value.retry_allowed is False
