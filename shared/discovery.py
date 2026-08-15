"""Deterministic, import-free discovery and ranking for the component catalog.

The search index is the component contract itself.  This module deliberately reads only
``ComponentRecord.metadata`` and never imports component Python, so agents can shortlist a large
catalog before loading any calculation.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeVar, cast

from defined_quant.catalog import ComponentRecord, iter_components
from defined_quant.types import ComponentContractError, ComponentNotFound

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

_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


class _FrozenMapping(Mapping[_Key, _Value]):
    """Read-only mapping that produces an ordinary detached dict when deep-copied."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[_Key, _Value]) -> None:
        self._values = MappingProxyType(dict(values))

    def __getitem__(self, key: _Key) -> _Value:
        return self._values[key]

    def __iter__(self) -> Iterator[_Key]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[_Key, _Value]:
        return {
            deepcopy(key, memo): deepcopy(value, memo)
            for key, value in self._values.items()
        }


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


@dataclass(frozen=True, slots=True)
class _IndexedField:
    name: str
    values: tuple[str, ...]
    weight: int = 0


@dataclass(frozen=True, slots=True)
class _IndexedComponent:
    record: ComponentRecord
    positive_fields: tuple[_IndexedField, ...]
    boundary_fields: tuple[_IndexedField, ...]
    facets: Mapping[FacetName, tuple[str, ...]]


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


def _matching_terms(
    query_terms: Sequence[str], indexed: frozenset[str]
) -> tuple[str, ...]:
    matches = [
        term
        for term in query_terms
        if any(form in indexed for form in _term_forms(term))
    ]
    return tuple(matches)


def _normalised_phrase(value: str) -> str:
    return " ".join(_raw_tokens(value))


def _freeze_contract_value(value: Any) -> Any:
    """Deeply snapshot JSON-compatible contract data without mutable containers."""

    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("contract metadata keys must be strings")
            frozen[key] = _freeze_contract_value(item)
        return _FrozenMapping(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_contract_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        f"contract metadata value of type {type(value).__name__} is not JSON-compatible"
    )


def _snapshot_record(record: ComponentRecord) -> ComponentRecord:
    metadata = _freeze_contract_value(record.metadata)
    if not isinstance(metadata, Mapping):  # pragma: no cover - ComponentRecord invariant
        raise TypeError("component metadata must be a mapping")
    return ComponentRecord(
        component_id=record.component_id,
        category=record.category,
        slug=record.slug,
        version=record.version,
        callable_path=record.callable_path,
        path=Path(record.path),
        metadata=cast(Mapping[str, Any], metadata),
    )


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
    return _FrozenMapping(
        {
            name: tuple(
                sorted(
                    {_normalise_filter_value(value) for value in values if value.strip()}
                )
            )
            for name, values in raw.items()
        }
    )


def catalog_facets(
    records: Iterable[ComponentRecord],
) -> Mapping[FacetName, tuple[FacetValue, ...]]:
    """Count every facet in stable name/value order."""

    counters: dict[FacetName, Counter[str]] = {name: Counter() for name in FACET_NAMES}
    for record in records:
        for name, values in component_facets(record).items():
            counters[name].update(values)
    return _FrozenMapping(
        {
            name: tuple(
                FacetValue(value=value, count=count)
                for value, count in sorted(counters[name].items())
            )
            for name in FACET_NAMES
        }
    )


def _compile_field(
    record: ComponentRecord,
    field_name: str,
    *,
    weight: int = 0,
) -> _IndexedField:
    values = _field_values(record, field_name)
    return _IndexedField(
        name=field_name,
        values=values,
        weight=weight,
    )


def _compile_record(record: ComponentRecord) -> _IndexedComponent:
    return _IndexedComponent(
        record=record,
        positive_fields=tuple(
            _compile_field(record, field_name, weight=weight)
            for field_name, weight in _POSITIVE_FIELD_WEIGHTS
        ),
        boundary_fields=tuple(
            _compile_field(record, field_name) for field_name in _BOUNDARY_FIELDS
        ),
        facets=component_facets(record),
    )


def _field_match(
    indexed: _IndexedField,
    query_terms: Sequence[str],
    *,
    polarity: MatchPolarity,
) -> FieldMatch | None:
    if not query_terms:
        return None
    terms = _matching_terms(query_terms, _value_terms(indexed.values))
    if not terms:
        return None
    return FieldMatch(
        field=indexed.name,
        polarity=polarity,
        terms=terms,
        values=indexed.values,
    )


def _rank_record(
    indexed: _IndexedComponent,
    query_phrase: str,
    terms: tuple[str, ...],
) -> SearchHit | None:
    positive: list[FieldMatch] = []
    boundary: list[FieldMatch] = []
    score = 0

    for indexed_field in indexed.positive_fields:
        match = _field_match(
            indexed_field,
            terms,
            polarity="positive",
        )
        if match is None:
            continue
        positive.append(match)
        score += indexed_field.weight * len(match.terms)
        if query_phrase and any(
            query_phrase == _normalised_phrase(value)
            for value in indexed_field.values
        ):
            score += indexed_field.weight * 2

    for indexed_field in indexed.boundary_fields:
        match = _field_match(
            indexed_field,
            terms,
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
        record=indexed.record,
        score=score,
        positive_matches=tuple(positive),
        boundary_matches=tuple(boundary),
        matched_terms=matched,
        unmatched_terms=unmatched,
    )


