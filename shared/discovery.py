"""Deterministic, import-free discovery and ranking for the component catalog.

The search index is the component contract itself.  This module deliberately reads only
``ComponentRecord.metadata`` and never imports component Python, so agents can shortlist a large
catalog before loading any calculation.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from defined_quant.catalog import ComponentRecord, iter_components

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

FacetName = Literal[
    "categories",
    "groups",
    "tags",
    "intents",
    "input_concepts",
    "output_concepts",
    "lifecycles",
    "profiles",
]
MatchPolarity = Literal["positive", "boundary"]

FACET_NAMES: tuple[FacetName, ...] = (
    "categories",
    "groups",
    "tags",
    "intents",
    "input_concepts",
    "output_concepts",
    "lifecycles",
    "profiles",
)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "calculate",
        "compute",
        "for",
        "from",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "that",
        "the",
        "this",
        "to",
        "use",
        "want",
        "with",
    }
)

# Scores are intentionally integers so ordering cannot vary by platform floating-point details.
# Higher-value authored discovery fields outrank explanatory prose.
_POSITIVE_FIELD_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("id", 14),
    ("slug", 14),
    ("title", 14),
    ("discovery.aliases", 13),
    ("discovery.intents", 12),
    ("discovery.output_concepts", 11),
    ("discovery.input_concepts", 10),
    ("tags", 8),
    ("summary", 6),
    ("guidance.use_when", 4),
    ("category", 3),
    ("group", 3),
    ("template.profile", 2),
)
_BOUNDARY_FIELDS: tuple[str, ...] = (
    "guidance.do_not_use_when",
    "guidance.unsupported_scope",
    "limitations",
)


@dataclass(frozen=True, slots=True)
class DiscoveryFilters:
    """Exact catalog filters.

    Values within one facet are ORed.  Populated facets are ANDed with one another.
    """

    categories: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    input_concepts: tuple[str, ...] = ()
    output_concepts: tuple[str, ...] = ()
    lifecycles: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in FACET_NAMES:
            raw_values = getattr(self, name)
            if len(raw_values) > 64:
                raise ValueError(f"{name} accepts at most 64 filter values")
            values = tuple(sorted({_normalise_filter_value(value) for value in raw_values}))
            object.__setattr__(self, name, values)


@dataclass(frozen=True, slots=True)
class FieldMatch:
    """Why query terms matched one positive or boundary contract field."""

    field: str
    polarity: MatchPolarity
    terms: tuple[str, ...]
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One eligible component and its inspectable ranking explanation."""

    record: ComponentRecord
    score: int
    positive_matches: tuple[FieldMatch, ...]
    boundary_matches: tuple[FieldMatch, ...]
    matched_terms: tuple[str, ...]
    unmatched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FacetValue:
    """One deterministic facet value and count."""

    value: str
    count: int


@dataclass(frozen=True, slots=True)
class SearchResults:
    """A bounded result page plus complete counts for the eligible result set."""

    query: str
    terms: tuple[str, ...]
    filters: DiscoveryFilters
    total_matches: int
    hits: tuple[SearchHit, ...]
    facets: Mapping[FacetName, tuple[FacetValue, ...]] = field(compare=False)


