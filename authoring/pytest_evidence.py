"""Pytest collector that turns component evidence YAML records into executable cases."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from authoring.evidence_runtime import (
    EXECUTABLE_EVIDENCE_SECTIONS,
    execute_agent_case,
    execute_numerical_case,
    generated_item_name,
    load_evidence,
)


class EvidenceItem(pytest.Item):
    def __init__(
        self,
        *,
        section: str,
        record: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.section = section
        self.record = record

    def runtest(self) -> None:
        component_dir = Path(str(self.path)).parent
        if self.section == "agent_cases":
            execute_agent_case(component_dir, self.record)
        else:
            execute_numerical_case(component_dir, self.record)

    def reportinfo(self) -> tuple[Path, int, str]:
        return Path(str(self.path)), 0, f"evidence case: {self.name}"


class EvidenceFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        evidence = load_evidence(Path(str(self.path)))
        for section in (*EXECUTABLE_EVIDENCE_SECTIONS, "agent_cases"):
            records = evidence.get(section, [])
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, Mapping) or not isinstance(record.get("id"), str):
                    continue
                yield EvidenceItem.from_parent(
                    self,
                    name=generated_item_name(section, record["id"]),
                    section=section,
                    record=record,
                )


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.File | None:
    if file_path.name != "evidence.yaml":
        return None
    try:
        relative = file_path.relative_to(Path(str(parent.config.rootpath)) / "categories")
    except ValueError:
        return None
    if len(relative.parts) != 3:
        return None
    return EvidenceFile.from_parent(parent, path=file_path)


__all__ = ["EvidenceFile", "EvidenceItem", "pytest_collect_file"]
