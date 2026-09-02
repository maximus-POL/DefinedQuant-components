"""Phase-2 tests for the immutable, import-free contract discovery index."""

from __future__ import annotations

import importlib.abc
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from defined_quant.catalog import ComponentRecord, iter_components
from defined_quant.discovery import (
    FACET_NAMES,
    MAX_INDEXED_RECORDS,
    ContractIndex,
    DiscoveryFilters,
    SearchResults,
    search_components,
)
from defined_quant.service import DefinedQuantService
from defined_quant.types import ComponentContractError, ComponentNotFound

ROOT = Path(__file__).resolve().parents[2]
EVALUATION_CASES = ROOT / "docs" / "local_mcp" / "evaluation_cases.json"
PHASE1_DISCOVERY = ROOT / "authoring" / "tests" / "fixtures" / "discovery.json"


def _record(
    slug: str,
    *,
    category: str = "market_data",
    group: str = "transformations",
    aliases: tuple[str, ...] = (),
    intents: tuple[str, ...] = ("calculate_metric",),
    input_concepts: tuple[str, ...] = ("numeric_series",),
    output_concepts: tuple[str, ...] = ("numeric_result",),
    tags: tuple[str, ...] = ("quantitative",),
    use_when: tuple[str, ...] = ("A caller needs the supported calculation.",),
    do_not_use_when: tuple[str, ...] = ("A caller needs an unrelated calculation.",),
    unsupported_scope: tuple[str, ...] = ("Unrelated calculations are unsupported.",),
    limitations: tuple[str, ...] = ("The method does not infer missing conventions.",),
    lifecycle: str = "draft",
    profile: str = "statistic",
    callable_prefix: str = "defined_quant",
) -> ComponentRecord:
    component_id = f"dq.{category}.{slug}"
    metadata: dict[str, Any] = {
        "id": component_id,
        "category": category,
        "group": group,
        "slug": slug,
        "title": slug.replace("_", " ").title(),
        "summary": "A deterministic quantitative calculation.",
        "version": "0.1.0",
        "callable": f"{callable_prefix}.{category}.{slug}.component:{slug}",
        "tags": list(tags),
        "lifecycle": lifecycle,
        "template": {"profile": profile, "version": 1},
        "discovery": {
            "aliases": list(aliases or (slug.replace("_", " "),)),
            "intents": list(intents),
            "input_concepts": list(input_concepts),
            "output_concepts": list(output_concepts),
        },
        "guidance": {
            "use_when": list(use_when),
            "do_not_use_when": list(do_not_use_when),
            "unsupported_scope": list(unsupported_scope),
        },
        "limitations": list(limitations),
    }
    return ComponentRecord(
        component_id=component_id,
        category=category,
        slug=slug,
        version="0.1.0",
        callable_path=f"{callable_prefix}.{category}.{slug}.component:{slug}",
        path=Path("/synthetic_catalog") / category / slug,
        metadata=metadata,
    )


def _limit_record(index: int) -> ComponentRecord:
    """Return a compact unique record for construction-boundary tests."""

    slug = f"limit_{index:05d}"
    component_id = f"dq.benchmark.{slug}"
    return ComponentRecord(
        component_id=component_id,
        category="benchmark",
        slug=slug,
        version="0.1.0",
        callable_path=f"synthetic_components.benchmark.{slug}:never_import",
        path=Path("/synthetic_catalog/benchmark") / slug,
        metadata={
            "id": component_id,
            "category": "benchmark",
            "slug": slug,
        },
    )


def _match_projection(match: Any) -> dict[str, Any]:
    return {
        "field": match.field,
        "polarity": match.polarity,
        "terms": list(match.terms),
        "values": list(match.values),
    }


def _result_projection(result: SearchResults) -> dict[str, Any]:
    return {
        "query": result.query,
        "terms": list(result.terms),
        "filters": {
            name: list(getattr(result.filters, name)) for name in FACET_NAMES
        },
        "total_matches": result.total_matches,
        "hits": [
            {
                "component_id": hit.record.component_id,
                "score": hit.score,
                "positive_matches": [
                    _match_projection(match) for match in hit.positive_matches
                ],
                "boundary_matches": [
                    _match_projection(match) for match in hit.boundary_matches
                ],
                "matched_terms": list(hit.matched_terms),
                "unmatched_terms": list(hit.unmatched_terms),
            }
            for hit in result.hits
        ],
        "facets": {
            name: [[value.value, value.count] for value in result.facets[name]]
            for name in FACET_NAMES
        },
    }


