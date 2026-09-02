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
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.local_host_platform import (
    SecureFilesystemError,
    SecureFilesystemErrorCode,
)
from defined_quant.run_views import MAX_ARTIFACT_CHUNK_BYTES
from defined_quant.service import (
    MAX_METHOD_ARTIFACT_READ_BYTES,
    DefinedQuantService,
    OperationArtifact,
)
from mcp import StdioServerParameters, stdio_client
from mcp.client import Client
from mcp.server._otel import OpenTelemetryMiddleware
from mcp.shared.exceptions import MCPError

from defined_quant_mcp import server as server_module
from defined_quant_mcp.models import CompilePlanRequest, ReadArtifactRequest
from defined_quant_mcp.server import (
    _ALLOWED_FAILURES,
    INITIALIZATION_INSTRUCTIONS,
    MAX_SEARCH_RESULT_BYTES,
    MAX_TOOL_RESULT_BYTES,
    RESOURCE_TEMPLATE,
    _bounded_tool_result,
    _compact_bytes,
    _dispatch,
    _success_result,
    _tool_result_projection,
    create_server,
    main,
)

_TOOLS = (
    "search_methods",
    "inspect_method",
    "compile_plan",
    "execute_plan",
    "get_plan",
    "get_run",
    "get_dataset",
    "read_artifact",
    "register_dataset",
    "search_components",
    "inspect_component",
    "describe_dataset",
    "compare_ports",
    "compile_component_plan",
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


def _assert_failure(result: Any, code: str) -> dict[str, Any]:
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == code
    assert result.content[0].text == result.structured_content["error"]["message"]
    return result.structured_content["error"]


def _minimal_tool_arguments() -> dict[str, dict[str, Any]]:
    component = {
        "id": "dq.market_data.simple_return",
        "version": "0.3.4",
        "subject_hash": "a" * 64,
    }
    return {
        "search_methods": {"query": "return"},
        "inspect_method": {"method_id": "dq.market_data.simple_return"},
        "compile_plan": {
            "proposal": {
                "method_id": "dq.market_data.simple_return",
                "method_version": "1.0.0",
                "financial_inputs": {"prices": [100.0, 101.0]},
                "conventions": {"price_kind": "adjusted"},
            }
        },
        "execute_plan": {"plan_ref": "dqplan:" + "a" * 64},
        "get_plan": {"plan_ref": "dqplan:" + "a" * 64},
        "get_run": {"run_ref": "dqrun:" + "a" * 64},
        "get_dataset": {"dataset_ref": "dqds:v1:" + "a" * 64},
        "read_artifact": {
            "run_ref": "dqrun:" + "a" * 64,
            "artifact_id": "returns_chart",
        },
        "search_components": {"query": "return"},
        "inspect_component": {"component_id": "dq.market_data.simple_return"},
        "register_dataset": _registration(),
        "describe_dataset": {"dataset_ref": "dqds:v1:" + "a" * 64},
        "compare_ports": {
            "producer": {"component": component, "field": "returns"},
            "consumer": {"component": component, "field": "prices"},
        },
        "compile_component_plan": {
            "proposal": {
                "method_id": "dq.market_data.simple_return",
                "method_version": "0.3.4",
                "financial_inputs": {"prices": [100.0, 101.0]},
                "conventions": {"price_kind": "adjusted"},
            }
        },
        "execute_component": {
            "component": component,
            "literals": {"prices": [100.0, 101.0], "price_kind": "adjusted"},
            "provenance": _PROVENANCE,
        },
        "get_operation": {"operation_ref": "dqop:v1:" + "a" * 64},
    }


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
            "provider_code",
            "request_id",
            "tool",
        }
        assert "path" not in json.dumps(record).lower()
    return records


