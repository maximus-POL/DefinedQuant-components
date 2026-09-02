"""Smoke-test all three distributions from a clean installed-wheel environment."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from typing import Any

import anyio  # type: ignore[import-not-found]
import defined_quant
import defined_quant_protocol
from defined_quant import load_execution_policy
from defined_quant.service import DefinedQuantService
from defined_quant.stdio_framing import utf8_lf_frame
from defined_quant.worker_runtime import MAX_WORKER_MEMORY_BYTES
from defined_quant_protocol import (
    CallerProvenance,
    InterpretationMethod,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    SourceKind,
)
from mcp import StdioServerParameters, stdio_client  # type: ignore[import-not-found]
from mcp.client import Client  # type: ignore[import-not-found]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout-root", required=True, type=Path)
    return parser


def _assert_installed(module: Any, checkout: Path) -> None:
    module_path = Path(module.__file__).resolve()
    if module_path.is_relative_to(checkout):
        raise AssertionError(f"production import leaked from checkout: {module_path}")


async def _mcp_smoke(state_root: Path) -> None:
    with TemporaryFile(mode="w+", encoding="utf-8") as audit:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "defined_quant_mcp", "--state-root", os.fspath(state_root)],
            encoding="utf-8",
            encoding_error_handler="strict",
        )
        try:
            async with Client(stdio_client(params, errlog=audit)) as client:
                if client.server_info is None or client.server_info.name != "defined-quant-mcp":
                    raise AssertionError("installed MCP server did not initialize")
                tools = await client.list_tools(cache_mode="reload")
                expected = (
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
                if tuple(tool.name for tool in tools.tools) != expected:
                    raise AssertionError("installed MCP tool list changed")
                result = await client.call_tool(
                    "search_methods",
                    {"query": "simple return", "limit": 1},
                )
                if result.is_error or result.structured_content["outcome"] != "ok":
                    raise AssertionError("installed MCP search smoke failed")
                compiled = await client.call_tool(
                    "compile_plan",
                    {
                        "proposal": {
                            "method_id": "dq.market_data.simple_return",
                            "method_version": "1.0.0",
                            "financial_inputs": {"prices": [100.0, 110.0, 121.0]},
                            "conventions": {"price_kind": "adjusted"},
                        }
                    },
                )
                if compiled.is_error:
                    raise AssertionError("installed MCP compile smoke failed")
                compiled_data = compiled.structured_content["data"]
                if compiled_data["status"] != "compiled":
                    raise AssertionError("installed MCP did not compile the DQ-native slice")
                run = await client.call_tool(
                    "execute_plan",
                    {"plan_ref": compiled_data["plan_ref"]},
                )
                if run.is_error or run.structured_content["data"]["status"] != "succeeded":
                    raise AssertionError("installed MCP governed execution smoke failed")
                run_data = run.structured_content["data"]
                if "canonical_method_output" in run_data:
                    raise AssertionError("installed MCP execution returned an unbounded record")
                returned = await client.call_tool(
                    "get_run",
                    {
                        "run_ref": run_data["run_ref"],
                        "view": "output",
                        "field": "returns",
                        "limit": 1,
                    },
                )
                if returned.is_error or returned.structured_content["data"]["items"] != [0.1]:
                    raise AssertionError("installed MCP governed run paging smoke failed")
        except BaseException as exc:
            audit.seek(0)
            records: list[str] = []
            non_audit = False
            for line in audit.read().splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    non_audit = True
                    continue
                if isinstance(value, dict):
                    event = value.get("event")
                    code = value.get("code")
                    if isinstance(event, str) and isinstance(code, str):
                        records.append(f"{event}:{code}")
            summary = ",".join(records) if records else "none"
            if non_audit:
                summary += ",non-audit-stderr"
            raise AssertionError(
                f"installed MCP smoke failed: {type(exc).__name__} ({summary})"
            ) from None


def main() -> int:
    args = _parser().parse_args()
    checkout = args.checkout_root.resolve()
    defined_quant_mcp = importlib.import_module("defined_quant_mcp")
    defined_quant_adapter = importlib.import_module("defined_quant_adapter_dq_native")
    _assert_installed(defined_quant, checkout)
    _assert_installed(defined_quant_protocol, checkout)
    _assert_installed(defined_quant_mcp, checkout)
    _assert_installed(defined_quant_adapter, checkout)
    if importlib.metadata.version("defined-quant") != "0.1.3":
        raise AssertionError("unexpected installed core version")
    if importlib.metadata.version("defined-quant-mcp") != "0.1.0a1":
        raise AssertionError("unexpected installed MCP version")
    if importlib.metadata.version("defined-quant-adapter-dq-native") != "1.0.0":
        raise AssertionError("unexpected installed DQ-native adapter version")
    if importlib.metadata.version("mcp") != "2.0.0":
        raise AssertionError("unexpected installed MCP SDK version")
    requirements = frozenset(importlib.metadata.requires("defined-quant-mcp") or ())
    if requirements != frozenset(
        {
            "defined-quant-adapter-dq-native==1.0.0",
            "defined-quant==0.1.3",
            "mcp==2.0.0",
        }
    ):
        raise AssertionError("unexpected installed MCP requirements")
    if sys.platform == "win32":
        importlib.metadata.version("pywin32")

    if MAX_WORKER_MEMORY_BYTES != 512 * 1024 * 1024:
        raise AssertionError("worker memory contract changed")
    if utf8_lf_frame('{"ok":true}') != b'{"ok":true}\n':
        raise AssertionError("UTF-8/LF framing contract changed")
    reference = load_execution_policy("simple_return_csv").components[0].component
    request = OperationRequest(
        component=reference,
        input={"prices": [100, 101], "price_kind": "adjusted"},
        provenance=CallerProvenance(
            source_kind=SourceKind.SYNTHETIC,
            interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
            label="Installed-wheel native matrix smoke test.",
        ),
    )
    with TemporaryDirectory(prefix="defined-quant-installed-wheel-smoke-") as temporary:
        root = Path(temporary)
        with DefinedQuantService(session_state_root=root / "core-state") as service:
            methods = service.search_methods("simple return", limit=1)
            if methods.hits[0].method.id != "dq.market_data.simple_return":
                raise AssertionError("installed methods-first discovery smoke failed")
            plan = service.compile_plan(
                {
                    "method_id": "dq.market_data.simple_return",
                    "method_version": "1.0.0",
                    "financial_inputs": {"prices": [100.0, 110.0, 121.0]},
                    "conventions": {"price_kind": "adjusted"},
                }
            )
            if plan.status != "compiled":
                raise AssertionError("installed methods-first compilation smoke failed")
            run = service.execute_plan(plan.ref)
            if run["status"] != "succeeded" or "canonical_method_output" in run:
                raise AssertionError("installed governed execution smoke failed")
            returned = service.get_run(
                run["run_ref"],
                view="output",
                field="returns",
                limit=1,
            )
            if returned["items"] != [0.1] or returned["page"]["total"] != 2:
                raise AssertionError("installed governed run paging smoke failed")
            search = service.search_components("calculate period price changes", limit=5)
            if search.hits[0].record.component_id != "dq.market_data.simple_return":
                raise AssertionError("installed core discovery smoke failed")
            result = service.execute_operation(request, output_dir=root / "operation")
            if not isinstance(result, OperationSuccess):
                assert isinstance(result, OperationFailure)
                detail_type = result.error.details.get("type", "none")
                raise AssertionError(
                    "installed core execution smoke failed: "
                    f"{result.error.code.value} ({detail_type})"
                )
            if result.manifest.request != request:
                raise AssertionError("installed core execution request changed")
        long_state = root / ("żółć-state-" + ("long-segment-" * 18))
        anyio.run(_mcp_smoke, long_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