def _phase1_projection(result: SearchResults) -> dict[str, Any]:
    def compact_match(match: Any) -> list[Any]:
        return [match.field, list(match.terms), list(match.values)]

    return {
        "query": result.query,
        "terms": list(result.terms),
        "filters": {
            name: list(values)
            for name in FACET_NAMES
            if (values := getattr(result.filters, name))
        },
        "total_matches": result.total_matches,
        "hits": [
            {
                "component_id": hit.record.component_id,
                "score": hit.score,
                "positive_matches": [
                    compact_match(match) for match in hit.positive_matches
                ],
                "boundary_matches": [
                    compact_match(match) for match in hit.boundary_matches
                ],
                "matched_terms": list(hit.matched_terms),
                "unmatched_terms": list(hit.unmatched_terms),
            }
            for hit in result.hits
        ],
        "facets": {
            name: [[value.value, value.count] for value in result.facets[name]]
            for name in FACET_NAMES
        },
    }


def test_index_takes_a_deeply_immutable_snapshot() -> None:
    source = _record("snapshot_record", aliases=("frozen needle",), tags=("original",))
    source_metadata = source.metadata
    assert isinstance(source_metadata, dict)
    source_discovery = source_metadata["discovery"]
    assert isinstance(source_discovery, dict)
    source_aliases = source_discovery["aliases"]
    assert isinstance(source_aliases, list)

    index = ContractIndex((source,))
    snapshot = index.get(source.component_id)

    source_metadata["title"] = "Mutated after indexing"
    source_aliases.append("mutated hook")
    source_metadata["tags"] = ["mutated"]

    assert snapshot is index.records[0]
    assert snapshot is index.by_component_id[source.component_id]
    assert snapshot.metadata["title"] == "Snapshot Record"
    result = index.search("frozen needle")
    assert result.total_matches == 1
    assert index.search("mutated hook").total_matches == 0

    with pytest.raises(TypeError):
        snapshot.metadata["title"] = "Cannot mutate"  # type: ignore[index]
    frozen_discovery = snapshot.metadata["discovery"]
    assert isinstance(frozen_discovery, Mapping)
    with pytest.raises(TypeError):
        frozen_discovery["aliases"] = ("Cannot mutate",)  # type: ignore[index]
    frozen_aliases = frozen_discovery["aliases"]
    assert isinstance(frozen_aliases, tuple)
    with pytest.raises(TypeError):
        frozen_aliases[0] = "Cannot mutate"  # type: ignore[index]
    with pytest.raises(TypeError):
        index.by_component_id[source.component_id] = source  # type: ignore[index]
    with pytest.raises(TypeError):
        result.facets["tags"] = ()  # type: ignore[index]

    # Public dataclasses remain deep-copyable into detached ordinary mappings.
    record_data = asdict(snapshot)
    result_data = asdict(result)
    assert isinstance(record_data["metadata"], dict)
    assert isinstance(record_data["metadata"]["discovery"], dict)
    assert isinstance(result_data["facets"], dict)
    record_data["metadata"]["title"] = "Detached mutation"
    assert snapshot.metadata["title"] == "Snapshot Record"


def test_index_has_a_stable_id_map_and_refuses_duplicate_ids() -> None:
    zeta = _record("zeta")
    alpha = _record("alpha")
    index = ContractIndex((zeta, alpha))

    assert isinstance(index.records, tuple)
    assert [record.component_id for record in index.records] == [
        alpha.component_id,
        zeta.component_id,
    ]
    assert tuple(index.by_component_id) == (alpha.component_id, zeta.component_id)
    assert index.get(alpha.component_id) is index.records[0]

    with pytest.raises(ComponentContractError, match="duplicate component id"):
        ContractIndex((alpha, alpha))
    with pytest.raises(ComponentNotFound, match="not installed"):
        index.get("dq.market_data.missing")