def _normalise_filter_value(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("filter values must be strings")
    normalised = unicodedata.normalize("NFKC", value).casefold().strip()
    if not normalised:
        raise ValueError("filter values must not be empty")
    return normalised


def _raw_tokens(text: str) -> tuple[str, ...]:
    normalised = unicodedata.normalize("NFKC", text).casefold()
    result: list[str] = []
    token: list[str] = []
    for character in normalised:
        if character.isalnum():
            token.append(character)
        elif token:
            result.append("".join(token))
            token = []
    if token:
        result.append("".join(token))
    return tuple(result)


def _term_forms(term: str) -> frozenset[str]:
    """Return a deliberately small inflection set, not an opaque linguistic stem."""

    forms = {term}
    if len(term) > 4 and term.endswith("ies"):
        forms.add(term[:-3] + "y")
    elif (
        len(term) > 4
        and term.endswith("s")
        and not term.endswith(("ss", "is", "us", "series"))
    ):
        forms.add(term[:-1])
    return frozenset(forms)


def tokenize(text: str) -> tuple[str, ...]:
    """Normalize a query into unique, ordered search terms.

    Unicode NFKC and case folding are deterministic.  Punctuation and snake-case separators are
    boundaries, common request filler is removed, and no external NLP dependency is required.
    """

    if not isinstance(text, str):
        raise TypeError("search query must be a string")
    seen: set[str] = set()
    terms: list[str] = []
    for token in _raw_tokens(text):
        if token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return tuple(terms)


def _string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _nested(metadata: Mapping[str, object], path: str) -> object:
    value: object = metadata
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _field_values(record: ComponentRecord, path: str) -> tuple[str, ...]:
    values = _string_values(_nested(record.metadata, path))
    return tuple(value for value in values if value)


def _value_terms(values: Sequence[str]) -> frozenset[str]:
    return frozenset(
        form
        for value in values
        for token in _raw_tokens(value)
        for form in _term_forms(token)
    )


def _matching_terms(query_terms: Sequence[str], values: Sequence[str]) -> tuple[str, ...]:
    indexed = _value_terms(values)
    matches = [
        term
        for term in query_terms
        if any(form in indexed for form in _term_forms(term))
    ]
    return tuple(matches)


def _normalised_phrase(value: str) -> str:
    return " ".join(_raw_tokens(value))


def _matches_phrase(query: str, values: Sequence[str]) -> bool:
    phrase = _normalised_phrase(query)
    return bool(phrase) and any(phrase == _normalised_phrase(value) for value in values)


def component_facets(record: ComponentRecord) -> Mapping[FacetName, tuple[str, ...]]:
    """Return normalized exact-match facets derived only from one component contract."""

    metadata = record.metadata
    discovery = metadata.get("discovery")
    discovery_mapping = discovery if isinstance(discovery, Mapping) else {}
    template = metadata.get("template")
    template_mapping = template if isinstance(template, Mapping) else {}

    raw: dict[FacetName, tuple[str, ...]] = {
        "categories": _string_values(metadata.get("category")),
        "groups": _string_values(metadata.get("group")),
        "tags": _string_values(metadata.get("tags")),
        "intents": _string_values(discovery_mapping.get("intents")),
        "input_concepts": _string_values(discovery_mapping.get("input_concepts")),
        "output_concepts": _string_values(discovery_mapping.get("output_concepts")),
        "lifecycles": _string_values(metadata.get("lifecycle")),
        "profiles": _string_values(template_mapping.get("profile")),
    }
    return {
        name: tuple(
            sorted({_normalise_filter_value(value) for value in values if value.strip()})
        )
        for name, values in raw.items()
    }


def _passes_filters(record: ComponentRecord, filters: DiscoveryFilters) -> bool:
    facets = component_facets(record)
    for name in FACET_NAMES:
        requested = getattr(filters, name)
        if requested and not set(requested).intersection(facets[name]):
            return False
    return True


def catalog_facets(
    records: Iterable[ComponentRecord],
) -> Mapping[FacetName, tuple[FacetValue, ...]]:
    """Count every facet in stable name/value order."""

    counters: dict[FacetName, Counter[str]] = {name: Counter() for name in FACET_NAMES}
    for record in records:
        for name, values in component_facets(record).items():
            counters[name].update(values)
    return {
        name: tuple(
            FacetValue(value=value, count=count)
            for value, count in sorted(counters[name].items())
        )
        for name in FACET_NAMES
    }


def _field_match(
    record: ComponentRecord,
    query_terms: Sequence[str],
    *,
    field_name: str,
    polarity: MatchPolarity,
) -> FieldMatch | None:
    values = _field_values(record, field_name)
    terms = _matching_terms(query_terms, values)
    if not terms:
        return None
    return FieldMatch(
        field=field_name,
        polarity=polarity,
        terms=terms,
        values=values,
    )


def _rank_record(record: ComponentRecord, query: str, terms: tuple[str, ...]) -> SearchHit | None:
    positive: list[FieldMatch] = []
    boundary: list[FieldMatch] = []
    score = 0

    for field_name, weight in _POSITIVE_FIELD_WEIGHTS:
        match = _field_match(
            record,
            terms,
            field_name=field_name,
            polarity="positive",
        )
        if match is None:
            continue
        positive.append(match)
        score += weight * len(match.terms)
        if _matches_phrase(query, match.values):
            score += weight * 2

    for field_name in _BOUNDARY_FIELDS:
        match = _field_match(
            record,
            terms,
            field_name=field_name,
            polarity="boundary",
        )
        if match is not None:
            boundary.append(match)

    if terms and not positive:
        return None

    matched = tuple(
        term
        for term in terms
        if any(term in match.terms for match in positive)
    )
    unmatched = tuple(term for term in terms if term not in matched)
    boundary_terms = {
        term
        for match in boundary
        for term in match.terms
    }
    # Reward coverage and modestly demote requests that directly touch an exclusion boundary.
    score += 5 * len(matched)
    score = max(0, score - 3 * len(boundary_terms))
    return SearchHit(
        record=record,
        score=score,
        positive_matches=tuple(positive),
        boundary_matches=tuple(boundary),
        matched_terms=matched,
        unmatched_terms=unmatched,
    )


def search_components(
    query: str,
    *,
    records: Iterable[ComponentRecord] | None = None,
    filters: DiscoveryFilters | None = None,
    limit: int = DEFAULT_LIMIT,
    root: str | Path | None = None,
) -> SearchResults:
    """Search contracts with stable ranking and explicit positive/boundary explanations.

    A nonblank query must match at least one positive field.  Text found only in
    ``do_not_use_when``, ``unsupported_scope``, or ``limitations`` never makes a component
    eligible.  Pass an explicit record iterable for generated/in-memory catalogs, or omit it to
    discover the local/installed catalog.
    """

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if records is not None and root is not None:
        raise ValueError("root cannot be combined with explicit records")

    applied_filters = filters or DiscoveryFilters()
    terms = tokenize(query)
    candidates = tuple(records) if records is not None else tuple(iter_components(root=root))
    hits: list[SearchHit] = []
    for record in candidates:
        if not _passes_filters(record, applied_filters):
            continue
        hit = _rank_record(record, query, terms)
        if hit is not None:
            hits.append(hit)

    hits.sort(key=lambda hit: (-hit.score, hit.record.component_id))
    eligible_records = tuple(hit.record for hit in hits)
    return SearchResults(
        query=query,
        terms=terms,
        filters=applied_filters,
        total_matches=len(hits),
        hits=tuple(hits[:limit]),
        facets=catalog_facets(eligible_records),
    )


__all__ = [
    "DEFAULT_LIMIT",
    "FACET_NAMES",
    "MAX_LIMIT",
    "DiscoveryFilters",
    "FacetName",
    "FacetValue",
    "FieldMatch",
    "MatchPolarity",
    "SearchHit",
    "SearchResults",
    "catalog_facets",
    "component_facets",
    "search_components",
    "tokenize",
]