def test_startup_failures_distinguish_arguments_capabilities_and_internal_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[dict[str, object]] = []
    monkeypatch.setattr(server_module, "_configure_audit", lambda: None)
    monkeypatch.setattr(server_module, "_audit", lambda payload: records.append(dict(payload)))
    monkeypatch.setattr(server_module, "configure_binary_descriptors", lambda *_args: None)

    assert main(["--unknown", "value"]) == 64
    assert records == [
        {"code": "invalid_launch_configuration", "event": "startup_failure"}
    ]

    records.clear()

    def unavailable_service(**_arguments: Any) -> None:
        raise SecureFilesystemError(SecureFilesystemErrorCode.CAPABILITY_UNAVAILABLE)

    monkeypatch.setattr(server_module, "DefinedQuantService", unavailable_service)
    assert main([]) == 69
    assert records == [
        {
            "code": "host_capability_unavailable",
            "event": "startup_failure",
            "provider_code": "capability_unavailable",
        }
    ]

    records.clear()

    def broken_service(**_arguments: Any) -> None:
        raise RuntimeError("must not cross the audit boundary")

    monkeypatch.setattr(server_module, "DefinedQuantService", broken_service)
    assert main([]) == 70
    assert records == [
        {"code": "internal_failure", "event": "startup_failure"}
    ]


def test_failure_allowlists_match_the_normative_fixture_exactly() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/local_mcp/host_failures.json")
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


def test_compile_plan_tool_schema_excludes_host_receipts_and_caller_automatic_mode() -> None:
    tool = next(item for item in server_module._tools() if item.name == "compile_plan")
    schema = json.dumps(tool.input_schema, sort_keys=True)

    assert "origin_receipt" not in schema
    assert "session_binding_hash" not in schema
    assert '"automatic"' not in schema
    assert '"user_explicit"' in schema
    assert all(
        field not in schema
        for field in (
            "api_key",
            "connection_string",
            "credentials",
            "executable_code",
            "raw_sql",
        )
    )

    constraint = {
        "constraint_id": "runtime_choice",
        "scope": {"all_steps": True},
        "dimension": "backend",
        "backend_role": "runtime",
        "mode": "required",
        "targets": ["dq_native"],
        "asserted_origin": "user_explicit",
        "fallback": "forbidden",
    }
    proposal = {
        "method_id": "dq.market_data.simple_return",
        "method_version": "1.0.0",
        "financial_inputs": {"prices": [100.0, 101.0]},
        "conventions": {"price_kind": "adjusted"},
        "resolution_constraints": {"constraints": [constraint]},
    }
    assert CompilePlanRequest.model_validate({"proposal": proposal}).proposal.to_protocol()

    with pytest.raises(ValueError):
        CompilePlanRequest.model_validate(
            {
                "proposal": {
                    **proposal,
                    "resolution_constraints": {
                        "constraints": [
                            {
                                **constraint,
                                "origin_receipt": {"constraint_hash": "a" * 64},
                            }
                        ]
                    },
                }
            }
        )
    with pytest.raises(ValueError):
        CompilePlanRequest.model_validate(
            {
                "proposal": {
                    **proposal,
                    "resolution_constraints": {
                        "constraints": [
                            {
                                **constraint,
                                "mode": "automatic",
                                "targets": [],
                            }
                        ]
                    },
                }
            }
        )

    for prohibited_field, value in (
        ("provider_url", "https://example.invalid"),
        ("api_key", "secret"),
        ("raw_sql", "select 1"),
        ("executable_code", "import os"),
    ):
        with pytest.raises(ValueError):
            CompilePlanRequest.model_validate(
                {
                    "proposal": {
                        **proposal,
                        "financial_inputs": {
                            "prices": [100.0, 101.0],
                            prohibited_field: value,
                        },
                    }
                }
            )


