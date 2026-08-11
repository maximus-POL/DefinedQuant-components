"""Closed fixture materialization and execution for component evidence records."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

import yaml  # type: ignore[import-untyped]
from defined_quant import component_models, component_record, load_component, subject_hash
from defined_quant.types import DQError
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    InterpretationMethod,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProvenanceStatus,
    SourceKind,
)
from pydantic import BaseModel, ValidationError

from authoring.input_validation import (
    expected_input_validation_issues,
    input_validation_issues,
)

EXECUTABLE_EVIDENCE_SECTIONS = ("known_answers", "boundary_cases", "cross_checks")


def generated_item_name(section: str, record_id: str) -> str:
    prefixes = {
        "known_answers": "known_answer",
        "boundary_cases": "boundary_case",
        "cross_checks": "cross_check",
        "agent_cases": "agent_case",
    }
    try:
        prefix = prefixes[section]
    except KeyError as exc:
        raise ValueError(f"unsupported executable evidence section: {section}") from exc
    return f"{prefix}_{record_id}"


def _invalid_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("raw non-finite numbers are forbidden; use a tagged $float fixture")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_non_finite(item)


def load_evidence(path: Path) -> dict[str, Any]:
    """Load JSON-compatible YAML while refusing raw NaN and infinity values."""

    text = path.read_text(encoding="utf-8")
    try:
        value: Any = json.loads(text, parse_constant=_invalid_json_constant)
    except json.JSONDecodeError:
        value = yaml.safe_load(text)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must contain one string-keyed mapping")
    _reject_non_finite(value)
    return value


def materialize_fixture(value: Any) -> Any:
    """Materialize the two reserved evidence fixture forms recursively."""

    if isinstance(value, list):
        return [materialize_fixture(item) for item in value]
    if not isinstance(value, Mapping):
        return value

    keys = set(value)
    if keys == {"$float"}:
        tag = value["$float"]
        values = {
            "nan": math.nan,
            "positive_infinity": math.inf,
            "negative_infinity": -math.inf,
        }
        if not isinstance(tag, str) or tag not in values:
            raise ValueError(f"unsupported $float fixture: {tag!r}")
        return values[tag]
    if keys == {"$repeat"}:
        repeat = value["$repeat"]
        if not isinstance(repeat, Mapping) or set(repeat) != {"value", "count"}:
            raise ValueError("$repeat must contain exactly value and count")
        count = repeat["count"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10_000:
            raise ValueError("$repeat count must be an integer between 1 and 10000")
        item = materialize_fixture(repeat["value"])
        return [item for _ in range(count)]
    if any(not isinstance(key, str) or key.startswith("$") for key in value):
        raise ValueError("ordinary evidence objects may not use reserved $-prefixed keys")
    return {str(key): materialize_fixture(item) for key, item in value.items()}


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise AssertionError(f"{label} must be a string-keyed object")
    return value


def _violation_ids(error: DQError) -> set[str]:
    violations = error.details.get("violations", [])
    if not isinstance(violations, list):
        return set()
    return {
        str(item["rule"])
        for item in violations
        if isinstance(item, Mapping) and "rule" in item
    }


def _resolve_pointer(document: Any, pointer: str) -> Any:
    current = document
    for raw_part in pointer.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if part not in current:
                raise AssertionError(f"evidence path {pointer!r} does not exist")
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise AssertionError(f"evidence path {pointer!r} does not exist") from exc
        else:
            raise AssertionError(f"evidence path {pointer!r} traverses a scalar")
    return current


def _assert_approx(actual: Any, expected: Any, *, rtol: float, atol: float, path: str) -> None:
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        if not math.isclose(float(actual), float(expected), rel_tol=rtol, abs_tol=atol):
            raise AssertionError(f"{path}: {actual!r} is not approximately {expected!r}")
        return
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            raise AssertionError(
                f"{path}: actual length {len(actual)} does not match {len(expected)}"
            )
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=True)):
            _assert_approx(
                actual_item,
                expected_item,
                rtol=rtol,
                atol=atol,
                path=f"{path}/{index}",
            )
        return
    if actual != expected:
        raise AssertionError(f"{path}: {actual!r} does not equal {expected!r}")


def _assert_output(payload: Mapping[str, Any], assertion: Mapping[str, Any]) -> None:
    pointer = assertion.get("path")
    operation = assertion.get("op")
    if not isinstance(pointer, str) or not isinstance(operation, str):
        raise AssertionError("evidence assertions require string path and op")
    actual = _resolve_pointer(payload, pointer)

    if operation == "equal":
        expected = materialize_fixture(assertion.get("value"))
        if actual != expected:
            raise AssertionError(f"{pointer}: {actual!r} does not equal {expected!r}")
        return
    if operation == "approx":
        expected = materialize_fixture(assertion.get("value"))
        rtol = assertion.get("rtol")
        atol = assertion.get("atol")
        if not isinstance(rtol, (int, float)) or not isinstance(atol, (int, float)):
            raise AssertionError("approx assertions require numeric rtol and atol")
        _assert_approx(actual, expected, rtol=float(rtol), atol=float(atol), path=pointer)
        return
    if operation == "length_equal":
        expected_length = assertion.get("value")
        if isinstance(expected_length, bool) or not isinstance(expected_length, int):
            raise AssertionError("length_equal assertions require an integer value")
        if not isinstance(actual, (list, str, Mapping)) or len(actual) != expected_length:
            actual_length = len(actual) if hasattr(actual, "__len__") else None
            raise AssertionError(
                f"{pointer}: actual length {actual_length!r} does not equal {expected_length}"
            )
        return
    if operation in {"contains", "not_contains"}:
        expected_item = materialize_fixture(assertion.get("value"))
        if not isinstance(actual, (list, str, Mapping)):
            raise AssertionError(f"{pointer}: contains operation requires a container")
        contains = expected_item in actual
        if contains != (operation == "contains"):
            raise AssertionError(f"{pointer}: {operation} failed for {expected_item!r}")
        return
    if operation in {"contains_prefix", "not_contains_prefix"}:
        prefix = assertion.get("value")
        if not isinstance(prefix, str) or not isinstance(actual, list):
            raise AssertionError(f"{pointer}: prefix operation requires a string and list")
        contains = any(isinstance(item, str) and item.startswith(prefix) for item in actual)
        if contains != (operation == "contains_prefix"):
            raise AssertionError(f"{pointer}: {operation} failed for {prefix!r}")
        return
    raise AssertionError(f"unsupported evidence assertion operation: {operation!r}")


def execute_numerical_case(component_dir: Path, record: Mapping[str, Any]) -> None:
    """Execute one known answer, boundary case, or cross-check from authored data."""

    inputs_model, output_model = component_models(component_dir)
    inputs = materialize_fixture(record.get("inputs"))
    if not isinstance(inputs, Mapping):
        raise AssertionError("evidence inputs must materialize to an object")
    expectation = _as_mapping(record.get("expect"), label="expect")
    expected_outcome = expectation.get("outcome")
    try:
        validated_inputs = inputs_model.model_validate(inputs)
    except ValidationError as exc:
        actual_issues = input_validation_issues(exc)
        if expected_outcome != "input_validation_error":
            raise AssertionError(
                f"expected {expected_outcome!r} but Inputs validation failed with "
                f"{actual_issues!r}"
            ) from exc
        expected_issues = expected_input_validation_issues(expectation)
        if actual_issues != expected_issues:
            raise AssertionError(
                f"input validation issues {actual_issues!r} do not equal "
                f"{expected_issues!r}"
            ) from exc
        return

    if expected_outcome == "input_validation_error":
        raise AssertionError("expected input validation to fail but Inputs were valid")

    component = load_component(component_dir)
    try:
        raw_result = component(**validated_inputs.model_dump(mode="python"))
    except DQError as exc:
        if expected_outcome != "error":
            raise AssertionError(
                f"expected successful output but component raised {exc.code}: {exc.message}"
            ) from exc
        if exc.code != expectation.get("code"):
            raise AssertionError(
                f"error code {exc.code!r} does not equal {expectation.get('code')!r}"
            ) from exc
        expected_violations = set(expectation.get("violation_ids", []))
        actual_violations = _violation_ids(exc)
        if actual_violations != expected_violations:
            raise AssertionError(
                f"violation IDs {sorted(actual_violations)!r} do not equal "
                f"{sorted(expected_violations)!r}"
            ) from exc
        return

    if expected_outcome == "error":
        raise AssertionError(
            f"expected error {expectation.get('code')!r} but component returned output"
        )
    if not isinstance(raw_result, output_model):
        raise AssertionError(
            f"component returned {type(raw_result).__name__}, not canonical Output"
        )
    result: BaseModel = output_model.model_validate(raw_result.model_dump(mode="python"))
    payload = result.model_dump(mode="json")
    assertions = expectation.get("assertions")
    if not isinstance(assertions, list):
        raise AssertionError("success expectations require assertions")
    for raw_assertion in assertions:
        _assert_output(payload, _as_mapping(raw_assertion, label="assertion"))


def _runner_path(project_root: Path) -> Path:
    return (
        project_root
        / ".agents"
        / "skills"
        / "use-defined-quant"
        / "scripts"
        / "run_component.py"
    )


def _agent_failure_details(failure: OperationFailure) -> Mapping[str, Any]:
    component_error = failure.error.details.get("component_error", {})
    return _as_mapping(component_error, label="component_error")


def execute_agent_case(component_dir: Path, record: Mapping[str, Any]) -> None:
    """Execute one structured agent fixture through the real unmanaged adapter."""

    project_root = component_dir.parents[2]
    component = component_record(component_dir)
    request = OperationRequest(
        component=ComponentRef(
            id=component.component_id,
            version=component.version,
            subject_hash=subject_hash(component),
        ),
        input=materialize_fixture(record.get("inputs")),
        provenance=CallerProvenance(
            source_kind=SourceKind.SYNTHETIC,
            interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
            verification_status=ProvenanceStatus.UNVERIFIED,
            label=f"Executable evidence agent case {record.get('id', 'unknown')}",
        ),
    )
    expectation = _as_mapping(record.get("expect"), label="expect")
    expected_action = expectation.get("action")

    with tempfile.TemporaryDirectory(prefix="defined-quant-agent-evidence-") as temporary:
        temporary_path = Path(temporary)
        request_path = temporary_path / "request.json"
        output_dir = temporary_path / "output"
        request_path.write_text(
            json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(_runner_path(project_root)),
                "--request",
                str(request_path),
                "--output-dir",
                str(output_dir),
            ],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )

        if completed.returncode == 0:
            actual_action = "compute"
            success = OperationSuccess.model_validate_json(completed.stdout)
            result_path = output_dir / success.manifest.result.path
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise AssertionError("adapter result must be a JSON object")
            expected_fields = expectation.get("fields", [])
            if not isinstance(expected_fields, list) or any(
                field not in result for field in expected_fields
            ):
                raise AssertionError("adapter output is missing expected fields")
            details: Mapping[str, Any] = {}
        elif completed.returncode == 2:
            failure = OperationFailure.model_validate_json(completed.stderr)
            details = _agent_failure_details(failure)
            error_code = details.get("code")
            if error_code == "ambiguous_input":
                actual_action = "ask"
            elif error_code in {"domain_error", "unsupported_scope"}:
                actual_action = "refuse"
            else:
                raise AssertionError(f"adapter returned non-agent failure {error_code!r}")
        else:
            raise AssertionError(
                f"adapter exited {completed.returncode}: {completed.stderr or completed.stdout}"
            )

    if actual_action != expected_action:
        raise AssertionError(f"agent action {actual_action!r} does not equal {expected_action!r}")
    if actual_action == "ask":
        questions = details.get("details", {})
        question_details = _as_mapping(questions, label="ambiguous input details")
        raw_questions = question_details.get("questions", [])
        actual_fields = {
            str(item["field"])
            for item in raw_questions
            if isinstance(item, Mapping) and "field" in item
        }
        if actual_fields != set(expectation.get("question_fields", [])):
            raise AssertionError("adapter question fields do not match evidence")
    elif actual_action == "refuse":
        if details.get("code") != expectation.get("code"):
            raise AssertionError("adapter refusal code does not match evidence")
        refusal_details = _as_mapping(details.get("details", {}), label="refusal details")
        raw_violations = refusal_details.get("violations", [])
        actual_violations = {
            str(item["rule"])
            for item in raw_violations
            if isinstance(item, Mapping) and "rule" in item
        }
        if actual_violations != set(expectation.get("violation_ids", [])):
            raise AssertionError("adapter refusal violation IDs do not match evidence")


__all__ = [
    "EXECUTABLE_EVIDENCE_SECTIONS",
    "execute_agent_case",
    "execute_numerical_case",
    "generated_item_name",
    "expected_input_validation_issues",
    "input_validation_issues",
    "load_evidence",
    "materialize_fixture",
]
