from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
CATALOG = SKILL_ROOT / "scripts" / "catalog.py"
SCRIPTS = SKILL_ROOT / "scripts"


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