def test_agent_visible_preference_requires_a_trusted_host_origin_receipt() -> None:
    request = CompilePlanRequest.model_validate(
        {
            "proposal": {
                "method_id": "dq.market_data.simple_return",
                "method_version": "1.0.0",
                "financial_inputs": {"prices": [100.0, 101.0]},
                "conventions": {"price_kind": "adjusted"},
                "resolution_constraints": {
                    "constraints": [
                        {
                            "constraint_id": "runtime_choice",
                            "scope": {"all_steps": True},
                            "dimension": "backend",
                            "backend_role": "runtime",
                            "mode": "required",
                            "targets": ["dq_native"],
                            "asserted_origin": "user_explicit",
                            "fallback": "forbidden",
                        }
                    ]
                },
            }
        }
    )
    with DefinedQuantService() as service:
        outcome = service.compile_plan(request.proposal.to_protocol())

    assert outcome.status == "needs_information"
    assert outcome.questions[0].code == "origin_confirmation"
    assert outcome.resolution_attempts == ()


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

                method_search = _assert_success(
                    await client.call_tool(
                        "search_methods",
                        {"query": "simple return", "limit": 1},
                    )
                )
                assert method_search["hits"][0]["method_id"] == (
                    "dq.market_data.simple_return"
                )
                method = _assert_success(
                    await client.call_tool(
                        "inspect_method",
                        {"method_id": "dq.market_data.simple_return"},
                    )
                )
                assert method["method"]["id"] == "dq.market_data.simple_return"

                compiled = _assert_success(
                    await client.call_tool(
                        "compile_plan",
                        {
                            "proposal": {
                                "method_id": "dq.market_data.simple_return",
                                "method_version": "1.0.0",
                                "financial_inputs": {"prices": [100.0, 110.0, 121.0]},
                                "conventions": {"price_kind": "adjusted"},
                                "agent_rationale": "Compile the selected return method.",
                            }
                        },
                    )
                )
                assert compiled["status"] == "compiled"
                assert compiled["steps"][0]["implementation"]["id"] == (
                    "dq_native.simple_return"
                )
                plan_ref = compiled["plan_ref"]
                plan_record = _assert_success(
                    await client.call_tool("get_plan", {"plan_ref": plan_ref})
                )
                assert plan_record["plan_ref"] == plan_ref
                run = _assert_success(
                    await client.call_tool("execute_plan", {"plan_ref": plan_ref})
                )
                assert run["status"] == "succeeded"
                assert run["compact"] is True
                assert run["record_available"] is True
                assert "canonical_method_output" not in run
                run_ref = run["run_ref"]
                retained_run = _assert_success(
                    await client.call_tool("get_run", {"run_ref": run_ref})
                )
                assert retained_run["run_ref"] == run_ref
                assert retained_run["view"] == "summary"
                assert retained_run["output"]["present"] is True
                assert "canonical_method_output" not in retained_run

                first_returns = _assert_success(
                    await client.call_tool(
                        "get_run",
                        {
                            "run_ref": run_ref,
                            "view": "output",
                            "field": "returns",
                            "limit": 1,
                        },
                    )
                )
                assert first_returns["items"] == [0.1]
                assert first_returns["page"]["complete"] is False
                returns_cursor = first_returns["page"]["next_cursor"]
                assert isinstance(returns_cursor, str)
                final_returns = _assert_success(
                    await client.call_tool(
                        "get_run",
                        {
                            "run_ref": run_ref,
                            "view": "output",
                            "field": "returns",
                            "cursor": returns_cursor,
                            "limit": 1,
                        },
                    )
                )
                assert final_returns["items"] == [0.1]
                assert final_returns["page"] == {
                    "start": 1,
                    "returned": 1,
                    "total": 2,
                    "complete": True,
                    "next_cursor": None,
                }

                rebound_cursor = await client.call_tool(
                    "get_run",
                    {
                        "run_ref": run_ref,
                        "view": "warnings",
                        "cursor": returns_cursor,
                        "limit": 1,
                    },
                )
                _assert_failure(rebound_cursor, "invalid_tool_request")
                missing_run_artifact = await client.call_tool(
                    "read_artifact",
                    {"run_ref": run_ref, "artifact_id": "returns_chart"},
                )
                _assert_failure(missing_run_artifact, "artifact_not_found")

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

                legacy_compiled = _assert_success(
                    await client.call_tool(
                        "compile_component_plan",
                        {
                            "proposal": {
                                "method_id": "dq.market_data.simple_return",
                                "method_version": "0.3.4",
                                "financial_inputs": {"prices": [100.0, 110.0]},
                                "conventions": {"price_kind": "adjusted"},
                                "agent_rationale": "Compile the inspected return method.",
                            }
                        },
                    )
                )
                assert legacy_compiled["status"] == "compiled"
                assert legacy_compiled["claims"] == [
                    "PLAN VALIDATION PASSED",
                    "ELIGIBLE UNDER POLICY",
                ]
                assert legacy_compiled["resolution_receipts"][0][
                    "runtime_fallback_allowed"
                ] is False

                unknown_method = _assert_success(
                    await client.call_tool(
                        "compile_plan",
                        {
                            "proposal": {
                                "method_id": "dq.market_data.not_installed",
                                "method_version": "1.0.0",
                                "financial_inputs": {},
                                "conventions": {},
                            }
                        },
                    )
                )
                assert unknown_method["status"] == "refused"
                assert unknown_method["code"] == "method_not_found"

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
                canonical_dataset = _assert_success(
                    await client.call_tool(
                        "get_dataset",
                        {"dataset_ref": dataset_ref},
                    )
                )
                assert canonical_dataset["dataset_ref"] == dataset_ref

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


