"""Bounded deterministic projections over immutable methods-first run records."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from defined_quant.data_records import cas_json_bytes, strict_json_loads
from defined_quant.host_failures import (
    HostFailureCode,
    HostFailureException,
    TrustLabel,
    tool_success_result_projection,
)
from defined_quant_protocol import canonical_json_bytes
from defined_quant_protocol.execution import (
    ExecutionFailure,
    RunRecord,
    StepRecord,
    ValidationIssue,
    ValidationStatus,
)
from pydantic import JsonValue

MAX_RUN_VIEW_BYTES = 256 * 1024
DEFAULT_RUN_PAGE_LIMIT = 100
MAX_RUN_PAGE_LIMIT = 1_000
DEFAULT_ARTIFACT_CHUNK_BYTES = 64 * 1024
MAX_ARTIFACT_CHUNK_BYTES = 128 * 1024

_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ArtifactChunk:
    """One bounded byte range from a fully verified governed-run artifact."""

    content: bytes
    media_type: str
    artifact_sha256: str
    chunk_sha256: str
    total_bytes: int
    offset: int
    complete: bool
    next_cursor: str | None


def _fail(
    code: HostFailureCode,
    *,
    fields: Sequence[str] = (),
    details: Mapping[str, JsonValue] | None = None,
) -> HostFailureException:
    if details is None and fields:
        details = {"fields": list(fields)}
    return HostFailureException(code, details=details)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _result_size(data: dict[str, Any]) -> int:
    projection = tool_success_result_projection(
        data,
        trust=TrustLabel.GOVERNED_EXECUTION_RECORD,
    )
    return len(cas_json_bytes(projection)) - 1


def _bounded(data: dict[str, Any], *, limit_name: str) -> dict[str, Any]:
    actual = _result_size(data)
    if actual > MAX_RUN_VIEW_BYTES:
        raise _fail(
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            details={
                "limit_name": limit_name,
                "maximum": MAX_RUN_VIEW_BYTES,
                "actual": actual,
            },
        )
    return data


def _validate_limit(value: int | None) -> int:
    selected = DEFAULT_RUN_PAGE_LIMIT if value is None else value
    if type(selected) is not int or not 1 <= selected <= MAX_RUN_PAGE_LIMIT:
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("limit",))
    return selected


def _validate_artifact_limit(value: int | None) -> int:
    selected = DEFAULT_ARTIFACT_CHUNK_BYTES if value is None else value
    if type(selected) is not int or not 1 <= selected <= MAX_ARTIFACT_CHUNK_BYTES:
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("limit_bytes",))
    return selected


def _cursor_payload(
    reference: str,
    view: str,
    selector: str | None,
    index: int,
) -> bytes:
    return json.dumps(
        {"i": index, "r": reference, "s": selector, "v": view},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode_cursor(
    key: bytes,
    *,
    reference: str,
    view: str,
    selector: str | None,
    index: int,
) -> str:
    payload = _cursor_payload(reference, view, selector, index)
    signature = hmac.digest(key, payload, "sha256")
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _decode_cursor(
    key: bytes,
    cursor: str | None,
    *,
    reference: str,
    view: str,
    selector: str | None,
) -> int:
    if cursor is None:
        return 0
    try:
        if _CURSOR_PATTERN.fullmatch(cursor) is None:
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        if canonical != cursor or len(decoded) <= 32:
            raise ValueError
        payload, signature = decoded[:-32], decoded[-32:]
        if not hmac.compare_digest(signature, hmac.digest(key, payload, "sha256")):
            raise ValueError
        value = strict_json_loads(payload)
        if (
            not isinstance(value, dict)
            or set(value) != {"i", "r", "s", "v"}
            or value["r"] != reference
            or value["v"] != view
            or value["s"] != selector
            or type(value["i"]) is not int
            or value["i"] < 0
        ):
            raise ValueError
        return value["i"]
    except (TypeError, UnicodeError, ValueError):
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("cursor",)) from None


def _page(
    values: Sequence[_T],
    *,
    start: int,
    limit: int,
    build: Callable[[list[_T], dict[str, Any]], dict[str, Any]],
    cursor_key: bytes,
    reference: str,
    view: str,
    selector: str | None,
    limit_name: str,
) -> dict[str, Any]:
    if start > len(values):
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("cursor",))
    maximum_end = min(len(values), start + limit)
    available = list(values[start:maximum_end])

    def project(items: list[_T]) -> dict[str, Any]:
        end = start + len(items)
        complete = end == len(values)
        page_metadata = {
            "start": start,
            "returned": len(items),
            "total": len(values),
            "complete": complete,
            "next_cursor": (
                None
                if complete
                else _encode_cursor(
                    cursor_key,
                    reference=reference,
                    view=view,
                    selector=selector,
                    index=end,
                )
            ),
        }
        return build(items, page_metadata)

    candidate = project(available)
    if _result_size(candidate) <= MAX_RUN_VIEW_BYTES:
        return candidate
    if not available:
        return _bounded(candidate, limit_name=limit_name)

    lower = 0
    upper = len(available)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        if _result_size(project(available[:middle])) <= MAX_RUN_VIEW_BYTES:
            lower = middle
        else:
            upper = middle - 1
    if lower == 0:
        return _bounded(project(available[:1]), limit_name=limit_name)
    return _bounded(project(available[:lower]), limit_name=limit_name)


def _failure(failure: ExecutionFailure | None) -> dict[str, Any] | None:
    if failure is None:
        return None
    return {
        "code": failure.code.value,
        "message": failure.message,
        "retryable": failure.retryable,
        "backend_role": (
            None if failure.backend_role is None else failure.backend_role.value
        ),
        "backend": (
            None if failure.backend is None else failure.backend.model_dump(mode="json")
        ),
        "fact_count": len(failure.facts),
        "facts_sha256": _digest(
            [item.model_dump(mode="json") for item in failure.facts]
        ),
    }


def _validation(
    status: ValidationStatus,
    issues: Sequence[ValidationIssue],
) -> dict[str, Any]:
    return {
        "status": status.value,
        "issue_count": len(issues),
        "issues_sha256": _digest([item.model_dump(mode="json") for item in issues]),
    }


def _output_descriptor(field: str, value: JsonValue) -> dict[str, Any]:
    if isinstance(value, list):
        kind = "array"
        count: int | None = len(value)
    elif isinstance(value, dict):
        kind = "object"
        count = len(value)
    else:
        kind = "scalar"
        count = None
    return {
        "field": field,
        "value_kind": kind,
        "count": count,
        "sha256": _digest(value),
        "page_available": kind != "scalar",
    }


def execution_summary(record: RunRecord) -> dict[str, Any]:
    """Return the compact execution acknowledgement; retain the full record internally."""

    return _bounded(
        {
            "compact": True,
            "run_ref": record.ref.reference,
            "status": record.status.value,
            "plan_ref": record.plan_record.plan.ref.reference,
            "method": record.plan_record.plan.method.model_dump(mode="json"),
            "failure": _failure(record.failure),
            "record_available": True,
            "expires": "session_end",
        },
        limit_name="run_execution_summary_bytes",
    )


def get_artifact_chunk(
    *,
    content: bytes,
    media_type: str,
    artifact_sha256: str,
    run_reference: str,
    artifact_id: str,
    cursor_key: bytes,
    cursor: str | None = None,
    limit_bytes: int | None = None,
) -> ArtifactChunk:
    """Return one session-bound artifact chunk without exposing an arbitrary byte offset."""

    selected_limit = _validate_artifact_limit(limit_bytes)
    start = _decode_cursor(
        cursor_key,
        cursor,
        reference=run_reference,
        view="artifact",
        selector=artifact_id,
    )
    if start > len(content):
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("cursor",))
    end = min(len(content), start + selected_limit)
    selected = content[start:end]
    complete = end == len(content)
    return ArtifactChunk(
        content=selected,
        media_type=media_type,
        artifact_sha256=artifact_sha256,
        chunk_sha256=hashlib.sha256(selected).hexdigest(),
        total_bytes=len(content),
        offset=start,
        complete=complete,
        next_cursor=(
            None
            if complete
            else _encode_cursor(
                cursor_key,
                reference=run_reference,
                view="artifact",
                selector=artifact_id,
                index=end,
            )
        ),
    )


def _summary(record: RunRecord) -> dict[str, Any]:
    output = record.canonical_method_output
    step_statuses = Counter(item.status.value for item in record.steps)
    return {
        "compact": True,
        "run_ref": record.ref.reference,
        "run_hash": record.run_hash,
        "view": "summary",
        "status": record.status.value,
        "plan_ref": record.plan_record.plan.ref.reference,
        "plan_record_hash": record.plan_record.record_hash,
        "method": record.plan_record.plan.method.model_dump(mode="json"),
        "policy": record.plan_record.plan.policy.model_dump(mode="json"),
        "availability_snapshot": (
            record.plan_record.plan.availability_snapshot.model_dump(mode="json")
        ),
        "method_input_validation": _validation(
            record.method_input_validation.status,
            record.method_input_validation.issues,
        ),
        "method_output_validation": _validation(
            record.method_output_validation.status,
            record.method_output_validation.issues,
        ),
        "steps": {
            "total": len(record.steps),
            "statuses": dict(sorted(step_statuses.items())),
            "sha256": _digest([item.ref.reference for item in record.steps]),
            "view_available": True,
        },
        "datasets": {
            "total": len(record.datasets),
            "sha256": _digest([item.reference for item in record.datasets]),
            "view_available": True,
        },
        "output": {
            "present": output is not None,
            "field_count": 0 if output is None else len(output),
            "sha256": None if output is None else _digest(output),
            "fields_view_available": output is not None,
        },
        "warnings": {
            "total": len(record.warnings),
            "sha256": _digest(
                [item.model_dump(mode="json") for item in record.warnings]
            ),
            "view_available": True,
        },
        "artifacts": {
            "total": len(record.artifacts),
            "sha256": _digest(
                [item.model_dump(mode="json") for item in record.artifacts]
            ),
            "view_available": True,
        },
        "failure": _failure(record.failure),
        "executor": record.executor.model_dump(mode="json"),
        "started_at": record.started_at.isoformat(),
        "finished_at": record.finished_at.isoformat(),
        "expires": "session_end",
    }


def _step(item: StepRecord) -> dict[str, Any]:
    adapter_result = item.adapter_result
    provider_interaction_count = (
        0 if adapter_result is None else len(adapter_result.provider_interactions)
    )
    artifact_count = 0
    if adapter_result is not None and adapter_result.status == "success":
        artifact_count = len(adapter_result.artifacts)
    return {
        "step_ref": item.ref.reference,
        "step_id": item.compiled_step.step_id,
        "status": item.status.value,
        "capability": item.compiled_step.capability.model_dump(mode="json"),
        "implementation": item.compiled_step.implementation.model_dump(mode="json"),
        "adapter": item.compiled_step.adapter.model_dump(mode="json"),
        "backend_bindings": [
            value.model_dump(mode="json")
            for value in item.compiled_step.backend_bindings
        ],
        "input_validation": _validation(
            item.input_validation.status,
            item.input_validation.issues,
        ),
        "output_validation": _validation(
            item.output_validation.status,
            item.output_validation.issues,
        ),
        "canonical_inputs_sha256": _digest(item.canonical_inputs),
        "canonical_output": (
            None
            if item.canonical_output is None
            else {
                "field_count": len(item.canonical_output),
                "sha256": _digest(item.canonical_output),
            }
        ),
        "adapter_result_status": (
            None if adapter_result is None else adapter_result.status
        ),
        "provider_interaction_count": provider_interaction_count,
        "artifact_count": artifact_count,
        "warning_count": len(item.warnings),
        "failure": _failure(item.failure),
    }


def get_run_view(
    record: RunRecord,
    *,
    cursor_key: bytes,
    view: str = "summary",
    field: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return one exact bounded view without mutating or truncating the retained record."""

    reference = record.ref.reference
    if view == "summary":
        if field is not None or cursor is not None or limit is not None:
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=())
        return _bounded(_summary(record), limit_name="run_summary_bytes")

    if view == "output":
        output = record.canonical_method_output
        if field is None or output is None or field not in output:
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("field",))
        value = output[field]
        base = {
            "compact": False,
            "run_ref": reference,
            "view": "output",
            "field": field,
            "output_sha256": _digest(output),
            "field_sha256": _digest(value),
            "expires": "session_end",
        }
        if not isinstance(value, (list, dict)):
            if cursor is not None or limit is not None:
                raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=())
            return _bounded(
                {**base, "value_kind": "scalar", "value": value},
                limit_name="run_output_page_bytes",
            )
        page_limit = _validate_limit(limit)
        start = _decode_cursor(
            cursor_key,
            cursor,
            reference=reference,
            view=view,
            selector=field,
        )
        if isinstance(value, list):

            def build_output(
                items: list[JsonValue],
                page: dict[str, Any],
            ) -> dict[str, Any]:
                return {**base, "value_kind": "array", "items": items, "page": page}

            return _page(
                value,
                start=start,
                limit=page_limit,
                build=build_output,
                cursor_key=cursor_key,
                reference=reference,
                view=view,
                selector=field,
                limit_name="run_output_page_bytes",
            )
        entries = [
            {"key": key, "value": value[key]}
            for key in sorted(value, key=lambda item: item.encode("utf-8"))
        ]

        def build_entries(
            items: list[dict[str, JsonValue]],
            page: dict[str, Any],
        ) -> dict[str, Any]:
            return {**base, "value_kind": "object", "entries": items, "page": page}

        return _page(
            entries,
            start=start,
            limit=page_limit,
            build=build_entries,
            cursor_key=cursor_key,
            reference=reference,
            view=view,
            selector=field,
            limit_name="run_output_page_bytes",
        )

    if field is not None:
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("field",))
    page_limit = _validate_limit(limit)
    start = _decode_cursor(
        cursor_key,
        cursor,
        reference=reference,
        view=view,
        selector=None,
    )
    if view == "steps":
        values: Sequence[Any] = [_step(item) for item in record.steps]
        item_name = "steps"
    elif view == "warnings":
        values = [item.model_dump(mode="json") for item in record.warnings]
        item_name = "warnings"
    elif view == "artifacts":
        values = [item.model_dump(mode="json") for item in record.artifacts]
        item_name = "artifacts"
    elif view == "datasets":
        values = [item.model_dump(mode="json") for item in record.datasets]
        item_name = "datasets"
    elif view == "output_fields":
        output = record.canonical_method_output or {}
        values = [
            _output_descriptor(name, output[name])
            for name in sorted(output, key=lambda item: item.encode("utf-8"))
        ]
        item_name = "fields"
    else:
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, fields=("view",))
    base = {
        "compact": False,
        "run_ref": reference,
        "view": view,
        "record_sha256": record.run_hash,
        "expires": "session_end",
    }

    def build_items(items: list[Any], page: dict[str, Any]) -> dict[str, Any]:
        return {**base, item_name: items, "page": page}

    return _page(
        values,
        start=start,
        limit=page_limit,
        build=build_items,
        cursor_key=cursor_key,
        reference=reference,
        view=view,
        selector=None,
        limit_name="run_view_page_bytes",
    )


__all__ = [
    "DEFAULT_ARTIFACT_CHUNK_BYTES",
    "DEFAULT_RUN_PAGE_LIMIT",
    "MAX_ARTIFACT_CHUNK_BYTES",
    "MAX_RUN_PAGE_LIMIT",
    "MAX_RUN_VIEW_BYTES",
    "ArtifactChunk",
    "execution_summary",
    "get_artifact_chunk",
    "get_run_view",
]
