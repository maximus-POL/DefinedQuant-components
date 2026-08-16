"""Official-client and raw-wire tests for the separate STDIO MCP distribution."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from defined_quant.host_failures import HostFailureCode
from defined_quant.service import DefinedQuantService
from mcp import StdioServerParameters, stdio_client
from mcp.client import Client
from mcp.server._otel import OpenTelemetryMiddleware
from mcp.shared.exceptions import MCPError

from defined_quant_mcp.server import (
    _ALLOWED_FAILURES,
    INITIALIZATION_INSTRUCTIONS,
    RESOURCE_TEMPLATE,
    create_server,
)

_TOOLS = (
    "search_components",
    "inspect_component",
    "register_dataset",
    "describe_dataset",
    "compare_ports",
    "execute_component",
    "get_operation",
)
_PROVENANCE = {
    "source_kind": "synthetic",
    "interpretation_method": "caller_structured",
    "label": "Synthetic official-client MCP test data.",
}


@contextmanager
def _audit_file() -> Iterator[Any]:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stream:
        yield stream


@asynccontextmanager
async def _client(
    audit: Any,
    *,
    arguments: list[str] | None = None,
    mode: str = "auto",
) -> AsyncIterator[Client]:
    params = StdioServerParameters(
        command=sys.executable,
        args=arguments or ["-m", "defined_quant_mcp"],
        cwd=Path.cwd(),
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    async with Client(stdio_client(params, errlog=audit), mode=mode) as client:
        yield client


def _registration() -> dict[str, Any]:
    return {
        "source": {
            "kind": "inline_rows",
            "rows": [
                {"price": 100.0},
                {"price": 110.0},
                {"price": 121.0},
            ],
        },
        "columns": [
            {
                "source_name": "price",
                "field_id": "price",
                "data_type": "number",
                "role": "value",
            }
        ],
        "semantics": {
            "ordering": "preserve_source_order",
            "price_kind": "adjusted",
        },
        "provenance": _PROVENANCE,
        "external_preprocessing": {"status": "none_declared"},
    }


def _assert_success(result: Any) -> dict[str, Any]:
    assert result.is_error is False, result.structured_content
    assert len(result.content) == 1
    assert result.content[0].text == "Defined Quant host outcome: ok; use structuredContent."
    assert result.structured_content["host_schema_version"] == 1
    assert result.structured_content["outcome"] == "ok"
    return result.structured_content["data"]


def _assert_audit_records(stream: Any) -> list[dict[str, Any]]:
    stream.seek(0)
    lines = stream.read().splitlines()
    records = [json.loads(line) for line in lines]
    for record in records:
        assert set(record) <= {
            "argument_bytes",
            "code",
            "elapsed_ms",
            "event",
            "outcome",
            "request_id",
            "tool",
        }
        assert "path" not in json.dumps(record).lower()
    return records


def test_failure_allowlists_match_the_normative_fixture_exactly() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/local_mcp/host_failures.v1.json")
        .read_text(encoding="utf-8")
    )
    assert {
        surface: {code.value for code in codes}
        for surface, codes in _ALLOWED_FAILURES.items()
    } == {
        surface: set(codes)
        for surface, codes in fixture["allowed_codes_by_surface"].items()
    }
    assert all(
        isinstance(code, HostFailureCode)
        for codes in _ALLOWED_FAILURES.values()
        for code in codes
    )


def test_sdk_telemetry_middleware_is_removed() -> None:
    service = DefinedQuantService()
    try:
        server = create_server(service)
        assert not any(
            isinstance(middleware, OpenTelemetryMiddleware)
            for middleware in server.middleware
        )
    finally:
        service.close()


def test_official_client_initialization_every_tool_and_resource(tmp_path: Path) -> None:
    async def story() -> None:
        state = tmp_path / ("żółć-state-" + ("long-segment-" * 12))
        with _audit_file() as audit:
            async with _client(
                audit,
                arguments=["-m", "defined_quant_mcp", "--state-root", str(state)],
            ) as client:
                assert client.protocol_version == "2026-07-28"
                assert client.server_info is not None
                assert client.server_info.name == "defined-quant-mcp"
                assert client.server_info.version == "0.1.0a1"
                assert client.instructions == INITIALIZATION_INSTRUCTIONS

                tools = await client.list_tools(cache_mode="reload")
                assert tuple(tool.name for tool in tools.tools) == _TOOLS
                for tool in tools.tools:
                    assert tool.input_schema["type"] == "object"
                    assert tool.input_schema["additionalProperties"] is False
                    assert tool.annotations is not None
                    assert tool.annotations.destructive_hint is False
                    assert tool.annotations.open_world_hint is False
                assert tools.ttl_ms == 0
                assert tools.cache_scope == "private"

                resources = await client.list_resources(cache_mode="reload")
                assert resources.resources == []
                templates = await client.list_resource_templates(cache_mode="reload")
                assert len(templates.resource_templates) == 1
                template = templates.resource_templates[0]
                assert template.uri_template == RESOURCE_TEMPLATE
                assert template.annotations is not None
                assert template.annotations.audience == ["assistant"]
                assert template.annotations.priority == 0.5

                search = _assert_success(
                    await client.call_tool(
                        "search_components",
                        {"query": "simple return", "limit": 1},
                    )
                )
                assert search["hits"][0]["component_id"] == "dq.market_data.simple_return"

                simple = _assert_success(
                    await client.call_tool(
                        "inspect_component",
                        {"component_id": "dq.market_data.simple_return"},
                    )
                )
                logarithmic = _assert_success(
                    await client.call_tool(
                        "inspect_component",
                        {"component_id": "dq.market_data.log_return"},
                    )
                )
                volatility = _assert_success(
                    await client.call_tool(
                        "inspect_component",
                        {"component_id": "dq.volatility.historical_volatility"},
                    )
                )
                compatibility = _assert_success(
                    await client.call_tool(
                        "compare_ports",
                        {
                            "producer": {
                                "component": logarithmic["component"],
                                "field": "returns",
                            },
                            "consumer": {
                                "component": volatility["component"],
                                "field": "returns",
                            },
                        },
                    )
                )
                assert compatibility["compatible"] is True

                registration = _assert_success(
                    await client.call_tool("register_dataset", _registration())
                )
                dataset_ref = registration["dataset_ref"]
                description = _assert_success(
                    await client.call_tool(
                        "describe_dataset",
                        {"dataset_ref": dataset_ref},
                    )
                )
                assert description["dataset_ref"] == dataset_ref

                execution = _assert_success(
                    await client.call_tool(
                        "execute_component",
                        {
                            "component": simple["component"],
                            "literals": {"price_kind": "adjusted"},
                            "sources": [
                                {
                                    "kind": "dataset",
                                    "ref": dataset_ref,
                                    "mappings": [
                                        {
                                            "source_field": "price",
                                            "input_field": "prices",
                                        }
                                    ],
                                }
                            ],
                            "artifacts": "svg_all",
                        },
                    )
                )
                operation_ref = execution["operation_ref"]
                operation = _assert_success(
                    await client.call_tool(
                        "get_operation",
                        {"operation_ref": operation_ref},
                    )
                )
                assert operation["operation_ref"] == operation_ref

                artifact_metadata = execution["artifacts"][0]
                resource = await client.read_resource(
                    artifact_metadata["resource_uri"],
                    cache_mode="reload",
                )
                assert resource.ttl_ms == 0
                assert resource.cache_scope == "private"
                assert len(resource.contents) == 1
                content = resource.contents[0]
                decoded = base64.b64decode(content.blob, validate=True)
                assert hashlib.sha256(decoded).hexdigest() == artifact_metadata["sha256"]
                assert content.meta["execution_mode"] == "unmanaged"

            records = _assert_audit_records(audit)
            assert {record.get("tool") for record in records} == set(_TOOLS)

        if state.exists():
            assert not tuple(state.glob("session-*"))

    anyio.run(story)


def test_official_client_safe_failure_envelopes_and_resource_errors() -> None:
    async def story() -> None:
        with _audit_file() as audit:
            async with _client(audit) as client:
                invalid = await client.call_tool(
                    "search_components",
                    {"query": "return", "limit": 6},
                )
                assert invalid.is_error is True
                failure = invalid.structured_content
                assert failure["error"] == {
                    "code": "invalid_tool_request",
                    "message": "The request does not satisfy the closed tool schema.",
                    "retry_allowed": False,
                    "details": {"fields": ["limit"]},
                }
                assert invalid.content[0].text == failure["error"]["message"]

                unsupported = await client.call_tool(
                    "get_operation",
                    {
                        "host_schema_version": 2,
                        "operation_ref": "dqop:v1:" + "a" * 64,
                    },
                )
                assert unsupported.structured_content["error"]["code"] == (
                    "unsupported_host_schema"
                )

                with pytest.raises(MCPError) as unknown:
                    await client.call_tool("not_a_defined_quant_tool", {})
                assert unknown.value.code == -32602
                assert unknown.value.message == "Unknown tool name."
                assert unknown.value.data is None

                with pytest.raises(MCPError) as unavailable:
                    await client.read_resource("file:///tmp/not-available", cache_mode="reload")
                assert unavailable.value.code == -32002
                assert unavailable.value.message == "Resource URI is not available."
                assert unavailable.value.data is None

                with pytest.raises(MCPError) as malformed:
                    await client.read_resource("dqop://broken", cache_mode="reload")
                assert malformed.value.code == -32002
                assert malformed.value.data["host_failure_code"] == "invalid_tool_request"
                assert malformed.value.data["trust"]["label"] == "no_verified_result"

    anyio.run(story)


def test_unexpected_service_exception_is_redacted_on_wire_and_stderr() -> None:
    secret = "SENSITIVE_EXCEPTION_TEXT_/private/secret.csv"
    code = f"""
