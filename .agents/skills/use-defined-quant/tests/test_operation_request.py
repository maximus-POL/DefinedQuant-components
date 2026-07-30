from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from defined_quant import OperationFailure, OperationSuccess

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
RUNNER = SKILL_ROOT / "scripts" / "run_component.py"


def _command(request_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--request", str(request_path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _earnings_input() -> dict[str, Any]:
    return {
        "metric_name": "Revenue",
        "reporting_currency": "USD",
        "value_scale": "millions",
        "period_basis": "calendar_aligned",
        "periods": ["Q1", "Q2"],
        "series": [
            {"key": "alpha", "label": "Alpha", "values": [100.0, 120.0]},
            {"key": "beta", "label": "Beta", "values": [80.0, 90.0]},
        ],
        "source_label": "AI-interpreted attached fixture; unverified",
    }


def test_request_envelope_runs_component_and_returns_typed_success(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "component_id": "dq.financial_analysis.earnings_comparison",
                "input": _earnings_input(),
                "provenance": {
                    "source_kind": "user_attachment",
                    "interpretation_method": "ai_interpreted",
                    "verification_status": "unverified",
                    "label": "earnings.xlsx interpreted by the agent",
                    "assumptions": ["Revenue treated as USD millions."],
                },
                "view": {
                    "use_case": "chat",
                    "supported_media_types": ["text/html", "image/svg+xml"],
                },
                "output_dir": str(tmp_path / "output"),
            }
        ),
        encoding="utf-8",
    )

    completed = _command(request_path)

    assert completed.returncode == 0, completed.stderr
    response = OperationSuccess.model_validate_json(completed.stdout)
    manifest = response.manifest
    assert manifest.schema_version == 2
    assert manifest.operation.provenance.interpretation_method == "ai_interpreted"
    assert manifest.view_selection is not None
    assert manifest.view_selection.selected_view_id == "chat_dashboard"
    primary = [artifact for artifact in manifest.artifacts if artifact.role == "primary"]
    assert len(primary) == 1
    assert primary[0].media_type == "text/html"
    assert Path(primary[0].path).is_file()


def test_request_envelope_returns_typed_component_input_failure(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "invalid-request.json"
    request_path.write_text(
        json.dumps(
            {
                "component_id": "dq.financial_analysis.earnings_comparison",
                "input": {"metric_name": "Revenue"},
                "provenance": {
                    "source_kind": "user_prompt",
                    "interpretation_method": "ai_interpreted",
                    "verification_status": "unverified",
                    "label": "User prompt interpreted by the agent",
                },
                "output_dir": str(tmp_path / "output"),
            }
        ),
        encoding="utf-8",
    )

    completed = _command(request_path)

    assert completed.returncode == 2
    response = OperationFailure.model_validate_json(completed.stderr)
    assert response.error.code == "invalid_component_input"
    assert response.error.component_id == "dq.financial_analysis.earnings_comparison"
