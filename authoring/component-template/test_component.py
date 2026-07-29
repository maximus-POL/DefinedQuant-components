"""Evidence and behaviour tests for ``dq.{{CATEGORY}}.{{SLUG}}``."""

from __future__ import annotations

from importlib import import_module

import pytest

component_module = import_module("defined_quant.{{CATEGORY}}.{{SLUG}}.component")
Inputs = component_module.Inputs
component_function = getattr(component_module, "{{SLUG}}")


@pytest.mark.skip(reason="Replace the canonical component scaffold before publishing.")
def test_component_scaffold() -> None:
    inputs = Inputs(values=(1.0, 2.0))
    result = component_function(**inputs.model_dump(mode="python"))
    assert result.values
