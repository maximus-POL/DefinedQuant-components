"""Smoke-test both distributions from a clean installed-wheel environment."""

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
                    "search_components",
                    "inspect_component",
                    "register_dataset",
                    "describe_dataset",
                    "compare_ports",
                    "compile_plan",
                    "execute_component",
                    "get_operation",
                )
                if tuple(tool.name for tool in tools.tools) != expected:
                    raise AssertionError("installed MCP tool list changed")
                result = await client.call_tool(
                    "search_components",
                    {"query": "simple return", "limit": 1},
                )
                if result.is_error or result.structured_content["outcome"] != "ok":
                    raise AssertionError("installed MCP search smoke failed")
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
    _assert_installed(defined_quant, checkout)
    _assert_installed(defined_quant_protocol, checkout)
    _assert_installed(defined_quant_mcp, checkout)
    if importlib.metadata.version("defined-quant") != "0.1.3":
        raise AssertionError("unexpected installed core version")
    if importlib.metadata.version("defined-quant-mcp") != "0.1.0a1":
        raise AssertionError("unexpected installed MCP version")
    if importlib.metadata.version("mcp") != "2.0.0":
        raise AssertionError("unexpected installed MCP SDK version")
    requirements = frozenset(importlib.metadata.requires("defined-quant-mcp") or ())
    if requirements != frozenset({"defined-quant==0.1.3", "mcp==2.0.0"}):
        raise AssertionError("unexpected installed MCP requirements")
    if sys.platform == "win32":
        importlib.metadata.version("pywin32")

    if MAX_WORKER_MEMORY_BYTES != 512 * 1024 * 1024:
        raise AssertionError("worker memory contract changed")
    if utf8_lf_frame('{"ok":true}') != b'{"ok":true}\n':
        raise AssertionError("UTF-8/LF framing contract changed")
    reference = load_execution_policy("simple_return_csv_v1").components[0].component
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