def test_every_tool_rejects_extra_fields_and_handles_host_schema_versions() -> None:
    async def story() -> None:
        with _audit_file() as audit:
            async with _client(audit) as client:
                for tool, valid in _minimal_tool_arguments().items():
                    closed = await client.call_tool(tool, {**valid, "unexpected": True})
                    error = _assert_failure(closed, "invalid_tool_request")
                    assert error["details"] == {"fields": []}

                    unsupported = await client.call_tool(
                        tool,
                        {"host_schema_version": 2},
                    )
                    error = _assert_failure(unsupported, "unsupported_host_schema")
                    assert error["details"] == {"requested_version": 2}

                    wrong_type = await client.call_tool(
                        tool,
                        {"host_schema_version": "1"},
                    )
                    error = _assert_failure(wrong_type, "invalid_tool_request")
                    assert error["details"] == {"fields": ["host_schema_version"]}

    anyio.run(story)


@pytest.mark.parametrize(
    ("surface", "maximum", "limit_name"),
    [
        ("search_components", MAX_SEARCH_RESULT_BYTES, "search_response_bytes"),
        ("inspect_component", MAX_TOOL_RESULT_BYTES, "structured_tool_response_bytes"),
    ],
)
def test_tool_response_ceilings_accept_exact_bytes_and_refuse_the_next_byte(
    surface: str,
    maximum: int,
    limit_name: str,
) -> None:
    empty = _success_result(surface, {"payload": ""})
    overhead = len(_compact_bytes(_tool_result_projection(empty)))
    exact = _success_result(surface, {"payload": "x" * (maximum - overhead)})
    assert len(_compact_bytes(_tool_result_projection(exact))) == maximum
    assert _bounded_tool_result(surface, exact) is exact

    over = _success_result(surface, {"payload": "x" * (maximum - overhead + 1)})
    failure = _bounded_tool_result(surface, over)
    error = _assert_failure(failure, "result_limit_exceeded")
    assert error["details"] == {
        "actual": maximum + 1,
        "limit_name": limit_name,
        "maximum": maximum,
    }


