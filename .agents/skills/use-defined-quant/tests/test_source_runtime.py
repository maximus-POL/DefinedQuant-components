from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
CATALOG = SKILL_ROOT / "scripts" / "catalog.py"
SCRIPTS = SKILL_ROOT / "scripts"
SOURCE_RUNTIME = SCRIPTS / "_source_runtime.py"


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
        env={"PATH": "/usr/bin:/bin"},
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
        env={"PATH": "/usr/bin:/bin"},
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
    extracted = subprocess.run(
        ["tar", "-x", "-C", str(checkout)],
        input=archive.stdout,
        check=False,
        capture_output=True,
    )
    assert extracted.returncode == 0, extracted.stderr.decode("utf-8", errors="replace")

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
        env={"PATH": "/usr/bin:/bin"},
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
