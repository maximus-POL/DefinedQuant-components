from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
CATALOG = SKILL_ROOT / "scripts" / "catalog.py"


def test_catalog_script_activates_the_current_checkout_without_installing_it() -> None:
    completed = subprocess.run(
        [sys.executable, str(CATALOG), "show", "dq.financial_analysis.earnings_comparison"],
        cwd=PROJECT_ROOT,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["component"]["id"] == "dq.financial_analysis.earnings_comparison"
    assert response["operation_protocol"]["name"] == "defined_quant_operation"
    assert "view_bundle" in response["output_schema"]["properties"]