def test_large_governed_run_is_retained_and_retrievable_in_bounded_pages() -> None:
    prices = [100.0 if index % 2 == 0 else 101.0 for index in range(10_001)]
    with DefinedQuantService() as service:
        compiled = service.compile_plan(
            {
                "method_id": "dq.market_data.simple_return",
                "method_version": "1.0.0",
                "financial_inputs": {"prices": prices},
                "conventions": {"price_kind": "adjusted"},
            }
        )
        assert compiled.status == "compiled"

        receipt = service.execute_plan(compiled.ref)
        assert receipt["status"] == "succeeded"
        assert "canonical_method_output" not in receipt
        receipt_result = _success_result("execute_plan", receipt)
        assert _bounded_tool_result("execute_plan", receipt_result) is receipt_result

        retained = service._methods_runtime.get_run(receipt["run_ref"])
        complete_result = _success_result(
            "get_run",
            retained.model_dump(mode="json"),
        )
        assert len(_compact_bytes(_tool_result_projection(complete_result))) > (
            MAX_TOOL_RESULT_BYTES
        )

        summary = service.get_run(receipt["run_ref"])
        assert summary == service.get_run(receipt["run_ref"])
        assert summary["output"]["present"] is True

        cursor = None
        values: list[float] = []
        field_digest = None
        while True:
            page = service.get_run(
                receipt["run_ref"],
                view="output",
                field="returns",
                cursor=cursor,
                limit=1_000,
            )
            page_result = _success_result("get_run", page)
            assert _bounded_tool_result("get_run", page_result) is page_result
            if field_digest is None:
                field_digest = page["field_sha256"]
            else:
                assert page["field_sha256"] == field_digest
            values.extend(page["items"])
            cursor = page["page"]["next_cursor"]
            if cursor is None:
                assert page["page"]["complete"] is True
                break

        assert len(values) == len(prices) - 1
        assert values[:2] == [0.01, -0.009900990099009901]


def test_governed_artifact_is_verified_and_retrievable_in_bounded_chunks() -> None:
    content = (b"bounded-governed-artifact-" * 10_000) + b"end"
    digest = hashlib.sha256(content).hexdigest()
    expected = SimpleNamespace(
        artifact_id="analysis_report",
        media_type="application/octet-stream",
        digest=digest,
        byte_length=len(content),
    )

    class ArtifactRuntime:
        def get_run(self, _reference: object) -> object:
            return SimpleNamespace(artifacts=(expected,))

        def close(self) -> None:
            return None

    reader_calls = 0

    def reader(_reference: str, _artifact_id: str) -> OperationArtifact:
        nonlocal reader_calls
        reader_calls += 1
        return OperationArtifact(
            content=content,
            media_type=expected.media_type,
            sha256=digest,
        )

    service = DefinedQuantService(
        methods_runtime=ArtifactRuntime(),  # type: ignore[arg-type]
        method_artifact_reader=reader,
    )
    try:
        cursor = None
        first_cursor = None
        reconstructed = bytearray()
        expected_offset = 0
        while True:
            request = ReadArtifactRequest(
                run_ref="dqrun:" + "a" * 64,
                artifact_id="analysis_report",
                cursor=cursor,
                limit_bytes=MAX_ARTIFACT_CHUNK_BYTES,
            )
            page = anyio.run(_dispatch, service, request)
            page_result = _success_result("read_artifact", page)
            assert _bounded_tool_result("read_artifact", page_result) is page_result
            assert page["offset"] == expected_offset
            chunk = base64.b64decode(page["content_base64"], validate=True)
            assert len(chunk) == page["returned_bytes"]
            assert hashlib.sha256(chunk).hexdigest() == page["chunk_sha256"]
            reconstructed.extend(chunk)
            expected_offset += len(chunk)
            cursor = page["next_cursor"]
            if first_cursor is None:
                first_cursor = cursor
            if cursor is None:
                assert page["complete"] is True
                break

        assert reader_calls > 1
        assert bytes(reconstructed) == content
        assert page["sha256"] == digest
        assert page["total_bytes"] == len(content)
        assert isinstance(first_cursor, str)

        replacement = "A" if first_cursor[-1] != "A" else "B"
        with pytest.raises(HostFailureException) as tampered:
            service.read_artifact(
                "dqrun:" + "a" * 64,
                "analysis_report",
                cursor=first_cursor[:-1] + replacement,
            )
        assert tampered.value.code == HostFailureCode.INVALID_TOOL_REQUEST
    finally:
        service.close()

    with pytest.raises(ValueError):
        ReadArtifactRequest(
            run_ref="dqrun:" + "a" * 64,
            artifact_id="analysis_report",
            limit_bytes=MAX_ARTIFACT_CHUNK_BYTES + 1,
        )


