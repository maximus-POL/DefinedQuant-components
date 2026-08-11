"""Contract-only discovery tests, including large-catalog deterministic behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from defined_quant.catalog import ComponentRecord
from defined_quant.discovery import (
    DiscoveryFilters,
    catalog_facets,
    search_components,
    tokenize,
)

CATALOG_ROOT = Path(__file__).resolve().parents[2]


def _record(
    slug: str,
    *,
    category: str = "market_data",
    group: str = "transformations",
    title: str | None = None,
    summary: str = "A deterministic quantitative calculation.",
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
) -> ComponentRecord:
    component_id = f"dq.{category}.{slug}"
    metadata: dict[str, Any] = {
        "id": component_id,
        "category": category,
        "group": group,
        "slug": slug,
        "title": title or slug.replace("_", " ").title(),
        "summary": summary,
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
        callable_path=f"defined_quant.{category}.{slug}.component:{slug}",
        path=Path("/catalog") / category / slug,
        metadata=metadata,
    )


def test_tokenization_is_unicode_safe_deterministic_and_small_stemmed() -> None:
    assert tokenize("  PLEASE calculate Price_Returns—Café returns!  ") == (
        "price",
        "returns",
        "café",
    )
    assert tokenize("the and from") == ()


def test_search_and_filters_use_nfkc_casefold_normalization() -> None:
    record = _record(
        "street_return",
        category="performance",
        aliases=("Straße return",),
    )

    result = search_components(
        "STRASSE return",
        records=(record,),
        filters=DiscoveryFilters(categories=("  PERFORMANCE  ",)),
    )

    assert result.total_matches == 1
    assert result.hits[0].matched_terms == ("strasse", "return")


def test_real_catalog_ranks_simple_return_for_period_price_change() -> None:
    result = search_components(
        "calculate period price changes",
        root=CATALOG_ROOT,
    )

    assert result.total_matches >= 1
    assert result.hits[0].record.component_id == "dq.market_data.simple_return"
    assert result.hits[0].positive_matches


def test_real_catalog_ranks_log_return_and_historical_volatility_by_intent() -> None:
    logarithmic = search_components(
        "compute continuously compounded returns",
        root=CATALOG_ROOT,
    )
    volatility = search_components(
        "annualized historical volatility from log returns",
        root=CATALOG_ROOT,
    )

    assert logarithmic.hits[0].record.component_id == "dq.market_data.log_return"
    assert volatility.hits[0].record.component_id == (
        "dq.volatility.historical_volatility"
    )


def test_alias_and_concept_fields_outrank_explanatory_prose() -> None:
    canonical = _record(
        "simple_return",
        aliases=("arithmetic return", "period price return"),
        intents=("calculate_simple_returns",),
        input_concepts=("price_series",),
        output_concepts=("simple_return_series",),
    )
    prose_only = _record(
        "generic_transform",
        use_when=("The caller wants an arithmetic return for a comparison.",),
    )

    result = search_components(
        "calculate arithmetic returns from prices",
        records=(prose_only, canonical),
    )

    assert [hit.record.component_id for hit in result.hits] == [
        canonical.component_id,
        prose_only.component_id,
    ]
    assert result.hits[0].score > result.hits[1].score
    assert any(
        match.field == "discovery.aliases"
        for match in result.hits[0].positive_matches
    )


def test_negative_boundary_only_text_never_makes_a_component_eligible() -> None:
    simple = _record(
        "simple_return",
        aliases=("simple return",),
        do_not_use_when=("The request requires logarithmic returns.",),
        unsupported_scope=("Logarithmic return construction.",),
    )

    result = search_components("logarithmic", records=(simple,))

    assert result.total_matches == 0
    assert result.hits == ()


def test_boundary_matches_are_explained_and_demoted_not_silently_refused() -> None:
    simple = _record(
        "simple_return",
        aliases=("simple return",),
        output_concepts=("simple_return_series",),
        do_not_use_when=("The request requires logarithmic returns.",),
    )
    logarithmic = _record(
        "log_return",
        aliases=("logarithmic return", "continuously compounded return"),
        output_concepts=("log_return_series",),
    )

    result = search_components("logarithmic returns", records=(simple, logarithmic))

    assert [hit.record.component_id for hit in result.hits] == [
        logarithmic.component_id,
        simple.component_id,
    ]
    simple_hit = result.hits[1]
    assert simple_hit.boundary_matches
    assert simple_hit.boundary_matches[0].polarity == "boundary"
    assert "logarithmic" in simple_hit.unmatched_terms


def test_filters_are_or_within_a_facet_and_and_across_facets() -> None:
    market = _record(
        "simple_return",
        tags=("returns", "time_series"),
        input_concepts=("price_series",),
        output_concepts=("simple_return_series",),
        profile="time_series",
    )
    risk = _record(
        "value_at_risk",
        category="risk",
        tags=("risk", "tail"),
        input_concepts=("return_series",),
        output_concepts=("value_at_risk",),
        profile="statistic",
    )

    result = search_components(
        "",
        records=(risk, market),
        filters=DiscoveryFilters(
            categories=("market_data",),
            tags=("returns", "risk"),
            input_concepts=("price_series",),
            profiles=("time_series",),
        ),
    )

    assert [hit.record.component_id for hit in result.hits] == [market.component_id]
    assert result.facets["tags"][0].value == "returns"


def test_facets_have_stable_values_and_counts() -> None:
    first = _record("first", tags=("shared", "alpha"))
    second = _record("second", tags=("shared", "beta"))

    facets = catalog_facets((second, first))

    assert [(item.value, item.count) for item in facets["tags"]] == [
        ("alpha", 1),
        ("beta", 1),
        ("shared", 2),
    ]


def test_result_facets_count_the_complete_eligible_set_before_bounding() -> None:
    records = (
        _record("first", tags=("shared", "alpha")),
        _record("second", tags=("shared", "beta")),
    )

    result = search_components("", records=records, limit=1)

    assert result.total_matches == 2
    assert len(result.hits) == 1
    assert [(item.value, item.count) for item in result.facets["tags"]] == [
        ("alpha", 1),
        ("beta", 1),
        ("shared", 2),
    ]


def test_ambiguous_broad_query_has_stable_tie_break_and_total_count() -> None:
    records = (
        _record("zeta", intents=("calculate_return",)),
        _record("alpha", intents=("calculate_return",)),
    )

    result = search_components("calculate a return", records=records, limit=1)

    assert result.total_matches == 2
    assert len(result.hits) == 1
    assert result.hits[0].record.component_id == "dq.market_data.alpha"


def test_exact_tokens_do_not_create_substring_false_positives() -> None:
    record = _record("mean_reversion", aliases=("mean reversion",))

    assert search_components("version", records=(record,)).total_matches == 0


def test_one_thousand_records_remain_stably_ranked_and_bounded() -> None:
    records = tuple(
        _record(
            f"synthetic_{index:04d}",
            aliases=("needle method",) if index in {7, 777} else (f"method {index}",),
        )
        for index in reversed(range(1000))
    )

    result = search_components("needle", records=records, limit=1)

    assert result.total_matches == 2
    assert len(result.hits) == 1
    assert result.hits[0].record.component_id == "dq.market_data.synthetic_0007"


@pytest.mark.parametrize("limit", [0, 101])
def test_search_limit_is_strictly_bounded(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        search_components("", records=(), limit=limit)


def test_explicit_records_and_root_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        search_components("", records=(), root=Path("."))
