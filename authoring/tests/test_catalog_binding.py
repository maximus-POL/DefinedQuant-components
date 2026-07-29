"""Tests for semantic versus layout-only subject-hash boundaries."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from defined_quant.catalog import ComponentRecord, component_record, subject_hash

ROOT = Path(__file__).resolve().parents[2]
COMPONENT_ID = "dq.market_data.simple_return"


def _metadata_copy() -> tuple[ComponentRecord, dict[str, Any]]:
    record = component_record(COMPONENT_ID, root=ROOT)
    return record, deepcopy(dict(record.metadata))


def test_semantic_display_copy_is_subject_bound() -> None:
    record, metadata = _metadata_copy()
    display = cast(dict[str, Any], metadata["display"])
    display["formula"] = "an intentionally different formula"

    changed = replace(record, metadata=metadata)

    assert subject_hash(changed) != subject_hash(record)


def test_layout_only_display_hints_are_not_subject_bound() -> None:
    record, metadata = _metadata_copy()
    display = cast(dict[str, Any], metadata["display"])
    display["layout_density"] = "compact"

    changed = replace(record, metadata=metadata)

    assert subject_hash(changed) == subject_hash(record)
