"""Run the repository's mypy gates without shell-expanded source globs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(arguments: list[str]) -> None:
    subprocess.run(arguments, cwd=ROOT, check=True, shell=False)


def _venv_python(environment: Path) -> Path:
    windows = environment / "Scripts" / "python.exe"
    return windows if windows.is_file() else environment / "bin" / "python"


def main() -> int:
    raw_directory = os.environ.get("DQ_WHEEL_DIRECTORY")
    if not raw_directory:
        raise SystemExit("DQ_WHEEL_DIRECTORY is required")
    wheel_directory = Path(raw_directory).resolve()
    core_wheel = wheel_directory / "defined_quant-0.1.3-py3-none-any.whl"
    adapter_wheel = (
        wheel_directory / "defined_quant_adapter_dq_native-1.0.0-py3-none-any.whl"
    )
    mcp_wheel = wheel_directory / "defined_quant_mcp-0.1.0a1-py3-none-any.whl"
    if not core_wheel.is_file() or not adapter_wheel.is_file() or not mcp_wheel.is_file():
        raise SystemExit("exact release wheels are missing")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is unavailable")
    _run(
        [
            sys.executable,
            os.fspath(ROOT / "authoring" / "verify_release_wheels.py"),
            "--wheel-directory",
            os.fspath(wheel_directory),
        ]
    )
    mcp_python = _venv_python(ROOT / "mcp_server" / ".venv")
    if not mcp_python.is_file():
        raise SystemExit("locked MCP development environment is missing")
    for python in (Path(sys.executable), mcp_python):
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                os.fspath(python),
                "--no-deps",
                "--reinstall",
                os.fspath(core_wheel),
                os.fspath(adapter_wheel),
                os.fspath(mcp_wheel),
            ]
        )
    authoring_modules = [os.fspath(path) for path in sorted((ROOT / "authoring").glob("*.py"))]
    _run([sys.executable, "-m", "mypy", "shared", "categories", *authoring_modules])
    _run([sys.executable, "-m", "mypy", "-p", "defined_quant_protocol"])
    _run([sys.executable, "-m", "mypy", "-p", "defined_quant_adapter_dq_native"])
    _run(
        [
            os.fspath(mcp_python),
            "-m",
            "mypy",
            "-p",
            "defined_quant_mcp",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