def test_oversized_governed_artifact_is_refused_before_reader_load() -> None:
    expected = SimpleNamespace(
        artifact_id="analysis_report",
        media_type="application/octet-stream",
        digest="a" * 64,
        byte_length=MAX_METHOD_ARTIFACT_READ_BYTES + 1,
    )

    class ArtifactRuntime:
        def get_run(self, _reference: object) -> object:
            return SimpleNamespace(artifacts=(expected,))

        def close(self) -> None:
            return None

    reader_called = False

    def reader(_reference: str, _artifact_id: str) -> OperationArtifact:
        nonlocal reader_called
        reader_called = True
        raise AssertionError("oversized artifact reader must not be called")

    service = DefinedQuantService(
        methods_runtime=ArtifactRuntime(),  # type: ignore[arg-type]
        method_artifact_reader=reader,
    )
    try:
        with pytest.raises(HostFailureException) as refused:
            service.read_artifact(
                "dqrun:" + "a" * 64,
                "analysis_report",
            )
        assert refused.value.code == HostFailureCode.RESULT_LIMIT_EXCEEDED
        assert refused.value.failure.error.details == {
            "limit_name": "artifact_decoded_bytes",
            "maximum": MAX_METHOD_ARTIFACT_READ_BYTES,
            "actual": MAX_METHOD_ARTIFACT_READ_BYTES + 1,
        }
        assert reader_called is False
    finally:
        service.close()


def test_official_client_cursor_binding_tampering_and_resource_error_set() -> None:
    async def story() -> None:
        with _audit_file() as audit:
            async with _client(audit) as client:
                first = _assert_success(
                    await client.call_tool("register_dataset", _registration())
                )
                second_registration = _registration()
                second_registration["source"]["rows"][2]["price"] = 122.0
                second = _assert_success(
                    await client.call_tool("register_dataset", second_registration)
                )

                preview = _assert_success(
                    await client.call_tool(
                        "describe_dataset",
                        {
                            "dataset_ref": first["dataset_ref"],
                            "view": "preview",
                            "limit": 1,
                        },
                    )
                )
                cursor = preview["preview"]["next_cursor"]
                assert isinstance(cursor, str)
                replacement = "A" if cursor[-1] != "A" else "B"
                tampered = cursor[:-1] + replacement

                tamper_failure = await client.call_tool(
                    "describe_dataset",
                    {
                        "dataset_ref": first["dataset_ref"],
                        "view": "preview",
                        "cursor": tampered,
                        "limit": 1,
                    },
                )
                assert _assert_failure(tamper_failure, "invalid_tool_request")["details"] == {
                    "fields": []
                }

                binding_failure = await client.call_tool(
                    "describe_dataset",
                    {
                        "dataset_ref": second["dataset_ref"],
                        "view": "preview",
                        "cursor": cursor,
                        "limit": 1,
                    },
                )
                assert _assert_failure(binding_failure, "invalid_tool_request")["details"] == {
                    "fields": []
                }

                with pytest.raises(MCPError) as unknown_operation:
                    await client.read_resource(
                        "dqop://v1/" + "a" * 64 + "/artifact/simple_return",
                        cache_mode="reload",
                    )
                assert unknown_operation.value.data["host_failure_code"] == (
                    "reference_not_found"
                )

                inspected = _assert_success(
                    await client.call_tool(
                        "inspect_component",
                        {"component_id": "dq.market_data.simple_return"},
                    )
                )
                operation = _assert_success(
                    await client.call_tool(
                        "execute_component",
                        {
                            "component": inspected["component"],
                            "literals": {
                                "prices": [100.0, 101.0],
                                "price_kind": "adjusted",
                            },
                            "provenance": _PROVENANCE,
                            "artifacts": "svg_all",
                        },
                    )
                )
                operation_digest = operation["operation_ref"].rsplit(":", maxsplit=1)[1]
                with pytest.raises(MCPError) as missing_artifact:
                    await client.read_resource(
                        f"dqop://v1/{operation_digest}/artifact/not_present",
                        cache_mode="reload",
                    )
                assert missing_artifact.value.data["host_failure_code"] == "artifact_not_found"

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
