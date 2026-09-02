"""Run one complete native CI cell exclusively against installed release wheels."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    environment: dict[str, str] | None = None,
) -> None:
    print(f"CI phase: {arguments[0]} {' '.join(arguments[1:])}", flush=True)
    subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        check=True,
        shell=False,
    )


def _venv_python(environment: Path) -> Path:
    windows = environment / "Scripts" / "python.exe"
    return windows if windows.is_file() else environment / "bin" / "python"


def _one_wheel(directory: Path, filename: str) -> Path:
    path = directory / filename
    if not path.is_file():
        raise SystemExit(f"required wheel is missing: {filename}")
    return path


def main() -> int:
    expected_python = os.environ.get("DQ_EXPECTED_PYTHON")
    actual_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if not expected_python or actual_python != expected_python:
        raise SystemExit(
            f"matrix Python mismatch: expected {expected_python!r}, found {actual_python!r}"
        )
    raw_directory = os.environ.get("DQ_WHEEL_DIRECTORY")
    if not raw_directory:
        raise SystemExit("DQ_WHEEL_DIRECTORY is required")
    wheel_directory = Path(raw_directory).resolve()
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
    core_wheel = _one_wheel(
        wheel_directory,
        "defined_quant-0.1.3-py3-none-any.whl",
    )
    adapter_wheel = _one_wheel(
        wheel_directory,
        "defined_quant_adapter_dq_native-1.0.0-py3-none-any.whl",
    )
    mcp_wheel = _one_wheel(
        wheel_directory,
        "defined_quant_mcp-0.1.0a1-py3-none-any.whl",
    )

    with TemporaryDirectory(prefix="defined-quant-native-ci-") as temporary:
        work = Path(temporary)
        root_requirements = work / "root-dev-requirements.txt"
        mcp_requirements = work / "mcp-production-requirements.txt"
        environment_root = work / "environment"
        _run(
            [
                uv,
                "export",
                "--quiet",
                "--locked",
                "--only-group",
                "dev",
                "--no-emit-project",
                "--output-file",
                os.fspath(root_requirements),
            ]
        )
        _run(
            [
                uv,
                "export",
                "--quiet",
                "--project",
                "mcp_server",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-emit-package",
                "defined-quant",
                "--no-emit-package",
                "defined-quant-adapter-dq-native",
                "--output-file",
                os.fspath(mcp_requirements),
            ]
        )
        _run([uv, "venv", os.fspath(environment_root), "--python", sys.executable])
        python = _venv_python(environment_root)
        if not python.is_file():
            raise SystemExit("uv did not create the expected Python executable")
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                os.fspath(python),
                "--require-hashes",
                "--requirements",
                os.fspath(root_requirements),
                "--requirements",
                os.fspath(mcp_requirements),
            ]
        )
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                os.fspath(python),
                "--no-deps",
                os.fspath(core_wheel),
                os.fspath(adapter_wheel),
                os.fspath(mcp_wheel),
            ]
        )

        clean_environment = dict(os.environ)
        for variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
            clean_environment.pop(variable, None)
        clean_environment.update(
            {
                "DEFINED_QUANT_CHECKOUT_ROOT": os.fspath(ROOT),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        _run(
            [
                os.fspath(python),
                os.fspath(ROOT / "authoring" / "installed_wheel_smoke.py"),
                "--checkout-root",
                os.fspath(ROOT),
            ],
            cwd=work,
            environment=clean_environment,
        )
        _run(
            [os.fspath(python), os.fspath(ROOT / "authoring" / "check_component.py")],
            environment=clean_environment,
        )

        root_environment = dict(clean_environment)
        root_environment["DEFINED_QUANT_SUITE_KIND"] = "root"
        _run(
            [
                os.fspath(python),
                "-m",
                "pytest",
                "-c",
                os.fspath(ROOT / "pyproject.toml"),
                "-p",
                "authoring.pytest_wheel_isolation",
            ],
            environment=root_environment,
        )

        mcp_environment = dict(clean_environment)
        mcp_environment["DEFINED_QUANT_SUITE_KIND"] = "mcp"
        _run(
            [
                os.fspath(python),
                "-m",
                "pytest",
                "-c",
                os.fspath(ROOT / "pyproject.toml"),
                "-p",
                "authoring.pytest_wheel_isolation",
                os.fspath(ROOT / "mcp_server" / "tests"),
            ],
            environment=mcp_environment,
        )
        if os.environ.get("DQ_MEASURE_WORKER_CAPACITY") == "1":
            _run(
                [
                    os.fspath(python),
                    os.fspath(ROOT / "authoring" / "measure_worker_capacity.py"),
                ],
                cwd=work,
                environment=clean_environment,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
