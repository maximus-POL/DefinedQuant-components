"""Build-matrix smoke test for the installed core and MCP wheels."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("core_wheel_directory", type=Path)
    parser.add_argument("mcp_wheel_directory", type=Path)
    return parser


def _one_wheel(directory: Path, pattern: str, label: str) -> Path:
    wheels = tuple(directory.glob(pattern))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one {label} wheel")
    return wheels[0].resolve()


def _run(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, shell=False)


def main() -> int:
    args = _parser().parse_args()
    core_wheel = _one_wheel(
        args.core_wheel_directory,
        "defined_quant-*.whl",
        "defined-quant",
    )
    mcp_wheel = _one_wheel(
        args.mcp_wheel_directory,
        "defined_quant_mcp-*.whl",
        "defined-quant-mcp",
    )
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is unavailable")

    smoke = dedent(
        '''\
        import importlib.metadata
        import sys
        import tempfile
        from pathlib import Path

        import anyio
        from mcp import StdioServerParameters, stdio_client
        from mcp.client import Client

        assert importlib.metadata.version("defined-quant") == "0.1.3"
        assert importlib.metadata.version("defined-quant-mcp") == "0.1.0a1"
        assert importlib.metadata.version("mcp") == "2.0.0"
        requirements = importlib.metadata.requires("defined-quant-mcp") or []
        assert set(requirements) == {"defined-quant==0.1.3", "mcp==2.0.0"}
        assert not any(requirement.startswith("mcp-types") for requirement in requirements)
        if sys.platform == "win32":
            assert importlib.metadata.version("pywin32")

        async def story():
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as audit:
                with tempfile.TemporaryDirectory(prefix="defined-quant-mcp-wheel-") as temporary:
                    state = Path(temporary).resolve() / "state-żółć"
                    params = StdioServerParameters(
                        command=sys.executable,
                        args=["-m", "defined_quant_mcp", "--state-root", str(state)],
                        encoding="utf-8",
                        encoding_error_handler="strict",
                    )
                    async with Client(stdio_client(params, errlog=audit)) as client:
                        assert client.server_info is not None
                        assert client.server_info.name == "defined-quant-mcp"
                        tools = await client.list_tools(cache_mode="reload")
                        assert [tool.name for tool in tools.tools] == [
                            "search_components",
                            "inspect_component",
                            "register_dataset",
                            "describe_dataset",
                            "compare_ports",
                            "compile_plan",
                            "execute_component",
                            "get_operation",
                        ]
                        result = await client.call_tool(
                            "search_components",
                            {"query": "simple return", "limit": 1},
                        )
                        assert result.is_error is False
                        assert result.structured_content["outcome"] == "ok"
                        assert result.structured_content["data"]["hits"][0][
                            "component_id"
                        ] == "dq.market_data.simple_return"

        anyio.run(story)
        '''
    )
    with TemporaryDirectory(prefix="defined-quant-mcp-wheel-environment-") as temporary:
        environment = Path(temporary) / "venv"
        _run([uv, "venv", os.fspath(environment), "--python", sys.executable])
        executable = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                os.fspath(executable),
                os.fspath(core_wheel),
                os.fspath(mcp_wheel),
            ]
        )
        _run([os.fspath(executable), "-c", smoke])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
