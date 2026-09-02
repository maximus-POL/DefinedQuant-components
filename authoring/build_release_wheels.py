"""Build the exact core, DQ-native adapter, and MCP wheels consumed by CI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(arguments: list[str]) -> None:
    subprocess.run(arguments, cwd=ROOT, check=True, shell=False)


def main() -> int:
    raw_directory = os.environ.get("DQ_WHEEL_DIRECTORY")
    if not raw_directory:
        raise SystemExit("DQ_WHEEL_DIRECTORY is required")
    wheel_directory = Path(raw_directory).resolve()
    if wheel_directory.exists():
        raise SystemExit("wheel output directory already exists")
    wheel_directory.mkdir(parents=True)

    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is unavailable")
    _run([uv, "build", "--wheel", "--out-dir", os.fspath(wheel_directory)])
    _run(
        [
            uv,
            "build",
            "--project",
            "adapters/dq_native",
            "--wheel",
            "--out-dir",
            os.fspath(wheel_directory),
        ]
    )
    _run(
        [
            uv,
            "build",
            "--project",
            "mcp_server",
            "--wheel",
            "--out-dir",
            os.fspath(wheel_directory),
        ]
    )
    _run(
        [
            sys.executable,
            os.fspath(ROOT / "authoring" / "verify_release_wheels.py"),
            "--wheel-directory",
            os.fspath(wheel_directory),
            "--write-manifest",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
