"""Build source archives and the static catalog after all native cells pass."""

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
    commit_sha = os.environ.get("DQ_COMMIT_SHA")
    if not commit_sha:
        raise SystemExit("DQ_COMMIT_SHA is required")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is unavailable")
    raw_output_root = os.environ.get("DQ_RELEASE_OUTPUT_ROOT")
    output_root = Path(raw_output_root).resolve() if raw_output_root else ROOT / "dist"
    output = output_root / "source"
    if output.exists():
        raise SystemExit("source archive output directory already exists")
    output.mkdir(parents=True)
    _run([uv, "build", "--sdist", "--out-dir", os.fspath(output)])
    _run(
        [
            uv,
            "build",
            "--project",
            "mcp_server",
            "--sdist",
            "--out-dir",
            os.fspath(output),
        ]
    )
    archives = tuple(sorted(path.name for path in output.glob("*.tar.gz")))
    if archives != ("defined_quant-0.1.3.tar.gz", "defined_quant_mcp-0.1.0a1.tar.gz"):
        raise SystemExit("source archive set does not match the two release distributions")
    _run(
        [
            sys.executable,
            os.fspath(ROOT / "authoring" / "export_catalog.py"),
            "--commit-sha",
            commit_sha,
            "--release-version",
            "0.1.3",
            "--release-label",
            "Experimental Technical Preview",
            "--output",
            os.fspath(output_root / "catalog" / "catalog.json"),
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
