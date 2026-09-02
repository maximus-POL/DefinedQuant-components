from __future__ import annotations

import math

import pytest
from defined_quant_adapter_dq_native.simple_return.kernel import (
    SimpleReturnCalculationError,
    calculate_simple_returns,
)


def test_dq_native_known_answer() -> None:
    result = calculate_simple_returns((100.0, 105.0, 102.9))

    assert result == pytest.approx((0.05, -0.02), rel=1e-12, abs=1e-12)


def test_dq_native_preserves_close_price_precision() -> None:
    result = calculate_simple_returns((1e16, 1.0000000000000002e16))

    assert result == pytest.approx((2e-16,), rel=1e-12, abs=0.0)


@pytest.mark.parametrize(
    ("prices", "code"),
    [
        ((100.0,), "insufficient_prices"),
        ((100.0, 0.0), "non_positive_prices"),
        ((100.0, -1.0), "non_positive_prices"),
        ((100.0, math.nan), "non_finite_prices"),
        ((100.0, math.inf), "non_finite_prices"),
        ((100.0, True), "invalid_prices"),
        ((100.0, "101.0"), "invalid_prices"),
    ],
)
def test_dq_native_uses_closed_input_failures(
    prices: tuple[object, ...],
    code: str,
) -> None:
    with pytest.raises(SimpleReturnCalculationError) as caught:
        calculate_simple_returns(prices)  # type: ignore[arg-type]

    assert caught.value.code == code


@pytest.mark.parametrize("prices", [(1e-308, 1e308), (1e308, 1e-308)])
def test_dq_native_refuses_unrepresentable_results(prices: tuple[float, float]) -> None:
    with pytest.raises(SimpleReturnCalculationError) as caught:
        calculate_simple_returns(prices)

    assert caught.value.code == "non_finite_result"


def test_dq_native_invariants() -> None:
    prices = (80.0, 84.0, 79.8, 91.77)
    returns = calculate_simple_returns(prices)
    scaled = calculate_simple_returns(tuple(value * 137.0 for value in prices))

    assert math.prod(1.0 + value for value in returns) == pytest.approx(
        prices[-1] / prices[0],
        rel=1e-12,
        abs=1e-12,
    )
    assert scaled == pytest.approx(returns, rel=1e-12, abs=1e-12)
    assert calculate_simple_returns((42.0, 42.0, 42.0)) == (0.0, 0.0)
