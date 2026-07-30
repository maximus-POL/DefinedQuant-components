#!/usr/bin/env python3
"""Discover and inspect installed Defined Quant components."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from _source_runtime import activate_source_runtime

activate_source_runtime()

from defined_quant import (  # noqa: E402
    component_models,
    component_record,
    operation_protocol_schema,
    subject_hash,
)
from defined_quant.catalog import ComponentRecord  # noqa: E402
from defined_quant.discovery import (  # noqa: E402
    DiscoveryFilters,
    FieldMatch,
    SearchHit,
    SearchResults,
    component_facets,
    search_components,
)
from defined_quant.types import DQError  # noqa: E402

_SUMMARY_FIELDS = (
    "title",
    "group",
    "version",
    "lifecycle",
    "summary",
)
_FILTER_ARGUMENTS = (
    ("category", "categories"),
    ("group", "groups"),
    ("profile", "profiles"),
    ("tag", "tags"),
    ("intent", "intents"),
    ("input-concept", "input_concepts"),
    ("output-concept", "output_concepts"),
    ("lifecycle", "lifecycles"),
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _add_filters(parser: argparse.ArgumentParser) -> None:
    for option, destination in _FILTER_ARGUMENTS:
        parser.add_argument(
            f"--{option}",
            action="append",
            default=[],
            dest=destination,
            metavar=option.upper().replace("-", "_"),
            help=(
                f"Filter by {option.replace('-', ' ')}. Repeat to accept any value "
                "within this field."
            ),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and inspect the installed Defined Quant catalog."
    )
    parser.add_argument(
        "--catalog-root",
        type=Path,
        help="Optional project root, categories directory, or installed-package catalog root.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser(
        "list",
        help="Browse installed components and discovery facets in stable order.",
    )
    listing.add_argument(
        "--limit",
        type=_positive_int,
        default=100,
        help="Maximum components to return (default: 100).",
    )
    _add_filters(listing)

    search = commands.add_parser(
        "search",
        help="Retrieve ranked candidates with positive and boundary match explanations.",
    )
    search.add_argument("query", help="Plain-language financial intent.")
    search.add_argument(
        "--limit",
        type=_positive_int,
        default=5,
        help="Maximum candidates to return (default: 5).",
    )
    _add_filters(search)

    show = commands.add_parser(
        "show",
        help="Show one contract, discovery facets, canonical schemas, and subject hash.",
    )
    show.add_argument("component", help="Stable component ID or component directory.")
    return parser


def _filters(args: argparse.Namespace) -> DiscoveryFilters:
    return DiscoveryFilters(
        categories=tuple(args.categories),
        groups=tuple(args.groups),
        profiles=tuple(args.profiles),
        tags=tuple(args.tags),
        intents=tuple(args.intents),
        input_concepts=tuple(args.input_concepts),
        output_concepts=tuple(args.output_concepts),
        lifecycles=tuple(args.lifecycles),
    )


def _summary(record: ComponentRecord) -> dict[str, Any]:
    summary = {
        field: record.metadata.get(field)
        for field in _SUMMARY_FIELDS
        if record.metadata.get(field) is not None
    }
    summary.update(
        {
            "id": record.component_id,
            "category": record.category,
            "version": record.version,
            "facets": {
                name: list(values)
                for name, values in sorted(component_facets(record).items())
                if values
            },
        }
    )
    return summary


def _field_match(match: FieldMatch) -> dict[str, Any]:
    return {
        "field": match.field,
        "polarity": match.polarity,
        "terms": list(match.terms),
        "values": list(match.values),
    }


def _hit(hit: SearchHit, *, include_match: bool) -> dict[str, Any]:
    payload = _summary(hit.record)
    if include_match:
        payload["match"] = {
            "score": hit.score,
            "positive_matches": [
                _field_match(match) for match in hit.positive_matches
            ],
            "boundary_matches": [
                _field_match(match) for match in hit.boundary_matches
            ],
            "matched_terms": list(hit.matched_terms),
            "unmatched_terms": list(hit.unmatched_terms),
        }
    return payload


def _facets(results: SearchResults) -> dict[str, list[dict[str, Any]]]:
    return {
        name: [asdict(value) for value in values]
        for name, values in sorted(results.facets.items())
    }


def _results(
    query: str,
    *,
    root: Path | None,
    filters: DiscoveryFilters,
    limit: int,
) -> SearchResults:
    return search_components(
        query,
        root=root,
        filters=filters,
        limit=limit,
    )


def _list(
    *,
    root: Path | None,
    filters: DiscoveryFilters,
    limit: int,
) -> dict[str, Any]:
    results = _results("", root=root, filters=filters, limit=limit)
    return {
        "schema_version": 1,
        "returned_count": len(results.hits),
        "total_matches": results.total_matches,
        "limit": limit,
        "filters": asdict(results.filters),
        "facets": _facets(results),
        "components": [_hit(hit, include_match=False) for hit in results.hits],
    }


def _search(
    query: str,
    *,
    root: Path | None,
    filters: DiscoveryFilters,
    limit: int,
) -> dict[str, Any]:
    results = _results(query, root=root, filters=filters, limit=limit)
    return {
        "schema_version": 1,
        "query": results.query,
        "terms": list(results.terms),
        "returned_count": len(results.hits),
        "total_matches": results.total_matches,
        "limit": limit,
        "filters": asdict(results.filters),
        "facets": _facets(results),
        "components": [_hit(hit, include_match=True) for hit in results.hits],
    }


def _show(identifier: str, root: Path | None) -> dict[str, Any]:
    record = component_record(identifier, root=root)
    inputs, output = component_models(record)
    return {
        "schema_version": 1,
        "component": dict(record.metadata),
        "facets": {
            name: list(values)
            for name, values in sorted(component_facets(record).items())
            if values
        },
        "source_path": str(record.path.resolve()),
        "subject_hash": subject_hash(record),
        "input_schema": inputs.model_json_schema(),
        "output_schema": output.model_json_schema(),
        "operation_protocol": operation_protocol_schema(),
    }


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, DQError):
        return exc.as_dict()
    return {
        "code": "catalog_request_failed",
        "message": str(exc),
        "details": {"type": type(exc).__name__},
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "list":
            response = _list(
                root=args.catalog_root,
                filters=_filters(args),
                limit=args.limit,
            )
        elif args.command == "search":
            response = _search(
                args.query,
                root=args.catalog_root,
                filters=_filters(args),
                limit=args.limit,
            )
        else:
            response = _show(args.component, args.catalog_root)
    except (DQError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": _error(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
