"""Build-matrix smoke test for the native worker from an installed core wheel."""

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
    parser.add_argument("wheel_directory", type=Path)
    return parser


def _run(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, shell=False)


def main() -> int:
    args = _parser().parse_args()
    wheels = tuple(args.wheel_directory.glob("defined_quant-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("expected exactly one defined-quant wheel")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is unavailable")

    smoke = dedent(
        '''\
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from defined_quant import load_execution_policy
        from defined_quant.local_host_platform import local_host_platform
        from defined_quant.service import DefinedQuantService
        from defined_quant.stdio_framing import utf8_lf_frame
        from defined_quant.worker_runtime import MAX_WORKER_MEMORY_BYTES
        from defined_quant_protocol import CallerProvenance, OperationRequest, OperationSuccess

        assert MAX_WORKER_MEMORY_BYTES == 512 * 1024 * 1024
        assert utf8_lf_frame('{"ok":true}') == b'{"ok":true}\\n'
        assert local_host_platform().provider_id in {"darwin", "linux", "win32"}
        reference = load_execution_policy("simple_return_csv").components[0].component
        request = OperationRequest(
            component=reference,
            input={"prices": [100, 101], "price_kind": "adjusted"},
            provenance=CallerProvenance(
                source_kind="synthetic",
                interpretation_method="caller_structured",
                label="Installed-wheel native worker smoke test.",
            ),
        )
        with TemporaryDirectory(prefix="defined-quant-installed-worker-") as temporary:
            service = DefinedQuantService()
            result = service.execute_operation(
                request,
                output_dir=Path(temporary) / "operation",
            )
            assert isinstance(result, OperationSuccess)
            assert result.manifest.request == request
            service.close()
        '''
    )
    with TemporaryDirectory(prefix="defined-quant-wheel-environment-") as temporary:
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
                os.fspath(wheels[0].resolve()),
            ]
        )
        _run([os.fspath(executable), "-c", smoke])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
