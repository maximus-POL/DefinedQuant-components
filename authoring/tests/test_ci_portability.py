"""Freeze the native, installed-wheel CI contract without shell assumptions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "defined-quant.yml"
RUNNER = ROOT / "authoring" / "run_native_wheel_matrix.py"
ISOLATION_PLUGIN = ROOT / "authoring" / "pytest_wheel_isolation.py"
RELEASE_EXTRAS = ROOT / "authoring" / "prepare_release_extras.py"
GIT_ATTRIBUTES = ROOT / ".gitattributes"


def _workflow() -> tuple[str, dict[str, Any]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return text, loaded


def test_native_matrix_is_exactly_the_six_mandatory_release_cells() -> None:
    text, workflow = _workflow()
    jobs = workflow["jobs"]
    native = jobs["verify-native"]

    assert native["needs"] == "build-release-wheels"
    assert native["strategy"] == {
        "fail-fast": False,
        "matrix": {
            "os": ["windows-2025", "macos-15", "ubuntu-24.04"],
            "python": ["3.11", "3.13"],
        },
    }
    assert native["runs-on"] == "${{ matrix.os }}"
    assert "continue-on-error" not in text
    assert "windows-latest" not in text
    assert "macos-latest" not in text
    assert "exclude:" not in text


def test_workflow_run_commands_are_shell_neutral() -> None:
    _text, workflow = _workflow()
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            command = step.get("run")
            if command is None:
                continue
            without_expressions = re.sub(r"\$\{\{[^}]+\}\}", "", command)
            assert "\n" not in command
            assert "\\" not in command
            assert "<<" not in command
            assert "$" not in without_expressions
            assert ".venv/bin" not in command
            assert ".wheel-check/bin" not in command
            assert not any(character in command for character in "*?[]")
            assert "shell" not in step


def test_wheels_are_built_once_and_consumed_by_every_native_cell() -> None:
    text, workflow = _workflow()
    jobs = workflow["jobs"]
    build_steps = jobs["build-release-wheels"]["steps"]
    native_steps = jobs["verify-native"]["steps"]

    assert text.count("python authoring/build_release_wheels.py") == 1
    assert "uv build" not in text
    assert any(step.get("uses", "").startswith("actions/upload-artifact@") for step in build_steps)
    assert any(
        step.get("uses", "").startswith("actions/download-artifact@")
        for step in native_steps
    )
    assert [
        step["run"]
        for step in native_steps
        if "run" in step
    ] == [
        "uv run --no-project --python ${{ matrix.python }} "
        "python authoring/run_native_wheel_matrix.py"
    ]


def test_native_runner_covers_every_required_behavior_from_installed_wheels() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    isolation = ISOLATION_PLUGIN.read_text(encoding="utf-8")

    assert '"--require-hashes"' in runner
    assert '"--no-deps"' in runner
    assert '"installed_wheel_smoke.py"' in runner
    assert '"check_component.py"' in runner
    assert runner.count('"pytest"') == 2
    assert runner.count('"pythonpath=."') == 2
    assert '"mcp_server" / "tests"' in runner
    assert '"authoring.pytest_wheel_isolation"' in runner
    for required_module in (
        "test_agent_protocol.py",
        "test_alpha_platforms.py",
        "test_alpha_product_story.py",
        "test_data_records.py",
        "test_dataset_registry.py",
        "test_local_host_platform.py",
        "test_operation_records.py",
        "test_operation_runtime.py",
        "test_session_cas.py",
        "test_stdio_framing.py",
        "test_windows_local_host.py",
        "test_worker_runtime.py",
        "mcp_server/tests/test_packaging.py",
        "mcp_server/tests/test_server.py",
    ):
        assert required_module in isolation


def test_dq_native_artifact_sources_have_platform_independent_bytes() -> None:
    attributes = GIT_ATTRIBUTES.read_text(encoding="utf-8").splitlines()

    assert "adapters/dq_native/src/** text eol=lf" in attributes


def test_release_extras_publish_the_canonical_component_page_artifact() -> None:
    workflow_text, _workflow_data = _workflow()
    release_extras = RELEASE_EXTRAS.read_text(encoding="utf-8")

    assert '"export_component_pages.py"' in release_extras
    assert '"component-pages.json"' in release_extras
    assert '"export_catalog.py"' not in release_extras
    assert "dist/catalog/component-pages.json" in workflow_text
    assert "dist/catalog/catalog.json" not in workflow_text