def test_service_builds_its_contract_index_once(tmp_path: Path) -> None:
    source = _record(
        "once",
        category="benchmark",
        aliases=("one time needle",),
    )
    component_dir = tmp_path / "benchmark" / "once"
    component_dir.mkdir(parents=True)
    contract_path = component_dir / "contract.yaml"
    contract_path.write_text(
        json.dumps(source.metadata, ensure_ascii=False),
        encoding="utf-8",
    )

    service = DefinedQuantService(catalog_root=tmp_path)
    first_index = service.contract_index
    assert first_index.get(source.component_id).component_id == source.component_id

    # A process-lifetime snapshot must not reopen contracts on later searches.
    contract_path.write_text("{", encoding="utf-8")

    assert service.contract_index is first_index
    first = service.search_components("one time needle", limit=5)
    second = service.search_components("one time needle", limit=5)
    assert _result_projection(first) == _result_projection(second)
    assert [hit.record.component_id for hit in first.hits] == [source.component_id]


def test_service_preserves_an_injected_empty_index() -> None:
    index = ContractIndex(())

    service = DefinedQuantService(contract_index=index)

    assert service.contract_index is index
    assert service.search_components("anything").total_matches == 0


def test_index_construction_succeeds_just_below_record_ceiling() -> None:
    index = ContractIndex(
        _limit_record(record_index)
        for record_index in range(MAX_INDEXED_RECORDS - 1)
    )

    assert len(index) == MAX_INDEXED_RECORDS - 1


def test_index_construction_fails_closed_just_above_record_ceiling() -> None:
    with pytest.raises(
        ComponentContractError,
        match=rf"supports at most {MAX_INDEXED_RECORDS} component records",
    ):
        ContractIndex(
            _limit_record(record_index)
            for record_index in range(MAX_INDEXED_RECORDS + 1)
        )


class _RejectComponentImports(importlib.abc.MetaPathFinder):
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.attempts: list[str] = []

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> Any:
        if fullname == self.prefix or fullname.startswith(f"{self.prefix}."):
            self.attempts.append(fullname)
            raise AssertionError(f"discovery attempted to import {fullname}")
        return None


def test_index_build_search_and_lookup_are_import_free() -> None:
    sentinel = _RejectComponentImports("synthetic_components")
    record = _record(
        "import_sentinel",
        aliases=("import free needle",),
        callable_prefix="synthetic_components",
    )
    sys.meta_path.insert(0, sentinel)
    try:
        index = ContractIndex((record,))
        result = index.search("import free needle")
        assert index.get(record.component_id).callable_path.startswith(
            "synthetic_components."
        )
        assert [hit.record.component_id for hit in result.hits] == [
            record.component_id
        ]
    finally:
        sys.meta_path.remove(sentinel)
    assert sentinel.attempts == []


