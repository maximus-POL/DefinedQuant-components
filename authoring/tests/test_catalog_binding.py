"""Tests for semantic versus layout-only subject-hash boundaries."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from defined_quant.catalog import (
    ComponentRecord,
    component_record,
    iter_components,
    subject_hash,
)
from defined_quant.types import ComponentContractError

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


def test_catalog_discovers_seven_real_components_in_stable_order() -> None:
    assert [record.component_id for record in iter_components(root=ROOT)] == [
        "dq.market_data.log_return",
        "dq.market_data.monthly_return_matrix",
        "dq.market_data.rebased_price_index",
        "dq.market_data.simple_return",
        "dq.performance.drawdown",
        "dq.volatility.historical_volatility",
        "dq.volatility.rolling_historical_volatility",
    ]


def test_component_dependency_cycle_detection_runs_across_real_contracts(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "catalog-copy"
    shutil.copytree(ROOT / "categories", copied_root / "categories")
    simple_path = (
        copied_root / "categories" / "market_data" / "simple_return" / "contract.yaml"
    )
    log_path = copied_root / "categories" / "market_data" / "log_return" / "contract.yaml"
    simple = json.loads(simple_path.read_text(encoding="utf-8"))
    logarithmic = json.loads(log_path.read_text(encoding="utf-8"))
    simple["depends_on"] = ["dq.market_data.log_return"]
    logarithmic["depends_on"] = ["dq.market_data.simple_return"]
    simple_path.write_text(json.dumps(simple), encoding="utf-8")
    log_path.write_text(json.dumps(logarithmic), encoding="utf-8")

    with pytest.raises(ComponentContractError, match="component dependency cycle"):
        subject_hash("dq.market_data.simple_return", root=copied_root)
