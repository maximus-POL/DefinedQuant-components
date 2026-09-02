"""Build hook that packages a JSON registry without adding PyYAML at runtime."""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hatchling.builders.hooks.plugin.interface import (  # type: ignore[import-not-found]
    BuildHookInterface,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from authoring.build_registry_bundle import (  # noqa: E402
    registry_bundle,
    write_registry_bundle,
)


class CustomBuildHook(BuildHookInterface):  # type: ignore[misc]
    """Compile authored YAML into the core wheel's private registry-data directory."""

    _temporary: TemporaryDirectory[str] | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        if self.target_name != "wheel":
            return
        self._temporary = TemporaryDirectory(prefix="defined-quant-registry-")
        output = Path(self._temporary.name) / "registry.json"
        write_registry_bundle(
            registry_bundle(Path(self.root) / "registry"),
            output,
        )
        force_include = build_data.setdefault("force_include", {})
        force_include[str(output)] = "defined_quant/registry_data/registry.json"

    def finalize(
        self,
        version: str,
        build_data: dict[str, Any],
        artifact_path: str,
    ) -> None:
        del version, build_data, artifact_path
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