def test_index_preserves_existing_ranking_explanations_filters_and_facets() -> None:
    records = (
        _record(
            "simple_return",
            aliases=("arithmetic return", "period price return"),
            intents=("calculate_simple_returns",),
            input_concepts=("price_series",),
            output_concepts=("simple_return_series",),
            tags=("returns", "time_series"),
            do_not_use_when=("The request requires logarithmic returns.",),
        ),
        _record(
            "log_return",
            aliases=("logarithmic return", "continuously compounded return"),
            intents=("calculate_log_returns",),
            input_concepts=("price_series",),
            output_concepts=("log_return_series",),
            tags=("returns", "time_series"),
        ),
        _record(
            "generic_transform",
            tags=("generic",),
            use_when=("The caller wants an arithmetic return for a comparison.",),
        ),
    )
    forward = ContractIndex(records)
    reverse = ContractIndex(reversed(records))
    fixture = json.loads(PHASE1_DISCOVERY.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == 1
    assert fixture["source_commit"] == "727ac4301f33a32aed53637255d6b5480003a10f"
    assert fixture["match_tuple"] == ["field", "terms", "values"]

    for case in fixture["cases"]:
        expected = dict(case["projection"])
        facet_projection = expected.pop("facet_projection")
        expected["facets"] = fixture["facet_projections"][facet_projection]
        filters = DiscoveryFilters(
            **{
                name: tuple(expected["filters"].get(name, ()))
                for name in FACET_NAMES
            }
        )
        query = expected["query"]
        limit = case["limit"]
        actual_results = (
            forward.search(query, filters=filters, limit=limit),
            reverse.search(query, filters=filters, limit=limit),
            search_components(
                query,
                records=records,
                filters=filters,
                limit=limit,
            ),
            search_components(
                query,
                records=reversed(records),
                filters=filters,
                limit=limit,
            ),
        )

        for actual in actual_results:
            assert _phase1_projection(actual) == expected, case["id"]


def _evaluation_case() -> dict[str, Any]:
    fixture = json.loads(EVALUATION_CASES.read_text(encoding="utf-8"))
    return next(
        case
        for case in fixture["cases"]
        if case["id"] == "generated_10000_component_discovery"
    )


def _expanded_value(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        expanded = value
        for token, replacement in replacements.items():
            expanded = expanded.replace(token, replacement)
        assert "{index_" not in expanded
        return expanded
    if isinstance(value, list):
        return [_expanded_value(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _expanded_value(item, replacements) for key, item in value.items()
        }
    return value


def _generated_evaluation_records(case: Mapping[str, Any]) -> tuple[ComponentRecord, ...]:
    generator = case["fixture"]["generator"]
    index_range = generator["index_range"]
    overrides = generator["overrides"]["rules"]
    records: list[ComponentRecord] = []

    for index in range(
        index_range["start_inclusive"],
        index_range["stop_exclusive"],
        index_range["step"],
    ):
        replacements = {
            "{index_5_digits}": f"{index:05d}",
            "{index_modulo_100_2_digits}": f"{index % 100:02d}",
        }
        expanded = _expanded_value(generator["record_template"], replacements)
        assert isinstance(expanded, dict)
        for rule in overrides:
            if index not in rule["when_index_in"]:
                continue
            assert rule["operation"] == "append_once"
            target: Any = expanded
            for part in rule["target_pointer"].removeprefix("/").split("/"):
                target = target[part]
            assert isinstance(target, list)
            if rule["value"] not in target:
                target.append(rule["value"])

        metadata = expanded["metadata"]
        assert isinstance(metadata, dict)
        records.append(
            ComponentRecord(
                component_id=expanded["component_id"],
                category=expanded["category"],
                slug=expanded["slug"],
                version=expanded["version"],
                callable_path=expanded["callable_path"],
                path=Path(expanded["path"]),
                metadata=metadata,
            )
        )

    assert len(records) == generator["record_count"]
    return tuple(records)


def test_frozen_10000_component_case_is_bounded_import_free_and_order_stable() -> None:
    case = _evaluation_case()
    records = _generated_evaluation_records(case)
    expected = case["expected_bounded_outputs"]
    generator = case["fixture"]["generator"]
    query = case["fixture"]["query"]
    limit = expected["maximum_search_candidates"]
    sentinel = _RejectComponentImports("synthetic_components")
    projections: list[dict[str, Any]] = []

    sys.meta_path.insert(0, sentinel)
    try:
        assert generator["materialized_records_committed"] is False
        assert expected["committed_generated_record_count"] == 0
        assert not any(
            record.component_id.startswith("dq.benchmark.component_")
            for record in iter_components(root=ROOT)
        )
        for ordered_records in (records, reversed(records)):
            index = ContractIndex(ordered_records)
            result = index.search(query, limit=limit)
            projection = _result_projection(result)
            projections.append(projection)

            assert len(index.records) == expected["generated_record_count"]
            assert result.total_matches == expected["total_matches"]
            assert [hit.record.component_id for hit in result.hits] == expected["hits"]
            assert len(result.hits) <= limit
            assert result.total_matches > len(result.hits)
            assert all(hit.score == 80 for hit in result.hits)
            assert all(
                hit.matched_terms == ("bounded", "zephyr", "transform")
                for hit in result.hits
            )
            assert all(hit.unmatched_terms == () for hit in result.hits)
            response_bytes = json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            assert len(response_bytes) <= expected["maximum_search_response_bytes"]
    finally:
        sys.meta_path.remove(sentinel)

    assert projections[0] == projections[1]
    assert sentinel.attempts == []
