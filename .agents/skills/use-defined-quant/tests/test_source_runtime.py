from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
CATALOG = SKILL_ROOT / "scripts" / "catalog.py"
SCRIPTS = SKILL_ROOT / "scripts"
SOURCE_RUNTIME = SCRIPTS / "_source_runtime.py"


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def test_catalog_script_activates_both_current_checkout_packages() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, pathlib, sys; "
                "sys.path.insert(0, sys.argv[1]); "
                "from _source_runtime import activate_source_runtime; "
                "activate_source_runtime(); "
                "import defined_quant, defined_quant_protocol; "
                "print(json.dumps({"
                "'runtime': defined_quant.__file__, "
                "'protocol': defined_quant_protocol.__file__}))"
            ),
            str(SCRIPTS),
        ],
        cwd=PROJECT_ROOT,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
    activated = json.loads(probe.stdout)
    assert Path(activated["runtime"]).resolve() == (PROJECT_ROOT / "shared/__init__.py").resolve()
    assert Path(activated["protocol"]).resolve() == (
        PROJECT_ROOT / "protocol/__init__.py"
    ).resolve()

    completed = subprocess.run(
        [sys.executable, str(CATALOG), "show", "dq.market_data.simple_return"],
        cwd=PROJECT_ROOT,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["component"]["id"] == "dq.market_data.simple_return"
    assert response["operation_protocol"]["protocol_version"]
    assert response["operation_protocol"]["schemas"]["request"]["title"] == (
        "OperationRequest"
    )


def test_extracted_checkout_activation_never_silently_loads_another_checkout(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    archive = subprocess.run(
        ["git", "archive", "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
    )
    assert archive.returncode == 0, archive.stderr.decode("utf-8", errors="replace")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as archived:
        archived.extractall(checkout, filter="data")

    extracted_runtime = (
        checkout
        / ".agents"
        / "skills"
        / "use-defined-quant"
        / "scripts"
        / "_source_runtime.py"
    )
    extracted_runtime.write_bytes(SOURCE_RUNTIME.read_bytes())
    extracted_scripts = extracted_runtime.parent
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "sys.path.insert(0, sys.argv[1]); "
                "from _source_runtime import activate_source_runtime; "
                "outcome = {}; "
                "\ntry:\n"
                " activate_source_runtime()\n"
                "except RuntimeError as exc:\n"
                " outcome = {'status': 'raised', 'message': str(exc)}\n"
                "else:\n"
                " import defined_quant.discovery, defined_quant_protocol\n"
                " outcome = {"
                "'status': 'loaded', "
                "'runtime': defined_quant.discovery.__file__, "
                "'protocol': defined_quant_protocol.__file__}\n"
                "print(json.dumps(outcome))"
            ),
            str(extracted_scripts),
        ],
        cwd=PROJECT_ROOT,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
    outcome = json.loads(probe.stdout)
    if outcome["status"] == "loaded":
        assert Path(outcome["runtime"]).resolve().is_relative_to(checkout.resolve())
        assert Path(outcome["protocol"]).resolve().is_relative_to(checkout.resolve())
    else:
        assert outcome["status"] == "raised"
        assert "expected" in outcome["message"]
        assert "actual" in outcome["message"]
        assert str(checkout.resolve()) in outcome["message"]


def test_checkout_resolution_handles_unicode_and_long_paths(tmp_path: Path) -> None:
    checkout = tmp_path / "zażółć-gęślą-jaźń"
    while len(os.fspath(checkout)) < 280:
        checkout /= "long-source-runtime-segment"
    runtime = (
        checkout
        / ".agents"
        / "skills"
        / "use-defined-quant"
        / "scripts"
        / "_source_runtime.py"
    )
    runtime.parent.mkdir(parents=True)
    shutil.copyfile(SOURCE_RUNTIME, runtime)
    (checkout / "shared").mkdir()
    (checkout / "shared" / "__init__.py").write_bytes(b"")
    (checkout / "categories").mkdir()
    (checkout / "protocol").mkdir()
    (checkout / "protocol" / "__init__.py").write_bytes(b"")

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0,sys.argv[1]); "
                "from _source_runtime import _resolve_checkout_root; "
                "sys.stdout.buffer.write(str(_resolve_checkout_root()).encode('utf-8'))"
            ),
            str(runtime.parent),
        ],
        cwd=tmp_path,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
    )

    assert probe.returncode == 0, probe.stderr.decode("utf-8", errors="replace")
    assert Path(probe.stdout.decode("utf-8").strip()).resolve() == checkout.resolve()


def test_checkout_resolution_rejects_noncanonical_script_layout(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "_source_runtime.py"
    script.parent.mkdir()
    shutil.copyfile(SOURCE_RUNTIME, script)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0,sys.argv[1]); "
                "from _source_runtime import _resolve_checkout_root; "
                "_resolve_checkout_root()"
            ),
            str(script.parent),
        ],
        cwd=tmp_path,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
    )

    assert probe.returncode != 0
    assert b"source-runtime checkout layout" in probe.stderr
