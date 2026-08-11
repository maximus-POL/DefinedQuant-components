from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from defined_quant_protocol import (
    OperationFailure,
    OperationManifest,
    OperationSuccess,
    operation_protocol_schema,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
CATALOG = SKILL_ROOT / "scripts" / "catalog.py"
RUNNER = SKILL_ROOT / "scripts" / "run_component.py"


def _command(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def test_catalog_lists_filters_and_inspects_installed_components() -> None:
    listed = _json_output(
        _command(
            str(CATALOG),
            "list",
            "--limit",
            "1",
            "--category",
            "market_data",
            "--profile",
            "time_series",
            "--tag",
            "returns",
            "--input-concept",
            "price_series",
            "--output-concept",
            "simple_return_series",
        )
    )
    assert listed["limit"] == 1
    assert listed["returned_count"] == 1
    assert listed["total_matches"] >= 1
    assert listed["components"][0]["id"] == "dq.market_data.simple_return"
    assert listed["filters"]["categories"] == ["market_data"]
    assert listed["filters"]["profiles"] == ["time_series"]
    assert listed["filters"]["tags"] == ["returns"]
    assert listed["filters"]["input_concepts"] == ["price_series"]
    assert listed["filters"]["output_concepts"] == ["simple_return_series"]
    assert listed["components"][0]["facets"]["output_concepts"] == [
        "calculation_diagnostics",
        "datapoint_lineage",
        "periodic_return_series",
        "return_timestamps",
        "simple_return_series",
        "visualization_specifications",
    ]

    shown = _json_output(
        _command(str(CATALOG), "show", "dq.market_data.simple_return")
    )
    assert shown["component"]["id"] == "dq.market_data.simple_return"
    assert set(shown["input_schema"]["properties"]) == {
        "declared_frequency",
        "price_kind",
        "prices",
        "timestamps",
    }
    assert "simple_return_series" in shown["facets"]["output_concepts"]
    assert len(shown["subject_hash"]) == 64
    assert shown["operation_protocol"]["protocol_version"] == "0.3.0"
    assert shown["operation_protocol"]["schemas"]["request"]["title"] == "OperationRequest"
    assert shown["operation_protocol"] == operation_protocol_schema()
    assert shown["source_path"] == "categories/market_data/simple_return"
    assert str(PROJECT_ROOT) not in json.dumps(shown)


def test_catalog_search_explains_positive_boundary_and_unmatched_terms() -> None:
    searched = _json_output(
        _command(
            str(CATALOG),
            "search",
            "simple total return from prices",
            "--limit",
            "3",
        )
    )

    assert searched["limit"] == 3
    assert searched["returned_count"] >= 1
    candidate = searched["components"][0]
    assert candidate["id"] == "dq.market_data.simple_return"
    assert candidate["match"]["positive_matches"]
    assert candidate["match"]["boundary_matches"]
    assert "total" in candidate["match"]["unmatched_terms"]
    assert all(
        match["polarity"] == "positive"
        for match in candidate["match"]["positive_matches"]
    )
    assert all(
        match["polarity"] == "boundary"
        for match in candidate["match"]["boundary_matches"]
    )

    boundary_only = _json_output(
        _command(str(CATALOG), "search", "reinvest dividends", "--limit", "3")
    )
    assert boundary_only["returned_count"] == 0
    assert boundary_only["total_matches"] == 0


def test_catalog_repeated_values_are_or_within_one_filter() -> None:
    searched = _json_output(
        _command(
            str(CATALOG),
            "search",
            "period price change",
            "--category",
            "not_a_category",
            "--category",
            "market_data",
            "--limit",
            "5",
        )
    )

    assert searched["filters"]["categories"] == [
        "market_data",
        "not_a_category",
    ]
    assert searched["components"][0]["id"] == "dq.market_data.simple_return"
    assert searched["components"][0]["match"]["positive_matches"]


def test_runner_validates_executes_and_renders_component(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "component-input.json"
    input_path.write_text(
        json.dumps(
            {
                "prices": [100.0, 103.0, 101.0, 105.0],
                "price_kind": "adjusted",
                "timestamps": [
                    "2026-07-20T00:00:00Z",
                    "2026-07-21T00:00:00Z",
                    "2026-07-22T00:00:00Z",
                    "2026-07-23T00:00:00Z",
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"

    response = OperationSuccess.model_validate(
        _json_output(
            _command(
                str(RUNNER),
                "--component",
                "dq.market_data.simple_return",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
            )
        )
    )
    manifest = response.manifest

    assert manifest.execution_mode == "unmanaged"
    assert manifest.component.id == "dq.market_data.simple_return"
    assert not Path(manifest.input.path).is_absolute()
    assert not Path(manifest.result.path).is_absolute()
    assert (output_dir / manifest.input.path).is_file()
    assert (output_dir / manifest.result.path).is_file()
    assert (output_dir / "manifest.json").is_file()
    assert len(manifest.artifacts) == 1
    assert not Path(manifest.artifacts[0].path).is_absolute()
    assert len(Path(manifest.artifacts[0].path).parts) == 1
    assert (output_dir / manifest.artifacts[0].path).read_text(encoding="utf-8").startswith(
        '<?xml version="1.0" encoding="UTF-8"?>'
    )

    saved_manifest = OperationManifest.model_validate_json(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert saved_manifest == manifest
    assert manifest.operation_hash == manifest.request.operation_hash
    assert str(output_dir) not in json.dumps(manifest.model_dump(mode="json"))

    result = json.loads((output_dir / manifest.result.path).read_text(encoding="utf-8"))
    assert result["component_id"] == "dq.market_data.simple_return"
    assert result["returns"] == [
        0.03,
        -0.019417475728155338,
        0.039603960396039604,
    ]


def test_runner_executes_scalar_component_when_requested_artifacts_are_absent(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "volatility-input.json"
    input_path.write_text(
        json.dumps(
            {
                "returns": [-0.01, 0.0, 0.02],
                "annualization_factor": 252.0,
                "return_kind": "log",
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "volatility-output"

    response = OperationSuccess.model_validate(
        _json_output(
            _command(
                str(RUNNER),
                "--component",
                "dq.volatility.historical_volatility",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
            )
        )
    )

    assert response.manifest.component.id == "dq.volatility.historical_volatility"
    assert response.manifest.artifacts == ()
    result = json.loads(
        (output_dir / response.manifest.result.path).read_text(encoding="utf-8")
    )
    assert result["return_kind"] == "log"
    assert result["annualization_factor"] == 252.0
    assert result["sample_size"] == 3
    assert len(result["derivations"]) == 2
    assert result["visualizations"] == []


def test_runner_returns_structured_input_error(tmp_path: Path) -> None:
    input_path = tmp_path / "invalid-input.json"
    input_path.write_text(
        json.dumps({"prices": [100.0, 101.0], "price_kind": "invented"}),
        encoding="utf-8",
    )

    completed = _command(
        str(RUNNER),
        "--component",
        "dq.market_data.simple_return",
        "--input",
        str(input_path),
        "--output-dir",
        str(tmp_path / "output"),
    )

    assert completed.returncode == 2
    response = OperationFailure.model_validate_json(completed.stderr)
    assert response.operation_hash is not None
    assert response.error.code == "invalid_component_input"
