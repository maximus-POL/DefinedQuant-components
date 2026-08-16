"""Official-SDK STDIO transport for the Defined Quant local MCP alpha."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import sys
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Event, Lock
from typing import Any, TypeVar, cast

import anyio
import mcp.types as types
from defined_quant.data_records import DatasetRegistrationRequest
from defined_quant.discovery import FACET_NAMES, DiscoveryFilters, SearchResults
from defined_quant.host_failures import (
    HOST_SUCCESS_TEXT,
    HostFailureCode,
    HostFailureException,
    TrustLabel,
    host_failure,
    host_success,
)
from defined_quant.service import DefinedQuantService
from defined_quant.stdio_framing import (
    BinaryFrameError,
    BoundedBinaryFrameReader,
    configure_binary_descriptors,
    write_utf8_lf_frame,
)
from defined_quant.types import ComponentContractError
from mcp.os.win32.utilities import rebind_std_handle_to_fd
from mcp.server._otel import OpenTelemetryMiddleware
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ValidationError

from .models import (
    REQUEST_MODELS,
    ComparePortsRequest,
    DescribeDatasetRequest,
    ExecuteComponentRequest,
    GetOperationRequest,
    InspectRequest,
    SearchRequest,
)

SERVER_NAME = "defined-quant-mcp"
SERVER_VERSION = "0.1.0a1"
HOST_SCHEMA_VERSION = 1
MAX_ARGUMENT_BYTES = 1024 * 1024
MAX_SEARCH_RESULT_BYTES = 64 * 1024
MAX_TOOL_RESULT_BYTES = 256 * 1024
MAX_STATIC_FRAME_BYTES = 256 * 1024
MAX_RESOURCE_FRAME_BYTES = 8 * 1024 * 1024
RESOURCE_TEMPLATE = "dqop://v1/{operation_sha256}/artifact/{artifact_id}"
INITIALIZATION_INSTRUCTIONS = (
    "For financial calculations, search the Defined Quant catalog, inspect only selected "
    "canonical contracts, register or reference data, execute through Defined Quant tools, "
    "and read the typed outcome. Do not inspect component source unless the user explicitly "
    "requests development, review, or debugging. Never infer an answer-changing convention. "
    "Operations are unmanaged and supplied data is not authenticated."
)

_T = TypeVar("_T")

_RESOURCE_URI = re.compile(
    r"^dqop://v1/(?P<operation>[0-9a-f]{64})/artifact/"
    r"(?P<artifact>[a-z][a-z0-9_]{0,63})$"
)

_SUCCESS_TRUST = {
    "search_components": TrustLabel.CONTRACT_METADATA_ONLY,
    "inspect_component": TrustLabel.INSTALLED_SUBJECT_INSPECTED,
    "register_dataset": TrustLabel.UNVERIFIED_CALLER_DATA,
    "describe_dataset": TrustLabel.UNVERIFIED_CALLER_DATA,
    "compare_ports": TrustLabel.STRUCTURAL_COMPATIBILITY_ONLY,
    "execute_component": TrustLabel.UNMANAGED_EXECUTION,
    "get_operation": TrustLabel.UNMANAGED_EXECUTION,
}
_FAILURE_TRUST = {
    **{name: TrustLabel.NO_VERIFIED_RESULT for name in REQUEST_MODELS},
    "search_components": TrustLabel.CONTRACT_METADATA_ONLY,
    "compare_ports": TrustLabel.STRUCTURAL_COMPATIBILITY_ONLY,
}

_ALLOWED_FAILURES = {
    "search_components": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            HostFailureCode.COMPONENT_CONTRACT_ERROR,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
    "inspect_component": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            HostFailureCode.COMPONENT_NOT_FOUND,
            HostFailureCode.COMPONENT_IDENTITY_MISMATCH,
            HostFailureCode.COMPONENT_CONTRACT_ERROR,
            HostFailureCode.WORKER_TIMEOUT,
            HostFailureCode.WORKER_CANCELLED,
            HostFailureCode.WORKER_CRASHED,
            HostFailureCode.WORKER_CAPACITY,
            HostFailureCode.WORKER_RESOURCE_LIMIT,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
    "register_dataset": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            HostFailureCode.INVALID_DATASET,
            HostFailureCode.UNSUPPORTED_DATA_FORMAT,
            HostFailureCode.INPUT_ROOT_DENIED,
            HostFailureCode.UNSAFE_INPUT_PATH,
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            HostFailureCode.INVALID_FIELD_MAPPING,
            HostFailureCode.RECORD_CORRUPT,
            HostFailureCode.CACHE_FULL,
            HostFailureCode.RECORD_PUBLICATION_FAILED,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
    "describe_dataset": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            HostFailureCode.UNSUPPORTED_REFERENCE_VERSION,
            HostFailureCode.REFERENCE_NOT_FOUND,
            HostFailureCode.REFERENCE_SCOPE_DENIED,
            HostFailureCode.RECORD_CORRUPT,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
    "compare_ports": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            HostFailureCode.COMPONENT_NOT_FOUND,
            HostFailureCode.COMPONENT_IDENTITY_MISMATCH,
            HostFailureCode.INVALID_FIELD_MAPPING,
            HostFailureCode.INCOMPATIBLE_PORTS,
            HostFailureCode.COMPONENT_CONTRACT_ERROR,
            HostFailureCode.WORKER_TIMEOUT,
            HostFailureCode.WORKER_CANCELLED,
            HostFailureCode.WORKER_CRASHED,
            HostFailureCode.WORKER_CAPACITY,
            HostFailureCode.WORKER_RESOURCE_LIMIT,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
    "execute_component": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            HostFailureCode.UNSUPPORTED_PROTOCOL_VERSION,
            HostFailureCode.UNSUPPORTED_REFERENCE_VERSION,
            HostFailureCode.COMPONENT_NOT_FOUND,
            HostFailureCode.COMPONENT_IDENTITY_MISMATCH,
            HostFailureCode.MISSING_CONVENTION,
            HostFailureCode.INCOMPATIBLE_PORTS,
            HostFailureCode.UNSUPPORTED_BINDING,
            HostFailureCode.MULTIPLE_NON_LITERAL_SOURCES,
            HostFailureCode.INVALID_DATASET,
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            HostFailureCode.INVALID_FIELD_MAPPING,
            HostFailureCode.REFERENCE_NOT_FOUND,
            HostFailureCode.REFERENCE_SCOPE_DENIED,
            HostFailureCode.RECORD_CORRUPT,
            HostFailureCode.CACHE_FULL,
            HostFailureCode.RECORD_PUBLICATION_FAILED,
            HostFailureCode.WORKER_TIMEOUT,
            HostFailureCode.WORKER_CANCELLED,
            HostFailureCode.WORKER_CRASHED,
            HostFailureCode.WORKER_CAPACITY,
            HostFailureCode.WORKER_RESOURCE_LIMIT,
            HostFailureCode.COMPONENT_REFUSED,
            HostFailureCode.COMPONENT_FAILED,
            HostFailureCode.INVALID_COMPONENT_INPUT,
            HostFailureCode.OUTPUT_VALIDATION_FAILED,
            HostFailureCode.COMPONENT_CONTRACT_ERROR,
            HostFailureCode.ARTIFACT_PUBLICATION_FAILED,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
    "get_operation": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
            HostFailureCode.INPUT_LIMIT_EXCEEDED,
            HostFailureCode.UNSUPPORTED_PROTOCOL_VERSION,
            HostFailureCode.UNSUPPORTED_REFERENCE_VERSION,
            HostFailureCode.REFERENCE_NOT_FOUND,
            HostFailureCode.REFERENCE_SCOPE_DENIED,
            HostFailureCode.RECORD_CORRUPT,
            HostFailureCode.ARTIFACT_NOT_FOUND,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
    "dqop_resource": frozenset(
        {
            HostFailureCode.INVALID_TOOL_REQUEST,
            HostFailureCode.REFERENCE_NOT_FOUND,
            HostFailureCode.REFERENCE_SCOPE_DENIED,
            HostFailureCode.RECORD_CORRUPT,
            HostFailureCode.ARTIFACT_NOT_FOUND,
            HostFailureCode.RESULT_LIMIT_EXCEEDED,
            HostFailureCode.INTERNAL_FAILURE,
        }
    ),
}


class _BinaryAuditHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        payload = getattr(record, "defined_quant_audit", None)
        if not isinstance(payload, dict):
            return
        try:
            text = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            write_utf8_lf_frame(2, text)
        except Exception:
            return


def _configure_audit() -> logging.Logger:
    for name in tuple(logging.Logger.manager.loggerDict):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = False
        logger.disabled = True
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.CRITICAL + 1)
    audit = logging.getLogger("defined_quant_mcp.audit")
    audit.disabled = False
    audit.handlers[:] = [_BinaryAuditHandler()]
    audit.propagate = False
    audit.setLevel(logging.INFO)
    warnings.filterwarnings("ignore", module=r"^mcp(?:\.|$)")
    return audit


_AUDIT = logging.getLogger("defined_quant_mcp.audit")


def _audit(payload: Mapping[str, object]) -> None:
    _AUDIT.info("audit", extra={"defined_quant_audit": dict(payload)})


class _BoundedAsyncInput:
    """Async text-line shape expected by the SDK, backed by project-owned raw framing."""

    def __init__(self, output: _ExclusiveAsyncOutput, descriptor: int = 0) -> None:
        self._reader = BoundedBinaryFrameReader.from_descriptor(descriptor)
        self._output = output

    def __aiter__(self) -> _BoundedAsyncInput:
        return self

    async def __anext__(self) -> str:
        while True:
            frame = await anyio.to_thread.run_sync(
                self._reader.read_frame,
                abandon_on_cancel=True,
            )
            if frame is None:
                raise StopAsyncIteration
            try:
                json.loads(frame)
            except json.JSONDecodeError:
                await self._output.write(
                    '{"jsonrpc":"2.0","id":null,"error":'
                    '{"code":-32700,"message":"Parse error"}}\n'
                )
                continue
            return frame + "\n"


class _ExclusiveAsyncOutput:
    """One serialized binary wire writer while ambient stdout is diverted."""

    def __init__(self, descriptor: int = 1) -> None:
        self._descriptor = descriptor
        self._wire_descriptor = os.dup(descriptor)
        self._lock = Lock()
        diversion = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(diversion, descriptor)
            rebind_std_handle_to_fd(descriptor)
        except Exception:
            os.close(self._wire_descriptor)
            self._wire_descriptor = -1
            raise
        finally:
            os.close(diversion)

    def _write(self, content: bytes) -> None:
        with self._lock:
            view = memoryview(content)
            while view:
                written = os.write(self._wire_descriptor, view)
                if written <= 0:
                    raise OSError("MCP wire write failed")
                view = view[written:]

    async def write(self, content: str) -> None:
        await anyio.to_thread.run_sync(
            self._write,
            content.encode("utf-8", errors="strict"),
            abandon_on_cancel=True,
        )

    async def flush(self) -> None:
        await anyio.lowlevel.checkpoint()

    def close(self) -> None:
        with self._lock:
            if self._wire_descriptor < 0:
                return
            os.dup2(self._wire_descriptor, self._descriptor)
            rebind_std_handle_to_fd(self._descriptor)
            # A cancelled anyio file-write thread may still be blocked in os.write. Like the
            # pinned SDK's STDIO claim, never close and recycle its private descriptor; process
            # exit releases it immediately after this one-session server returns.
            self._wire_descriptor = -1


def _compact_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _validation_fields(model: type[BaseModel], exc: ValidationError) -> list[str]:
    allowed = set(model.model_fields)
    fields: list[str] = []
    for error in exc.errors(include_url=False, include_input=False):
        location = error.get("loc", ())
        selected = next(
            (
                item
                for item in location
                if isinstance(item, str) and item in allowed
            ),
            None,
        )
        if selected is not None and selected not in fields:
            fields.append(selected)
        if len(fields) == 32:
            break
    return fields


def _failure_result(
    surface: str,
    code: HostFailureCode,
    *,
    details: Mapping[str, Any] | None = None,
) -> types.CallToolResult:
    if code not in _ALLOWED_FAILURES[surface]:
        code = HostFailureCode.INTERNAL_FAILURE
        details = {}
    try:
        failure = host_failure(
            code,
            details=cast(Any, details or {}),
            trust=_FAILURE_TRUST[surface],
        )
    except (TypeError, ValueError, ValidationError):
        failure = host_failure(
            HostFailureCode.INTERNAL_FAILURE,
            trust=_FAILURE_TRUST[surface],
        )
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=failure.error.message)],
        structured_content=failure.model_dump(mode="json"),
        is_error=True,
    )


def _success_result(surface: str, data: Mapping[str, Any]) -> types.CallToolResult:
    envelope = host_success(data, trust=_SUCCESS_TRUST[surface]).model_dump(mode="json")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=HOST_SUCCESS_TEXT)],
        structured_content=envelope,
        is_error=False,
    )


def _tool_result_projection(result: types.CallToolResult) -> dict[str, Any]:
    return {
        "content": [item.model_dump(mode="json", by_alias=True) for item in result.content],
        "structuredContent": result.structured_content,
        "isError": result.is_error,
    }


def _bounded_tool_result(surface: str, result: types.CallToolResult) -> types.CallToolResult:
    maximum = MAX_SEARCH_RESULT_BYTES if surface == "search_components" else MAX_TOOL_RESULT_BYTES
    actual = len(_compact_bytes(_tool_result_projection(result)))
    if actual <= maximum:
        return result
    return _failure_result(
        surface,
        HostFailureCode.RESULT_LIMIT_EXCEEDED,
        details={
            "limit_name": (
                "search_response_bytes"
                if surface == "search_components"
                else "structured_tool_response_bytes"
            ),
            "maximum": maximum,
            "actual": actual,
        },
    )


def _search_projection(results: SearchResults) -> dict[str, Any]:
    facets: dict[str, Any] = {}
    for name in FACET_NAMES:
        ordered = sorted(
            results.facets[name],
            key=lambda item: (-item.count, item.value.encode("utf-8")),
        )
        facets[name] = {
            "values": [
                {"value": item.value, "count": item.count}
                for item in ordered[:10]
            ],
            "other_count": sum(item.count for item in ordered[10:]),
        }
    return {
        "compact": True,
        "query": results.query,
        "terms": list(results.terms),
        "total_matches": results.total_matches,
        "hits": [
            {
                "component_id": hit.record.component_id,
                "version": hit.record.version,
                "title": hit.record.metadata["title"],
                "lifecycle": hit.record.metadata["lifecycle"],
                "summary": hit.record.metadata["summary"],
                "score": hit.score,
                "matched_terms": list(hit.matched_terms),
                "unmatched_terms": list(hit.unmatched_terms),
                "positive_matches": [
                    {
                        "field": match.field,
                        "terms": list(match.terms),
                        "values": list(match.values),
                    }
                    for match in hit.positive_matches
                ],
                "boundary_matches": [
                    {
                        "field": match.field,
                        "terms": list(match.terms),
                        "values": list(match.values),
                    }
                    for match in hit.boundary_matches
                ],
            }
            for hit in results.hits
        ],
        "facets": facets,
        "truncated": results.total_matches > len(results.hits),
    }


async def _cancellable_call(call: Callable[[], _T], cancel_event: Event) -> _T:
    try:
        return await anyio.to_thread.run_sync(call, abandon_on_cancel=True)
    finally:
        cancel_event.set()


async def _dispatch(
    service: DefinedQuantService,
    request: BaseModel,
) -> Mapping[str, Any]:
    cancel_event = Event()
    if isinstance(request, SearchRequest):
        filters = DiscoveryFilters(
            **{
                facet: getattr(request.filters, facet)
                for facet in FACET_NAMES
            }
        )
        results = await _cancellable_call(
            lambda: service.search_components(
                request.query,
                filters=filters,
                limit=request.limit,
            ),
            cancel_event,
        )
        return _search_projection(results)
    if isinstance(request, InspectRequest):
        return await _cancellable_call(
            lambda: service.inspect_component(
                request.component_id,
                expected_version=request.expected_version,
                expected_subject_hash=request.expected_subject_hash,
                view=request.view,
                cancel_event=cancel_event,
            ),
            cancel_event,
        )
    if isinstance(request, DatasetRegistrationRequest):
        return await _cancellable_call(
            lambda: service.register_dataset(request),
            cancel_event,
        )
    if isinstance(request, DescribeDatasetRequest):
        return await _cancellable_call(
            lambda: service.describe_dataset(
                request.dataset_ref,
                view=request.view,
                cursor=request.cursor,
                limit=request.limit,
            ),
            cancel_event,
        )
    if isinstance(request, ComparePortsRequest):
        return await _cancellable_call(
            lambda: service.compare_ports(
                request.producer.model_dump(mode="json"),
                request.consumer.model_dump(mode="json"),
                cancel_event=cancel_event,
            ),
            cancel_event,
        )
    if isinstance(request, ExecuteComponentRequest):
        return await _cancellable_call(
            lambda: service.execute_component(
                request.component,
                literals=request.literals,
                sources=[source.model_dump(mode="json") for source in request.sources],
                provenance=request.provenance,
                artifacts=request.artifacts,
                cancel_event=cancel_event,
            ),
            cancel_event,
        )
    if isinstance(request, GetOperationRequest):
        return await _cancellable_call(
            lambda: service.get_operation(
                request.operation_ref,
                view=request.view,
                field=request.field,
                artifact_id=request.artifact_id,
                cursor=request.cursor,
                limit=request.limit,
            ),
            cancel_event,
        )
    raise RuntimeError("unreachable request model")


_TOOL_DESCRIPTIONS = {
    "search_components": "Search contract-only Defined Quant catalog metadata.",
    "inspect_component": "Inspect one exact installed component identity and schema.",
    "register_dataset": "Normalize and register caller-supplied data for this session.",
    "describe_dataset": "Read bounded metadata or a deliberate preview from a dataset record.",
    "compare_ports": "Compare one exact output semantic port with one exact input port.",
    "execute_component": "Execute one exact unmanaged component operation.",
    "get_operation": "Read a bounded verified projection of an operation record.",
}
_READ_ONLY_TOOLS = {
    "search_components",
    "inspect_component",
    "describe_dataset",
    "compare_ports",
    "get_operation",
}
_IDEMPOTENT_TOOLS = _READ_ONLY_TOOLS | {"execute_component"}


def _tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=name,
            description=_TOOL_DESCRIPTIONS[name],
            input_schema=model.model_json_schema(by_alias=True),
            annotations=types.ToolAnnotations(
                read_only_hint=name in _READ_ONLY_TOOLS,
                destructive_hint=False,
                idempotent_hint=name in _IDEMPOTENT_TOOLS,
                open_world_hint=False,
            ),
        )
        for name, model in REQUEST_MODELS.items()
    ]


def _resource_error(code: HostFailureCode, details: Mapping[str, Any] | None = None) -> MCPError:
    if code not in _ALLOWED_FAILURES["dqop_resource"]:
        code = HostFailureCode.INTERNAL_FAILURE
        details = {}
    try:
        failure = host_failure(code, details=cast(Any, details or {}))
    except (TypeError, ValueError, ValidationError):
        failure = host_failure(HostFailureCode.INTERNAL_FAILURE)
    return MCPError(
        -32002,
        failure.error.message,
        {
            "host_schema_version": HOST_SCHEMA_VERSION,
            "outcome": failure.outcome.value,
            "host_failure_code": failure.error.code.value,
            "retry_allowed": failure.error.retry_allowed,
            "details": failure.error.details,
            "trust": failure.trust.model_dump(mode="json"),
        },
    )


def create_server(service: DefinedQuantService) -> Server[Any]:
    """Create the exact low-level SDK server around one transport-neutral service."""

    tools = _tools()

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=tools,
            ttl_ms=0,
            cache_scope="private",
        )

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        request_id = secrets.token_hex(16)
        started = time.monotonic()
        arguments = params.arguments if params.arguments is not None else {}
        argument_bytes = 0
        outcome = HostFailureCode.INTERNAL_FAILURE.value
        if params.name not in REQUEST_MODELS:
            _audit(
                {
                    "event": "tool_call",
                    "request_id": request_id,
                    "outcome": "unknown_tool",
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
            raise MCPError(-32602, "Unknown tool name.")
        surface = params.name
        try:
            try:
                argument_bytes = len(_compact_bytes(arguments))
            except (TypeError, ValueError, UnicodeError):
                result = _failure_result(
                    surface,
                    HostFailureCode.INVALID_TOOL_REQUEST,
                    details={"fields": []},
                )
                outcome = HostFailureCode.INVALID_TOOL_REQUEST.value
                return result
            if argument_bytes > MAX_ARGUMENT_BYTES:
                result = _failure_result(
                    surface,
                    HostFailureCode.INPUT_LIMIT_EXCEEDED,
                    details={
                        "limit_name": "tool_arguments_bytes",
                        "maximum": MAX_ARGUMENT_BYTES,
                        "actual": argument_bytes,
                    },
                )
                outcome = HostFailureCode.INPUT_LIMIT_EXCEEDED.value
                return result
            requested_version = arguments.get("host_schema_version", 1)
            if type(requested_version) is not int:
                result = _failure_result(
                    surface,
                    HostFailureCode.INVALID_TOOL_REQUEST,
                    details={"fields": ["host_schema_version"]},
                )
                outcome = HostFailureCode.INVALID_TOOL_REQUEST.value
                return result
            if requested_version != 1:
                result = _failure_result(
                    surface,
                    HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
                    details={"requested_version": requested_version},
                )
                outcome = HostFailureCode.UNSUPPORTED_HOST_SCHEMA.value
                return result
            model = REQUEST_MODELS[surface]
            try:
                request = model.model_validate(arguments)
            except ValidationError as exc:
                result = _failure_result(
                    surface,
                    HostFailureCode.INVALID_TOOL_REQUEST,
                    details={"fields": _validation_fields(model, exc)},
                )
                outcome = HostFailureCode.INVALID_TOOL_REQUEST.value
                return result
            try:
                data = await _dispatch(service, request)
                result = _success_result(surface, data)
                outcome = "ok"
            except HostFailureException as exc:
                result = _failure_result(
                    surface,
                    exc.code,
                    details=exc.failure.error.details,
                )
                outcome = exc.code.value
            except ComponentContractError:
                result = _failure_result(surface, HostFailureCode.COMPONENT_CONTRACT_ERROR)
                outcome = HostFailureCode.COMPONENT_CONTRACT_ERROR.value
            except anyio.get_cancelled_exc_class():
                outcome = HostFailureCode.WORKER_CANCELLED.value
                raise
            except Exception:
                result = _failure_result(surface, HostFailureCode.INTERNAL_FAILURE)
                outcome = HostFailureCode.INTERNAL_FAILURE.value
            return _bounded_tool_result(surface, result)
        finally:
            _audit(
                {
                    "argument_bytes": argument_bytes,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "event": "tool_call",
                    "outcome": outcome,
                    "request_id": request_id,
                    "tool": surface,
                }
            )

    async def on_list_resources(_ctx: Any, _params: Any) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[],
            ttl_ms=0,
            cache_scope="private",
        )

    async def on_list_resource_templates(
        _ctx: Any, _params: Any
    ) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(
            resource_templates=[
                types.ResourceTemplate(
                    name="defined_quant_operation_artifact",
                    uri_template=RESOURCE_TEMPLATE,
                    description="One digest-checked artifact from an active operation record.",
                    annotations=types.Annotations(audience=["assistant"], priority=0.5),
                )
            ],
            ttl_ms=0,
            cache_scope="private",
        )

    async def on_read_resource(
        _ctx: Any, params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        uri = params.uri
        if not uri.startswith("dqop:"):
            raise MCPError(-32002, "Resource URI is not available.")
        match = _RESOURCE_URI.fullmatch(uri)
        if match is None:
            raise _resource_error(
                HostFailureCode.INVALID_TOOL_REQUEST,
                {"fields": []},
            )
        reference = f"dqop:v1:{match.group('operation')}"
        artifact_id = match.group("artifact")
        try:
            artifact = await anyio.to_thread.run_sync(
                lambda: service.read_operation_artifact(reference, artifact_id),
                abandon_on_cancel=True,
            )
        except HostFailureException as exc:
            raise _resource_error(exc.code, exc.failure.error.details) from None
        except anyio.get_cancelled_exc_class():
            raise
        except Exception:
            raise _resource_error(HostFailureCode.INTERNAL_FAILURE) from None
        result = types.ReadResourceResult(
            contents=[
                types.BlobResourceContents(
                    uri=uri,
                    mime_type=artifact.media_type,
                    blob=base64.b64encode(artifact.content).decode("ascii"),
                    _meta={
                        "sha256": artifact.sha256,
                        "size_bytes": len(artifact.content),
                        "execution_mode": "unmanaged",
                        "trust": host_success(
                            {}, trust=TrustLabel.UNMANAGED_EXECUTION
                        ).trust.model_dump(mode="json"),
                    },
                )
            ],
            ttl_ms=0,
            cache_scope="private",
        )
        actual = len(_compact_bytes(result.model_dump(mode="json", by_alias=True)))
        if actual > MAX_RESOURCE_FRAME_BYTES:
            raise _resource_error(
                HostFailureCode.RESULT_LIMIT_EXCEEDED,
                {
                    "limit_name": "resource_response_frame_bytes",
                    "maximum": MAX_RESOURCE_FRAME_BYTES,
                    "actual": actual,
                },
            )
        return result

    server: Server[Any] = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INITIALIZATION_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_list_resource_templates=on_list_resource_templates,
        on_read_resource=on_read_resource,
    )
    server.middleware[:] = [
        middleware
        for middleware in server.middleware
        if not isinstance(middleware, OpenTelemetryMiddleware)
    ]
    if any(isinstance(item, OpenTelemetryMiddleware) for item in server.middleware):
        raise RuntimeError("telemetry middleware removal failed")
    static_objects = {
        "tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools],
        "resource_templates": [
            {
                "name": "defined_quant_operation_artifact",
                "uriTemplate": RESOURCE_TEMPLATE,
            }
        ],
    }
    if len(_compact_bytes(static_objects)) > MAX_STATIC_FRAME_BYTES:
        raise RuntimeError("static MCP listing exceeds its frozen limit")
    return server


async def run_stdio(service: DefinedQuantService) -> None:
    """Serve one process-lifetime local session through bounded binary STDIO."""

    configure_binary_descriptors(0, 1, 2)
    global _AUDIT
    _AUDIT = _configure_audit()
    server = create_server(service)
    output = _ExclusiveAsyncOutput(1)
    bounded_input = _BoundedAsyncInput(output, 0)
    try:
        async with stdio_server(
            stdin=cast(Any, bounded_input),
            stdout=cast(Any, output),
        ) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
                raise_exceptions=False,
            )
    except BinaryFrameError as exc:
        _audit({"code": exc.code.value, "event": "transport_failure"})
    except BaseExceptionGroup as group:
        framing_errors = [
            exception
            for exception in _leaf_exceptions(group)
            if isinstance(exception, BinaryFrameError)
        ]
        if len(framing_errors) != len(_leaf_exceptions(group)) or not framing_errors:
            raise
        _audit(
            {
                "code": framing_errors[0].code.value,
                "event": "transport_failure",
            }
        )
    finally:
        output.close()


def _leaf_exceptions(group: BaseExceptionGroup) -> list[BaseException]:
    leaves: list[BaseException] = []
    for exception in group.exceptions:
        if isinstance(exception, BaseExceptionGroup):
            leaves.extend(_leaf_exceptions(exception))
        else:
            leaves.append(exception)
    return leaves


def _paths(arguments: Sequence[str]) -> tuple[Path | None, tuple[Path, ...], Path | None]:
    catalog: Path | None = None
    data_roots: list[Path] = []
    state: Path | None = None
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option not in {"--catalog-root", "--data-root", "--state-root"}:
            raise ValueError
        index += 1
        if index == len(arguments):
            raise ValueError
        value = arguments[index]
        if not value or "\x00" in value:
            raise ValueError
        path = Path(value)
        if option == "--catalog-root" and catalog is None:
            catalog = path
        elif option == "--data-root" and len(data_roots) < 8:
            data_roots.append(path)
        elif option == "--state-root" and state is None:
            state = path
        else:
            raise ValueError
        index += 1
    return catalog, tuple(data_roots), state


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the console entry point without reflecting launch values on STDERR."""

    configure_binary_descriptors(0, 1, 2)
    global _AUDIT
    _AUDIT = _configure_audit()
    try:
        catalog, data_roots, state = _paths(
            tuple(sys.argv[1:] if arguments is None else arguments)
        )
        service = DefinedQuantService(
            catalog_root=catalog,
            data_roots=data_roots,
            session_state_root=state,
        )
        service.contract_index
    except Exception:
        _audit({"code": "invalid_launch_configuration", "event": "startup_failure"})
        return 64
    try:
        anyio.run(run_stdio, service)
        return 0
    except Exception:
        _audit({"code": "internal_failure", "event": "transport_failure"})
        return 70
    finally:
        try:
            service.close()
        except Exception:
            _audit({"code": "cleanup_failure", "event": "shutdown_failure"})


__all__ = [
    "INITIALIZATION_INSTRUCTIONS",
    "RESOURCE_TEMPLATE",
    "SERVER_NAME",
    "SERVER_VERSION",
    "create_server",
    "main",
    "run_stdio",
]
