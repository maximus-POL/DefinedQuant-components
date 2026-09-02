from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from defined_quant_adapter_dq_native.simple_return.adapter import AdapterFailure, execute
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_PATH = ROOT / "registry/capabilities/returns/simple/capability.yaml"
CONFORMANCE_PATH = ROOT / "registry/capabilities/returns/simple/conformance.yaml"


def _load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _materialize(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$binary64"}:
            encoded = value["$binary64"]
            assert isinstance(encoded, str)
            return float.fromhex(encoded)
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def test_dq_native_passes_authored_capability_cases() -> None:
    capability = _load(CAPABILITY_PATH)
    suite = _load(CONFORMANCE_PATH)
    output_validator = Draft202012Validator(
        capability["output_schema"],
        format_checker=FormatChecker(),
    )

    for case in suite["cases"]:
        expected = case["expected"]
        case_input = _materialize(case["input"])
        if expected["outcome"] == "failure":
            with pytest.raises(AdapterFailure) as caught:
                execute(case_input)
            assert caught.value.code == expected["code"], case["id"]
            continue

        actual = execute(case_input)
        output_validator.validate(actual)
        tolerance = expected["tolerance"]
        expected_output = expected["output"]
        assert actual["returns"] == pytest.approx(
            expected_output["returns"],
            rel=tolerance["relative"],
            abs=tolerance["absolute"],
        ), case["id"]
        assert {key: value for key, value in actual.items() if key != "returns"} == {
            key: value for key, value in expected_output.items() if key != "returns"
        }, case["id"]