def _catalog_facets_from_indexed(
    records: Iterable[_IndexedComponent],
) -> Mapping[FacetName, tuple[FacetValue, ...]]:
    counters: dict[FacetName, Counter[str]] = {name: Counter() for name in FACET_NAMES}
    for indexed in records:
        for name, values in indexed.facets.items():
            counters[name].update(values)
    return _FrozenMapping(
        {
            name: tuple(
                FacetValue(value=value, count=count)
                for value, count in sorted(counters[name].items())
            )
            for name in FACET_NAMES
        }
    )


def _validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")


class ContractIndex:
    """One deep-immutable, import-free snapshot of component contract metadata."""

    __slots__ = (
        "_all_component_ids",
        "_by_component_id",
        "_components",
        "_facet_postings",
        "_positive_postings",
        "_records",
    )

    def __init__(self, records: Iterable[ComponentRecord]) -> None:
        snapshots: dict[str, ComponentRecord] = {}
        for record in records:
            if record.component_id in snapshots:
                raise ComponentContractError(
                    f"duplicate component id: {record.component_id}",
                    component_id=record.component_id,
                )
            snapshots[record.component_id] = _snapshot_record(record)

        ordered_ids = tuple(sorted(snapshots))
        ordered_records = tuple(snapshots[component_id] for component_id in ordered_ids)
        compiled = {
            record.component_id: _compile_record(record) for record in ordered_records
        }

        positive_postings: defaultdict[str, set[str]] = defaultdict(set)
        facet_postings: dict[FacetName, defaultdict[str, set[str]]] = {
            name: defaultdict(set) for name in FACET_NAMES
        }
        for component_id, indexed in compiled.items():
            for indexed_field in indexed.positive_fields:
                for term in _value_terms(indexed_field.values):
                    positive_postings[term].add(component_id)
            for name, values in indexed.facets.items():
                for value in values:
                    facet_postings[name][value].add(component_id)

        self._all_component_ids = frozenset(ordered_ids)
        self._records = ordered_records
        self._by_component_id = MappingProxyType(
            {record.component_id: record for record in ordered_records}
        )
        self._components = MappingProxyType(compiled)
        self._positive_postings = MappingProxyType(
            {
                term: frozenset(component_ids)
                for term, component_ids in positive_postings.items()
            }
        )
        self._facet_postings = MappingProxyType(
            {
                name: MappingProxyType(
                    {
                        value: frozenset(component_ids)
                        for value, component_ids in postings.items()
                    }
                )
                for name, postings in facet_postings.items()
            }
        )

    @classmethod
    def from_catalog(cls, *, root: str | Path | None = None) -> ContractIndex:
        """Snapshot the local or installed contract catalog exactly once."""

        return cls(iter_components(root=root))

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[ComponentRecord, ...]:
        """Return the stable component-ID ordered immutable snapshot."""

        return self._records

    @property
    def by_component_id(self) -> Mapping[str, ComponentRecord]:
        """Return the read-only stable-ID map for this snapshot."""

        return self._by_component_id

    def get(self, component_id: str) -> ComponentRecord:
        """Look up a contract without touching the filesystem or importing code."""

        record = self._by_component_id.get(component_id)
        if record is None:
            raise ComponentNotFound(
                f"component is not installed: {component_id}",
                component_id=component_id,
            )
        return record

    def search(
        self,
        query: str,
        *,
        filters: DiscoveryFilters | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> SearchResults:
        """Search this snapshot with the established ranking and explanation semantics."""

        _validate_limit(limit)
        applied_filters = filters or DiscoveryFilters()
        terms = tokenize(query)

        candidate_ids = set(self._all_component_ids)
        for name in FACET_NAMES:
            requested = getattr(applied_filters, name)
            if not requested:
                continue
            matching_ids: set[str] = set()
            postings = self._facet_postings[name]
            for value in requested:
                matching_ids.update(postings.get(value, ()))
            candidate_ids.intersection_update(matching_ids)

        if terms:
            positive_ids: set[str] = set()
            for term in terms:
                for form in _term_forms(term):
                    positive_ids.update(self._positive_postings.get(form, ()))
            candidate_ids.intersection_update(positive_ids)

        query_phrase = _normalised_phrase(query)
        hits: list[SearchHit] = []
        eligible: list[_IndexedComponent] = []
        for component_id in sorted(candidate_ids):
            indexed = self._components[component_id]
            hit = _rank_record(indexed, query_phrase, terms)
            if hit is not None:
                hits.append(hit)
                eligible.append(indexed)

        hits.sort(key=lambda hit: (-hit.score, hit.record.component_id))
        return SearchResults(
            query=query,
            terms=terms,
            filters=applied_filters,
            total_matches=len(hits),
            hits=tuple(hits[:limit]),
            facets=_catalog_facets_from_indexed(eligible),
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

    _validate_limit(limit)
    if records is not None and root is not None:
        raise ValueError("root cannot be combined with explicit records")
    applied_filters = filters or DiscoveryFilters()
    tokenize(query)
    index = ContractIndex(
        records if records is not None else iter_components(root=root)
    )
    return index.search(query, filters=applied_filters, limit=limit)


__all__ = [
    "DEFAULT_LIMIT",
    "FACET_NAMES",
    "MAX_LIMIT",
    "ContractIndex",
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
