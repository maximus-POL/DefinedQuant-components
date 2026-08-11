"""Executable evidence and contract tests for ``dq.volatility.historical_volatility``."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from defined_quant import preflight, subject_hash
from defined_quant.types import (
    AmbiguousInput,
    Derivation,
    DomainError,
    InputRef,
    OutputRef,
    ReturnKind,
    Unit,
)
from defined_quant.volatility.historical_volatility.component import (
    FORMULA,
    Inputs,
    Output,
    historical_volatility,
)
from pydantic import ValidationError


def _rules(error: DomainError) -> set[str]:
    violations = error.details.get("violations", [])
    assert isinstance(violations, list)
    return {
        str(item["rule"])
        for item in violations
        if isinstance(item, dict) and "rule" in item
    }


def test_contract_exports_are_present() -> None:
    assert Inputs is not None
    assert Output is not None
    assert callable(historical_volatility)


def test_formula_surfaces_match_the_executed_calculation() -> None:
    component_dir = Path(__file__).parent
    contract = json.loads((component_dir / "contract.yaml").read_text(encoding="utf-8"))
    readme = (component_dir / "README.md").read_text(encoding="utf-8")
    result = historical_volatility(
        (-0.01, 0.01),
        annualization_factor=4.0,
        return_kind=ReturnKind.LOG,
    )

    expected = (
        "a = r₀; cᵢ = rᵢ − a; dᵢ = cᵢ if every cᵢ is finite, otherwise dᵢ = rᵢ; "
        "s = maxᵢ |dᵢ|; if s = 0, σ̂ = 0; otherwise μ = fsum(dᵢ / s) / n, h₀ = 0, "
        "hᵢ₊₁ = hypot(hᵢ, dᵢ / s − μ), and σ̂ = s × (hₙ / sqrt(n − 1)); "
        "σ̂annual = σ̂ × sqrt(A)"
    )
    assert FORMULA == expected
    assert contract["display"]["formula"] == FORMULA
    assert f"`{FORMULA}`" in readme
    assert FORMULA in result.transformations[0]


def test_evidence_inv_001() -> None:
    result = historical_volatility(
        (-0.01, 0.01),
        annualization_factor=4.0,
        return_kind=ReturnKind.LOG,
    )

    assert result.periodic_volatility == pytest.approx(math.sqrt(0.0002))
    assert result.annualized_volatility == pytest.approx(2.0 * math.sqrt(0.0002))


def test_evidence_inv_002() -> None:
    result = historical_volatility(
        (0.025, 0.025, 0.025),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )

    assert result.periodic_volatility == 0.0
    assert result.annualized_volatility == 0.0


def test_evidence_inv_003() -> None:
    returns = (-0.03, -0.01, 0.02, 0.04)
    shifted = tuple(value + 0.5 for value in returns)
    original = historical_volatility(
        returns,
        annualization_factor=12.0,
        return_kind=ReturnKind.LOG,
    )
    translated = historical_volatility(
        shifted,
        annualization_factor=12.0,
        return_kind=ReturnKind.LOG,
    )

    assert translated.periodic_volatility == pytest.approx(
        original.periodic_volatility,
        rel=1e-14,
        abs=1e-14,
    )
    assert translated.annualized_volatility == pytest.approx(
        original.annualized_volatility,
        rel=1e-14,
        abs=1e-14,
    )


def test_evidence_inv_004() -> None:
    returns = (-0.03, -0.01, 0.02, 0.04)
    scale = 7.0
    original = historical_volatility(
        returns,
        annualization_factor=12.0,
        return_kind=ReturnKind.LOG,
    )
    scaled = historical_volatility(
        tuple(scale * value for value in returns),
        annualization_factor=12.0,
        return_kind=ReturnKind.LOG,
    )

    assert scaled.periodic_volatility == pytest.approx(
        scale * original.periodic_volatility
    )
    assert scaled.annualized_volatility == pytest.approx(
        scale * original.annualized_volatility
    )


def test_evidence_inv_005() -> None:
    returns = (-0.01, 0.0, 0.02)
    result = historical_volatility(
        returns,
        annualization_factor=12.0,
        return_kind=ReturnKind.LOG,
    )
    return_inputs = tuple(
        InputRef(field="returns", index=index) for index in range(len(returns))
    )

    assert result.derivations == (
        Derivation(
            output=OutputRef(field="periodic_volatility", index=0),
            inputs=return_inputs,
            expression="centered_scaled_hypot_sample_stdev_n_minus_1(returns)",
            value=result.periodic_volatility,
        ),
        Derivation(
            output=OutputRef(field="annualized_volatility", index=0),
            inputs=return_inputs + (InputRef(field="annualization_factor", index=0),),
            expression=(
                "centered_scaled_hypot_sample_stdev_n_minus_1(returns) "
                "* sqrt(annualization_factor)"
            ),
            value=result.annualized_volatility,
        ),
    )


def test_models_are_frozen_and_reject_extra_fields() -> None:
    inputs = Inputs(
        returns=(-0.01, 0.01),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )

    with pytest.raises(ValidationError):
        inputs.__setattr__("annualization_factor", 12.0)
    with pytest.raises(ValidationError):
        Inputs.model_validate(
            {
                "returns": [-0.01, 0.01],
                "annualization_factor": 252.0,
                "return_kind": "log",
                "undeclared": True,
            }
        )


def test_numeric_inputs_reject_boolean_and_numeric_string_coercion() -> None:
    for invalid_return in (True, "0.01"):
        with pytest.raises(ValidationError):
            Inputs.model_validate(
                {
                    "returns": [invalid_return, 0.01],
                    "annualization_factor": 252.0,
                    "return_kind": "log",
                }
            )
    for invalid_factor in (True, "252.0"):
        with pytest.raises(ValidationError):
            Inputs.model_validate(
                {
                    "returns": [-0.01, 0.01],
                    "annualization_factor": invalid_factor,
                    "return_kind": "log",
                }
            )

    with pytest.raises(ValidationError):
        historical_volatility(
            (False, 0.01),
            annualization_factor=252.0,
            return_kind=ReturnKind.LOG,
        )
    with pytest.raises(ValidationError):
        historical_volatility(
            (-0.01, 0.01),
            annualization_factor=True,
            return_kind=ReturnKind.LOG,
        )

    validated = Inputs(
        returns=(-1, 1),
        annualization_factor=252,
        return_kind=ReturnKind.LOG,
    )
    assert validated.returns == (-1.0, 1.0)
    assert validated.annualization_factor == 252.0


def test_output_provenance_and_subject_binding() -> None:
    result = historical_volatility(
        (-0.01, 0.01),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )

    assert result.component_id == "dq.volatility.historical_volatility"
    assert result.version == "0.1.0"
    assert result.subject_hash == subject_hash(result.component_id)
    assert len(result.subject_hash) == 64
    assert result.unit is Unit.VOLATILITY
    assert result.return_kind is ReturnKind.LOG
    assert result.sample_size == 2
    assert result.degrees_of_freedom_adjustment == 1


@pytest.mark.parametrize("sample_size", [2, 29])
def test_small_valid_samples_warn(sample_size: int) -> None:
    result = historical_volatility(
        tuple(0.0 for _ in range(sample_size)),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )

    assert any(message.startswith("small_sample:") for message in result.warnings)


def test_warning_clears_at_thirty_returns() -> None:
    result = historical_volatility(
        tuple(0.0 for _ in range(30)),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )

    assert not any(message.startswith("small_sample:") for message in result.warnings)


def test_permanent_context_is_disclosed_without_warning() -> None:
    result = historical_volatility(
        tuple(0.0 for _ in range(30)),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )

    assert result.warnings == ()
    assert result.disclosures == (
        "annualization_factor_explicit: The annualization factor was supplied explicitly; "
        "no default was inferred.",
        "calendar_frequency_not_inferred: No timestamps, observation frequency, market "
        "calendar, or gap policy was inferred.",
    )
    assert result.visualizations == ()


def test_missing_convention_inputs_are_explicit_questions() -> None:
    with pytest.raises(AmbiguousInput) as missing_factor:
        preflight(
            "dq.volatility.historical_volatility",
            returns=(-0.01, 0.01),
            return_kind=ReturnKind.LOG,
        )
    assert {
        item["field"] for item in missing_factor.value.details["questions"]
    } == {"annualization_factor"}

    with pytest.raises(AmbiguousInput) as missing_kind:
        preflight(
            "dq.volatility.historical_volatility",
            returns=(-0.01, 0.01),
            annualization_factor=252.0,
        )
    assert {item["field"] for item in missing_kind.value.details["questions"]} == {
        "return_kind"
    }


def test_simple_returns_are_refused() -> None:
    with pytest.raises(DomainError) as caught:
        historical_volatility(
            (-0.01, 0.01),
            annualization_factor=252.0,
            return_kind=ReturnKind.SIMPLE,
        )

    assert _rules(caught.value) == {"unsupported_return_kind"}


@pytest.mark.parametrize(
    ("annualization_factor", "expected_rule"),
    [
        (0.0, "non_positive_annualization_factor"),
        (-1.0, "non_positive_annualization_factor"),
        (math.nan, "non_finite_annualization_factor"),
        (math.inf, "non_finite_annualization_factor"),
        (-math.inf, "non_finite_annualization_factor"),
    ],
)
def test_invalid_annualization_factors_are_refused(
    annualization_factor: float,
    expected_rule: str,
) -> None:
    with pytest.raises(DomainError) as caught:
        historical_volatility(
            (-0.01, 0.01),
            annualization_factor=annualization_factor,
            return_kind=ReturnKind.LOG,
        )

    assert _rules(caught.value) == {expected_rule}


@pytest.mark.parametrize("bad_return", [math.nan, math.inf, -math.inf])
def test_non_finite_returns_are_refused(bad_return: float) -> None:
    with pytest.raises(DomainError) as caught:
        historical_volatility(
            (0.0, bad_return),
            annualization_factor=252.0,
            return_kind=ReturnKind.LOG,
        )

    assert _rules(caught.value) == {"non_finite_returns"}


@pytest.mark.parametrize(
    ("returns", "annualization_factor"),
    [
        ((-1.3e308, 1.3e308), 1.0),
        ((-1.0e308, 1.0e308), 4.0),
        ((0.0, 5e-324), 5e-324),
    ],
)
def test_unrepresentable_results_are_refused(
    returns: tuple[float, ...],
    annualization_factor: float,
) -> None:
    with pytest.raises(DomainError) as caught:
        historical_volatility(
            returns,
            annualization_factor=annualization_factor,
            return_kind=ReturnKind.LOG,
        )

    assert _rules(caught.value) == {"non_finite_result"}


def test_exact_zero_dispersion_is_valid_with_a_subnormal_factor() -> None:
    result = historical_volatility(
        (0.0, 0.0),
        annualization_factor=5e-324,
        return_kind=ReturnKind.LOG,
    )

    assert result.periodic_volatility == 0.0
    assert result.annualized_volatility == 0.0
    assert result.derivations[0].expression == (
        "zero_dispersion_sample_stdev_n_minus_1(returns)"
    )
    assert result.derivations[1].expression == (
        "zero_dispersion_sample_stdev_n_minus_1(returns) "
        "* sqrt(annualization_factor)"
    )


def test_large_common_offset_preserves_adjacent_float_dispersion() -> None:
    first = 1e308
    second = math.nextafter(first, math.inf)
    expected = (second - first) / math.sqrt(2.0)

    result = historical_volatility(
        (first, second),
        annualization_factor=1.0,
        return_kind=ReturnKind.LOG,
    )

    assert result.periodic_volatility == pytest.approx(expected, rel=1e-15)
    assert result.annualized_volatility == pytest.approx(expected, rel=1e-15)
    assert result.derivations[0].expression == (
        "centered_scaled_hypot_sample_stdev_n_minus_1(returns)"
    )
    assert result.derivations[1].expression == (
        "centered_scaled_hypot_sample_stdev_n_minus_1(returns) "
        "* sqrt(annualization_factor)"
    )


def test_opposite_sign_extremes_record_the_raw_fallback_branch() -> None:
    expected = 1e308 * math.sqrt(2.0)

    result = historical_volatility(
        (-1e308, 1e308),
        annualization_factor=1.0,
        return_kind=ReturnKind.LOG,
    )

    assert result.periodic_volatility == pytest.approx(expected, rel=1e-15)
    assert result.annualized_volatility == pytest.approx(expected, rel=1e-15)
    assert result.derivations[0].expression == (
        "raw_fallback_scaled_hypot_sample_stdev_n_minus_1(returns)"
    )
    assert result.derivations[1].expression == (
        "raw_fallback_scaled_hypot_sample_stdev_n_minus_1(returns) "
        "* sqrt(annualization_factor)"
    )


def test_output_rejects_incomplete_or_reindexed_lineage() -> None:
    result = historical_volatility(
        (-0.01, 0.01),
        annualization_factor=4.0,
        return_kind=ReturnKind.LOG,
    )
    payload = result.model_dump(mode="python")
    payload["derivations"][0]["output"]["index"] = 1
    with pytest.raises(ValidationError):
        Output.model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["derivations"][1]["inputs"] = payload["derivations"][1]["inputs"][:-1]
    with pytest.raises(ValidationError, match="every return and the factor"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["derivations"][0]["expression"] = (
        "zero_dispersion_sample_stdev_n_minus_1(returns)"
    )
    with pytest.raises(ValidationError, match="zero-dispersion derivation branch"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["derivations"][1]["expression"] = (
        "raw_fallback_scaled_hypot_sample_stdev_n_minus_1(returns) "
        "* sqrt(annualization_factor)"
    )
    with pytest.raises(ValidationError, match="annualized volatility derivation"):
        Output.model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["annualized_volatility"] = 1.0
    payload["derivations"][1]["value"] = 1.0
    with pytest.raises(ValidationError, match="must equal periodic volatility"):
        Output.model_validate(payload)


def test_result_is_deterministic() -> None:
    first = historical_volatility(
        (-0.01, 0.0, 0.02),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )
    second = historical_volatility(
        (-0.01, 0.0, 0.02),
        annualization_factor=252.0,
        return_kind=ReturnKind.LOG,
    )

    assert first.model_dump_json() == second.model_dump_json()