import anyio
from defined_quant.service import DefinedQuantService
from defined_quant_mcp.server import run_stdio
class FailingService(DefinedQuantService):
    def search_components(self, *args, **kwargs):
        print({secret!r}, flush=True)
        raise RuntimeError({secret!r})
service = FailingService()
try:
    anyio.run(run_stdio, service)
finally:
    service.close()
"""

    async def story() -> None:
        with _audit_file() as audit:
            async with _client(audit, arguments=["-c", code]) as client:
                result = await client.call_tool("search_components", {"query": "return"})
                assert result.is_error is True
                assert result.structured_content["error"]["code"] == "internal_failure"
                wire_projection = json.dumps(result.model_dump(mode="json"))
                assert secret not in wire_projection
                assert "RuntimeError" not in wire_projection
            audit.seek(0)
            stderr = audit.read()
            assert secret not in stderr
            assert "RuntimeError" not in stderr
            assert "Traceback" not in stderr
            records = [json.loads(line) for line in stderr.splitlines()]
            assert records[-1]["outcome"] == "internal_failure"

    anyio.run(story)


def test_official_client_cancellation_keeps_session_usable_and_cleans_up(tmp_path: Path) -> None:
    code = """
import anyio
import time
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.service import DefinedQuantService
from defined_quant_mcp.server import run_stdio
class SlowService(DefinedQuantService):
    def execute_component(self, *args, cancel_event=None, **kwargs):
        while cancel_event is not None and not cancel_event.is_set():
            time.sleep(0.01)
        raise HostFailureException(HostFailureCode.WORKER_CANCELLED)
