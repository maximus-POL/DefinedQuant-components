from __future__ import annotations

import pytest
from defined_quant_adapter_dq_native.simple_return.adapter import AdapterFailure, execute


def test_adapter_maps_canonical_input_and_interval_end_timestamps() -> None:
    result = execute(
        {
            "prices": [100.0, 105.0, 102.9],
            "timestamps": [
                "2026-07-24T00:00:00Z",
                "2026-07-27T00:00:00Z",
                "2026-07-28T00:00:00Z",
            ],
        }
    )

    assert result["returns"] == pytest.approx([0.05, -0.02], rel=1e-12, abs=1e-12)
    assert result["return_kind"] == "simple"
    assert result["return_timestamps"] == [
        "2026-07-27T00:00:00Z",
        "2026-07-28T00:00:00Z",
    ]
    assert result["ordering_status"] == "verified"


def test_adapter_preserves_unverified_order_without_timestamps() -> None:
    result = execute({"prices": [100.0, 101.0]})

    assert result["return_timestamps"] is None
    assert result["ordering_status"] == "unverified"


def test_adapter_rejects_undeclared_control_fields() -> None:
    with pytest.raises(AdapterFailure) as caught:
        execute(
            {
                "prices": [100.0, 101.0],
                "backend": "untrusted",
                "fallback": "automatic",
            }
        )

    assert caught.value.code == "invalid_adapter_input"
    assert caught.value.details == {"fields": ["backend", "fallback"]}


def test_adapter_translates_kernel_failure_without_fallback() -> None:
    with pytest.raises(AdapterFailure) as caught:
        execute({"prices": [1e-308, 1e308]})

    assert caught.value.code == "non_finite_result"
    assert caught.value.retry_allowed is False
    assert caught.value.details == {"violation_ids": ["non_finite_result"]}


@pytest.mark.parametrize(
    ("timestamps", "code"),
    [
        (["2026-07-24T00:00:00Z"], "timestamp_length_mismatch"),
        (
            ["2026-07-24T00:00:00Z", "2026-07-24T00:00:00Z"],
            "duplicate_timestamps",
        ),
        (
            ["2026-07-28T00:00:00Z", "2026-07-27T00:00:00Z"],
            "non_increasing_timestamps",
        ),
        (
            ["2026-07-24T00:00:00", "2026-07-25T00:00:00Z"],
            "invalid_timestamps",
        ),
    ],
)
def test_adapter_translates_timestamp_mapping_failures(
    timestamps: list[str],
    code: str,
) -> None:
    with pytest.raises(AdapterFailure) as caught:
        execute({"prices": [100.0, 101.0], "timestamps": timestamps})

    assert caught.value.code == code


def test_adapter_compares_timestamp_instants_not_lexical_text() -> None:
    with pytest.raises(AdapterFailure) as caught:
        execute(
            {
                "prices": [100.0, 101.0],
                "timestamps": [
                    "2024-01-01T00:00:00-01:00",
                    "2024-01-01T00:30:00+01:00",
                ],
            }
        )

    assert caught.value.code == "non_increasing_timestamps"


def test_adapter_detects_nonadjacent_duplicate_instants_before_ordering() -> None:
    with pytest.raises(AdapterFailure) as caught:
        execute(
            {
                "prices": [100.0, 101.0, 102.0],
                "timestamps": [
                    "2026-07-24T00:00:00Z",
                    "2026-07-25T00:00:00Z",
                    "2026-07-24T00:00:00+00:00",
                ],
            }
        )

    assert caught.value.code == "duplicate_timestamps"
