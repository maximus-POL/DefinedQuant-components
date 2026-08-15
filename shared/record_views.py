"""Bounded projections and paging over verified session records."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from defined_quant.data_records import cas_json_bytes, strict_json_loads
from defined_quant.host_failures import (
    HostFailureCode,
    HostFailureException,
    TrustLabel,
    tool_success_result_projection,
)
from defined_quant.session_cas import SessionCas, StoredDataset, StoredOperation
from defined_quant_protocol import canonical_json_bytes
from pydantic import JsonValue

MAX_STRUCTURED_RESPONSE_BYTES = 256 * 1024
DEFAULT_PREVIEW_LIMIT = 20
MAX_PREVIEW_LIMIT = 50
DEFAULT_RESULT_LIMIT = 100
MAX_RESULT_LIMIT = 1_000
DEFAULT_MESSAGE_LIMIT = 20
MAX_MESSAGE_LIMIT = 100

_T = TypeVar("_T")
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _fail(
    code: HostFailureCode,
    *,
    details: Mapping[str, JsonValue] | None = None,
) -> HostFailureException:
    return HostFailureException(code, details=details)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _result_size(data: dict[str, Any], *, trust: TrustLabel) -> int:
    # CAS serialization adds one storage-only LF; tool-result accounting is pre-JSON-RPC JSON.
    return len(cas_json_bytes(tool_success_result_projection(data, trust=trust))) - 1


def _bounded_result(
    data: dict[str, Any],
    *,
    limit_name: str,
    trust: TrustLabel,
) -> dict[str, Any]:
    actual = _result_size(data, trust=trust)
    if actual > MAX_STRUCTURED_RESPONSE_BYTES:
        raise _fail(
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            details={
                "limit_name": limit_name,
                "maximum": MAX_STRUCTURED_RESPONSE_BYTES,
                "actual": actual,
            },
        )
    return data


def _validate_limit(value: int, *, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": ["limit"]})
    return value


def _start(
    cas: SessionCas,
    *,
    cursor: str | None,
    reference: str,
    view: str,
    selector: str | None,
) -> int:
    if cursor is None:
        return 0
    return cas.decode_cursor(
        cursor,
        reference=reference,
        view=view,
        selector=selector,
    )


def _page(
    values: Sequence[_T],
    *,
    start: int,
    limit: int,
    build: Callable[[list[_T], dict[str, Any]], dict[str, Any]],
    cas: SessionCas,
    reference: str,
    view: str,
    selector: str | None,
    limit_name: str,
    trust: TrustLabel,
) -> dict[str, Any]:
    if start > len(values):
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": ["cursor"]})
    selected: list[_T] = []
    maximum_end = min(len(values), start + limit)
    for index in range(start, maximum_end):
        candidate = [*selected, values[index]]
        candidate_end = start + len(candidate)
        complete = candidate_end == len(values)
        page = {
            "start": start,
            "returned": len(candidate),
            "next_cursor": (
                None
                if complete
                else cas.encode_cursor(reference, view, selector, candidate_end)
            ),
            "complete": complete,
        }
        projected = build(candidate, page)
        if _result_size(projected, trust=trust) > MAX_STRUCTURED_RESPONSE_BYTES:
            if not selected:
                actual = _result_size(projected, trust=trust)
                raise _fail(
                    HostFailureCode.RESULT_LIMIT_EXCEEDED,
                    details={
                        "limit_name": limit_name,
                        "maximum": MAX_STRUCTURED_RESPONSE_BYTES,
                        "actual": actual,
                    },
                )
            break
        selected = candidate
    end = start + len(selected)
    complete = end == len(values)
    page = {
        "start": start,
        "returned": len(selected),
        "next_cursor": (
            None if complete else cas.encode_cursor(reference, view, selector, end)
        ),
        "complete": complete,
    }
    return _bounded_result(build(selected, page), limit_name=limit_name, trust=trust)


def _dataset_metadata(stored: StoredDataset, *, compact: bool) -> dict[str, Any]:
    record = stored.record
    return {
        "compact": compact,
        "dataset_ref": stored.reference,
        "normalized_payload_sha256": record.payload.sha256,
        "raw_source_sha256": record.raw_source_sha256,
        "row_count": record.row_count,
        "columns": [column.canonical_projection() for column in record.columns],
        "semantics": record.semantics.canonical_projection(),
        "external_preprocessing": record.external_preprocessing.model_dump(mode="json"),
        "normalization_events": [
            event.model_dump(mode="json") for event in record.normalization_events
        ],
        "findings": [finding.model_dump(mode="json") for finding in record.findings],
        "expires": "session_end",
    }


def describe_dataset(
    cas: SessionCas,
    reference: str,
    *,
    view: str = "metadata",
    cursor: str | None = None,
    limit: int = DEFAULT_PREVIEW_LIMIT,
) -> dict[str, Any]:
    """Return verified metadata or a bounded source-order normalized preview."""

    stored = cas.load_dataset(reference)
    if view == "metadata":
        if cursor is not None or limit != DEFAULT_PREVIEW_LIMIT:
            raise _fail(
                HostFailureCode.INVALID_TOOL_REQUEST,
                details={"fields": ["cursor"] if cursor is not None else ["limit"]},
            )
        return _bounded_result(
            _dataset_metadata(stored, compact=True),
            limit_name="structured_tool_response_bytes",
            trust=TrustLabel.UNVERIFIED_CALLER_DATA,
        )
    if view != "preview":
        raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": ["view"]})
    page_limit = _validate_limit(limit, maximum=MAX_PREVIEW_LIMIT)
    start = _start(
        cas,
        cursor=cursor,
        reference=reference,
        view="preview",
        selector=None,
    )
    field_ids = [column.field_id for column in stored.payload.columns]
    rows = [
        dict(zip(field_ids, row, strict=True))
        for row in stored.payload.rows
    ]
    base = _dataset_metadata(stored, compact=False)

    def build(items: list[dict[str, JsonValue]], page: dict[str, Any]) -> dict[str, Any]:
        return {**base, "preview": {"rows": items, **page}}

    return _page(
        rows,
        start=start,
        limit=page_limit,
        build=build,
        cas=cas,
        reference=reference,
        view="preview",
        selector=None,
        limit_name="dataset_preview_page_bytes",
        trust=TrustLabel.UNVERIFIED_CALLER_DATA,
    )


def _result(stored: StoredOperation) -> dict[str, JsonValue]:
    content = stored.members.get(stored.manifest.result.path)
    if content is None:
        raise _fail(HostFailureCode.RECORD_CORRUPT)
    try:
        value = strict_json_loads(content)
    except ValueError as exc:
        raise _fail(HostFailureCode.RECORD_CORRUPT) from exc
    if not isinstance(value, dict):
        raise _fail(HostFailureCode.RECORD_CORRUPT)
    return value


def _messages(result: Mapping[str, JsonValue]) -> tuple[list[dict[str, Any]], str]:
    warnings = result.get("warnings", [])
    disclosures = result.get("disclosures", [])
    if not isinstance(warnings, list) or not isinstance(disclosures, list):
        raise _fail(HostFailureCode.RECORD_CORRUPT)
    if not all(isinstance(item, str) for item in (*warnings, *disclosures)):
        raise _fail(HostFailureCode.RECORD_CORRUPT)
    combined = [
        {"kind": "warning", "index": index, "text": text}
        for index, text in enumerate(warnings)
    ] + [
        {"kind": "disclosure", "index": index, "text": text}
        for index, text in enumerate(disclosures)
    ]
    return combined, _digest({"warnings": warnings, "disclosures": disclosures})


def _artifact_metadata(stored: StoredOperation) -> list[dict[str, Any]]:
    sizes = {member.path: member.size_bytes for member in stored.record.members}
    digest = stored.reference.rsplit(":", 1)[1]
    return [
        {
            "artifact_id": artifact.visualization_id,
            "media_type": artifact.media_type,
            "size_bytes": sizes[artifact.path],
            "sha256": artifact.sha256,
            "resource_uri": (
                f"dqop://v1/{digest}/artifact/{artifact.visualization_id}"
            ),
        }
        for artifact in stored.manifest.artifacts
    ]


def operation_summary(
    cas: SessionCas,
    stored: StoredOperation,
    *,
    cursor_encoder: Callable[[str, str, str | None, int], str] | None = None,
) -> dict[str, Any]:
    """Build the compact, calculation-free projection of one verified operation."""

    result = _result(stored)
    result_projection: dict[str, Any] = {}
    for field, value in result.items():
        if isinstance(value, (list, dict)):
            result_projection[field] = {
                "kind": "structured",
                "value_kind": "array" if isinstance(value, list) else "object",
                "count": len(value),
                "sha256": _digest(value),
                "page_available": True,
            }
        else:
            result_projection[field] = value
    messages, messages_digest = _messages(result)
    first_messages = messages[:DEFAULT_MESSAGE_LIMIT]
    complete = len(first_messages) == len(messages)
    encode_cursor = cas.encode_cursor if cursor_encoder is None else cursor_encoder
    source = stored.record.binding.source
    source_context: dict[str, Any] | None = None
    if source is not None:
        if source.kind != "dataset":
            raise _fail(HostFailureCode.UNSUPPORTED_BINDING)
        dataset = cas.load_dataset(source.ref)
        source_context = {
            "kind": "dataset",
            "ref": source.ref,
            "external_preprocessing_status": dataset.record.external_preprocessing.status,
            "normalization_event_count": len(dataset.record.normalization_events),
            "normalization_event_application_count": sum(
                event.count for event in dataset.record.normalization_events
            ),
        }
    summary: dict[str, Any] = {
        "compact": True,
        "operation_ref": stored.reference,
        "operation_hash": stored.record.operation_hash,
        "manifest_sha256": stored.record.manifest_sha256,
        "component": stored.record.component.model_dump(mode="json"),
        "protocol_version": stored.record.protocol_version,
        "execution_mode": "unmanaged",
        "result": result_projection,
        "messages": {
            "items": first_messages,
            "total": len(messages),
            "digest": messages_digest,
            "complete": complete,
            "next_cursor": (
                None
                if complete
                else encode_cursor(
                    stored.reference, "messages", None, len(first_messages)
                )
            ),
        },
        "artifacts": _artifact_metadata(stored),
        "expires": "session_end",
    }
    if source_context is not None:
        summary["source_context"] = source_context
    return _bounded_result(
        summary,
        limit_name="structured_tool_response_bytes",
        trust=TrustLabel.UNMANAGED_EXECUTION,
    )


def get_operation(
    cas: SessionCas,
    reference: str,
    *,
    view: str = "summary",
    field: str | None = None,
    artifact_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return one exact bounded view of a fully reconciled operation."""

    stored = cas.load_operation(reference)
    if view == "summary":
        if any(value is not None for value in (field, artifact_id, cursor, limit)):
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
        summary = operation_summary(cas, stored)
        return _bounded_result(
            {
                "compact": True,
                "operation_ref": reference,
                "view": "summary",
                "summary_sha256": _digest(summary),
                "summary": summary,
                "expires": "session_end",
            },
            limit_name="structured_tool_response_bytes",
            trust=TrustLabel.UNMANAGED_EXECUTION,
        )
    if view == "manifest":
        if any(value is not None for value in (field, artifact_id, cursor, limit)):
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
        return _bounded_result(
            {
                "compact": False,
                "operation_ref": reference,
                "view": "manifest",
                "manifest_sha256": stored.record.manifest_sha256,
                "manifest": stored.manifest.model_dump(mode="json"),
                "expires": "session_end",
            },
            limit_name="structured_tool_response_bytes",
            trust=TrustLabel.UNMANAGED_EXECUTION,
        )
    result = _result(stored)
    result_sha256 = stored.manifest.result.sha256
    if view == "result_field":
        if field is None or artifact_id is not None or field not in result:
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": ["field"]})
        value = result[field]
        base = {
            "compact": False,
            "operation_ref": reference,
            "view": "result_field",
            "field": field,
            "result_sha256": result_sha256,
            "field_sha256": _digest(value),
            "expires": "session_end",
        }
        if not isinstance(value, (list, dict)):
            if cursor is not None or limit is not None:
                raise _fail(
                    HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []}
                )
            return _bounded_result(
                {**base, "value_kind": "scalar", "value": value},
                limit_name="result_page_bytes",
                trust=TrustLabel.UNMANAGED_EXECUTION,
            )
        page_limit = _validate_limit(
            DEFAULT_RESULT_LIMIT if limit is None else limit,
            maximum=MAX_RESULT_LIMIT,
        )
        start = _start(
            cas,
            cursor=cursor,
            reference=reference,
            view="result_field",
            selector=field,
        )
        if isinstance(value, list):
            def build(items: list[JsonValue], page: dict[str, Any]) -> dict[str, Any]:
                return {**base, "value_kind": "array", "items": items, "page": page}

            return _page(
                value,
                start=start,
                limit=page_limit,
                build=build,
                cas=cas,
                reference=reference,
                view="result_field",
                selector=field,
                limit_name="result_page_bytes",
                trust=TrustLabel.UNMANAGED_EXECUTION,
            )
        entries = [
            {"key": key, "value": value[key]}
            for key in sorted(value, key=lambda item: item.encode("utf-8"))
        ]

        def build_entries(
            items: list[dict[str, JsonValue]], page: dict[str, Any]
        ) -> dict[str, Any]:
            return {**base, "value_kind": "object", "entries": items, "page": page}

        return _page(
            entries,
            start=start,
            limit=page_limit,
            build=build_entries,
            cas=cas,
            reference=reference,
            view="result_field",
            selector=field,
            limit_name="result_page_bytes",
            trust=TrustLabel.UNMANAGED_EXECUTION,
        )
    if view == "messages":
        if field is not None or artifact_id is not None:
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
        messages, messages_digest = _messages(result)
        page_limit = _validate_limit(
            DEFAULT_MESSAGE_LIMIT if limit is None else limit,
            maximum=MAX_MESSAGE_LIMIT,
        )
        start = _start(
            cas,
            cursor=cursor,
            reference=reference,
            view="messages",
            selector=None,
        )
        base = {
            "compact": False,
            "operation_ref": reference,
            "view": "messages",
            "result_sha256": result_sha256,
            "messages_sha256": messages_digest,
            "expires": "session_end",
        }

        def build_messages(
            items: list[dict[str, Any]], page: dict[str, Any]
        ) -> dict[str, Any]:
            return {**base, "messages": items, "page": page}

        return _page(
            messages,
            start=start,
            limit=page_limit,
            build=build_messages,
            cas=cas,
            reference=reference,
            view="messages",
            selector=None,
            limit_name="message_page_bytes",
            trust=TrustLabel.UNMANAGED_EXECUTION,
        )
    if view == "artifact_metadata":
        if artifact_id is None or field is not None or cursor is not None or limit is not None:
            raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": []})
        if _SAFE_ID.fullmatch(artifact_id) is None:
            raise _fail(
                HostFailureCode.INVALID_TOOL_REQUEST,
                details={"fields": ["artifact_id"]},
            )
        artifact = next(
            (
                item
                for item in _artifact_metadata(stored)
                if item["artifact_id"] == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise _fail(
                HostFailureCode.ARTIFACT_NOT_FOUND,
                details={"artifact_id": artifact_id},
            )
        return _bounded_result(
            {
                "compact": True,
                "operation_ref": reference,
                "view": "artifact_metadata",
                "manifest_sha256": stored.record.manifest_sha256,
                "artifact": artifact,
                "expires": "session_end",
            },
            limit_name="structured_tool_response_bytes",
            trust=TrustLabel.UNMANAGED_EXECUTION,
        )
    raise _fail(HostFailureCode.INVALID_TOOL_REQUEST, details={"fields": ["view"]})


__all__ = [
    "DEFAULT_MESSAGE_LIMIT",
    "DEFAULT_PREVIEW_LIMIT",
    "DEFAULT_RESULT_LIMIT",
    "MAX_MESSAGE_LIMIT",
    "MAX_PREVIEW_LIMIT",
    "MAX_RESULT_LIMIT",
    "MAX_STRUCTURED_RESPONSE_BYTES",
    "describe_dataset",
    "get_operation",
    "operation_summary",
]