service = SlowService(session_state_root=__import__('pathlib').Path(__import__('sys').argv[1]))
try:
    anyio.run(run_stdio, service)
finally:
    service.close()
"""

    async def story() -> None:
        state = tmp_path / "cancel-state"
        with _audit_file() as audit:
            async with _client(
                audit,
                arguments=["-c", code, str(state)],
                mode="legacy",
            ) as client:
                scope = anyio.CancelScope()

                async def invoke() -> None:
                    with scope:
                        await client.call_tool(
                            "execute_component",
                            {
                                "component": {
                                    "id": "dq.market_data.simple_return",
                                    "version": "0.3.4",
                                    "subject_hash": "a" * 64,
                                },
                                "literals": {
                                    "prices": [100.0, 101.0],
                                    "price_kind": "adjusted",
                                },
                                "provenance": _PROVENANCE,
                            },
                        )

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(invoke)
                    await anyio.sleep(0.2)
                    scope.cancel()
                search = await client.call_tool(
                    "search_components",
                    {"query": "return", "limit": 1},
                )
                assert search.is_error is False
            records = _assert_audit_records(audit)
            assert any(record.get("outcome") == "worker_cancelled" for record in records)
        if state.exists():
            assert not tuple(state.glob("session-*"))

    anyio.run(story)


def test_raw_malformed_json_returns_standard_parse_error() -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "defined_quant_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "raw-test", "version": "1"},
        },
    }
    process.stdin.write(json.dumps(initialize).encode("utf-8") + b"\n")
    process.stdin.flush()
    initialized = json.loads(process.stdout.readline())
    assert initialized["id"] == 1
    assert "result" in initialized
    process.stdin.write(b"not-json\n")
    process.stdin.flush()
    response = json.loads(process.stdout.readline())
    assert response["error"]["code"] == -32700
    process.stdin.close()
    assert process.wait(timeout=20) == 0


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (b"\xff\n", "invalid_utf8"),
        (b" " * (2 * 1024 * 1024) + b"x\n", "frame_too_large"),
    ],
    ids=("invalid-utf8", "overlong"),
)
def test_raw_invalid_or_overlong_input_terminates_without_response(
    content: bytes,
    expected_code: str,
) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "defined_quant_mcp"],
        input=content,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0
    assert completed.stdout == b""
    records = [json.loads(line) for line in completed.stderr.splitlines()]
    assert records == [{"code": expected_code, "event": "transport_failure"}]
