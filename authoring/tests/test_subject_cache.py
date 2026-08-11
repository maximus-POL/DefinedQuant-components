"""Regression tests for cached subject lookup and explicit fresh verification."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import defined_quant.catalog as catalog
import pytest
from defined_quant import invalidate_subject_cache, subject_hash, verify_subject
from defined_quant.market_data.simple_return.component import simple_return
from defined_quant.types import ComponentContractError

ROOT = Path(__file__).resolve().parents[2]
COMPONENT_ID = "dq.market_data.simple_return"


@pytest.fixture(autouse=True)
def _reset_subject_cache() -> Iterator[None]:
    invalidate_subject_cache()
    yield
    invalidate_subject_cache()


def test_stable_id_lookups_share_one_normalized_root_cache_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = catalog._subject_manifest

    def counted(
        record: catalog.ComponentRecord,
        stack: tuple[str, ...],
        catalog_root: Path,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original(record, stack, catalog_root)

    monkeypatch.setattr(catalog, "_subject_manifest", counted)

    first = subject_hash(COMPONENT_ID, root=ROOT)
    second = subject_hash(COMPONENT_ID, root=ROOT / "categories")

    assert first == second
    assert calls == 1


def test_invalidation_forces_a_fresh_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    original = catalog._subject_manifest

    def counted(
        record: catalog.ComponentRecord,
        stack: tuple[str, ...],
        catalog_root: Path,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original(record, stack, catalog_root)

    monkeypatch.setattr(catalog, "_subject_manifest", counted)

    subject_hash(COMPONENT_ID, root=ROOT)
    invalidate_subject_cache()
    subject_hash(COMPONENT_ID, root=ROOT)

    assert calls == 2


def test_explicit_records_remain_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    record = catalog.component_record(COMPONENT_ID, root=ROOT)
    calls = 0
    original = catalog._subject_manifest

    def counted(
        selected: catalog.ComponentRecord,
        stack: tuple[str, ...],
        catalog_root: Path,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original(selected, stack, catalog_root)

    monkeypatch.setattr(catalog, "_subject_manifest", counted)

    assert subject_hash(record) == subject_hash(record)
    assert calls == 2


def test_different_catalog_roots_never_share_cache_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_root = tmp_path / "catalog-copy"
    shutil.copytree(ROOT / "categories", copied_root / "categories")
    calls = 0
    original_manifest = catalog._subject_manifest

    def counted(
        record: catalog.ComponentRecord,
        stack: tuple[str, ...],
        catalog_root: Path,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original_manifest(record, stack, catalog_root)

    monkeypatch.setattr(catalog, "_subject_manifest", counted)

    original = subject_hash(COMPONENT_ID, root=ROOT)
    copied = subject_hash(COMPONENT_ID, root=copied_root)

    assert copied == original
    assert calls == 2


def test_verify_subject_observes_disk_change_and_refreshes_cache(tmp_path: Path) -> None:
    copied_root = tmp_path / "catalog-copy"
    shutil.copytree(ROOT / "categories", copied_root / "categories")
    contract_path = copied_root / "categories" / "market_data" / "simple_return" / "contract.yaml"

    original = subject_hash(COMPONENT_ID, root=copied_root)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["display"]["formula"] = "changed formula for fresh verification"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    assert subject_hash(COMPONENT_ID, root=copied_root) == original
    refreshed = verify_subject(COMPONENT_ID, root=copied_root)
    assert refreshed != original
    assert subject_hash(COMPONENT_ID, root=copied_root) == refreshed


def test_component_result_reuses_freshly_verified_installed_subject() -> None:
    verified = verify_subject(COMPONENT_ID)

    result = simple_return((100.0, 101.0), price_kind="adjusted")

    assert result.subject_hash == verified


def test_verify_subject_accepts_only_stable_component_ids() -> None:
    with pytest.raises(ComponentContractError, match="stable component ID"):
        verify_subject("categories/market_data/simple_return")
