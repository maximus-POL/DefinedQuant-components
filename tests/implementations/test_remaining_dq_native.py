from __future__ import annotations

import math

import pytest
from defined_quant_adapter_dq_native._boundary import CalculationError
from defined_quant_adapter_dq_native.drawdown.kernel import calculate_drawdown
from defined_quant_adapter_dq_native.log_return.kernel import calculate_log_returns
from defined_quant_adapter_dq_native.monthly_calendar_matrix.kernel import align_monthly_returns
from defined_quant_adapter_dq_native.rebased_price_index.kernel import rebase_prices
from defined_quant_adapter_dq_native.statistics.kernel import (
    annualize_square_root,
    annualize_square_root_series,
    calculate_rolling_sample_standard_deviations,
    calculate_sample_standard_deviation,
)


def test_log_return_preserves_legacy_branch_order_and_extreme_ratios() -> None:
    assert calculate_log_returns((100.0, 105.0, 102.9)) == pytest.approx(
        (0.04879016416943201, -0.020202707317519393),
        rel=1e-15,
        abs=0.0,
    )
    assert calculate_log_returns((1e-308, 1e308, 1e-308)) == pytest.approx(
        (1418.3924172843322, -1418.3924172843322),
        rel=1e-15,
    )
    assert calculate_log_returns((1e16, 1.0000000000000002e16)) == pytest.approx(
        (1.9999999999999997e-16,),
        rel=1e-15,
        abs=0.0,
    )


@pytest.mark.parametrize(
    ("prices", "code"),
    [
        ((100.0,), "insufficient_prices"),
        ((100.0, 0.0), "non_positive_prices"),
        ((100.0, math.inf), "non_finite_prices"),
        ((100.0, True), "invalid_prices"),
    ],
)
def test_log_return_kernel_has_closed_failures(
    prices: tuple[object, ...],
    code: str,
) -> None:
    with pytest.raises(CalculationError) as caught:
        calculate_log_returns(prices)  # type: ignore[arg-type]
    assert caught.value.code == code


def test_monthly_calendar_alignment_preserves_values_and_crosses_years() -> None:
    returns, months = align_monthly_returns(
        (-0.2, 0.1, -0.1),
        ("2022-11", "2022-12", "2023-01", "2023-02"),
    )
    assert returns == (-0.2, 0.1, -0.1)
    assert months == ("2022-12", "2023-01", "2023-02")


@pytest.mark.parametrize(
    ("months", "code"),
    [
        (("2023-01", "2023-03"), "non_consecutive_months"),
        (("2023-01", "2023-01"), "duplicate_months"),
        (("2023-02", "2023-01"), "non_increasing_months"),
        (("2023-00", "2023-01"), "invalid_month_labels"),
    ],
)
def test_monthly_calendar_alignment_refuses_invalid_calendars(
    months: tuple[str, str],
    code: str,
) -> None:
    with pytest.raises(CalculationError) as caught:
        align_monthly_returns((0.1,), months)
    assert caught.value.code == code


def test_rebase_preserves_explicit_base_and_scale_invariance() -> None:
    prices = (80.0, 100.0, 60.0, 120.0)
    result = rebase_prices(prices, base_index=1, base_value=100.0)
    scaled = rebase_prices(
        tuple(value * 137.0 for value in prices),
        base_index=1,
        base_value=100.0,
    )
    assert result == pytest.approx((80.0, 100.0, 60.0, 120.0))
    assert result[1] == 100.0
    assert scaled == pytest.approx(result, rel=1e-12, abs=1e-12)


def test_rebase_refuses_out_of_range_and_unrepresentable_values() -> None:
    with pytest.raises(CalculationError) as out_of_range:
        rebase_prices((100.0, 110.0), base_index=2, base_value=100.0)
    assert out_of_range.value.code == "base_index_out_of_range"
    with pytest.raises(CalculationError) as unrepresentable:
        rebase_prices((1e-308, 1e308), base_index=0, base_value=1e308)
    assert unrepresentable.value.code == "non_finite_result"


def test_drawdown_preserves_episode_ties_and_recovery_rules() -> None:
    result = calculate_drawdown((100.0, 120.0, 90.0, 84.0, 108.0, 120.0))
    assert result.drawdowns == pytest.approx((0.0, 0.0, -0.25, -0.3, -0.1, 0.0))
    assert result.maximum_drawdown == pytest.approx(-0.3)
    assert result.running_peak_indices == (0, 1, 1, 1, 1, 5)
    assert (result.peak_index, result.trough_index, result.recovery_index) == (1, 3, 5)

    equal_high = calculate_drawdown((100.0, 100.0, 80.0))
    assert equal_high.running_peak_indices == (0, 1, 1)
    assert (equal_high.peak_index, equal_high.trough_index) == (1, 2)


def test_sample_standard_deviation_preserves_numerical_boundaries() -> None:
    assert calculate_sample_standard_deviation((-0.01, 0.01)) == pytest.approx(
        0.01414213562373095,
        rel=1e-15,
    )
    assert calculate_sample_standard_deviation((1e308, 1.0000000000000002e308)) == pytest.approx(
        1.4112722170374585e292,
        rel=1e-15,
    )
    assert calculate_sample_standard_deviation((0.01, 0.01, 0.01)) == 0.0


def test_rolling_standard_deviation_matches_scalar_windows() -> None:
    values = (0.01, -0.02, 0.03, 0.04)
    rolling = calculate_rolling_sample_standard_deviations(values, window_length=3)
    assert rolling == pytest.approx(
        (
            calculate_sample_standard_deviation(values[:3]),
            calculate_sample_standard_deviation(values[1:]),
        ),
        rel=1e-15,
    )
    with pytest.raises(CalculationError) as caught:
        calculate_rolling_sample_standard_deviations(values, window_length=5)
    assert caught.value.code == "window_exceeds_values"


def test_square_root_annualization_preserves_zero_and_rejects_lost_positive_values() -> None:
    assert annualize_square_root(0.01414213562373095, 4.0) == pytest.approx(
        0.0282842712474619,
        rel=1e-15,
    )
    assert annualize_square_root(0.0, 5e-324) == 0.0
    assert annualize_square_root_series((0.01, 0.02), 4.0) == (0.02, 0.04)
    with pytest.raises(CalculationError) as caught:
        annualize_square_root(5e-324, 5e-324)
    assert caught.value.code == "non_finite_result"
