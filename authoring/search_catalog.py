#!/usr/bin/env python3
"""Search and filter component contracts without importing calculation code."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from defined_quant.discovery import (
    MAX_LIMIT,
    DiscoveryFilters,
    FieldMatch,
    SearchHit,
    SearchResults,
    search_components,
)


def _match_json(match: FieldMatch) -> dict[str, Any]:
    return {
        "field": match.field,
        "polarity": match.polarity,
        "terms": list(match.terms),
        "values": list(match.values),
    }


def _hit_json(hit: SearchHit) -> dict[str, Any]:
    metadata = hit.record.metadata
    return {
        "id": hit.record.component_id,
        "category": hit.record.category,
        "group": metadata.get("group"),
        "slug": hit.record.slug,
        "title": metadata.get("title"),
        "summary": metadata.get("summary"),
        "score": hit.score,
        "matched_terms": list(hit.matched_terms),
        "unmatched_terms": list(hit.unmatched_terms),
        "positive_matches": [_match_json(match) for match in hit.positive_matches],
        "boundary_matches": [_match_json(match) for match in hit.boundary_matches],
    }


def _limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def _print_human(query: str, result: SearchResults) -> None:
    label = f" for {query!r}" if query.strip() else ""
    print(
        f"{result.total_matches} matching component(s){label}; "
        f"showing {len(result.hits)}."
    )
    for hit in result.hits:
        title = hit.record.metadata.get("title", hit.record.slug)
        print(f"\n{hit.record.component_id}  score={hit.score}  {title}")
        summary = hit.record.metadata.get("summary")
        if isinstance(summary, str):
            print(f"  {summary}")
        for label_name, matches in (
            ("positive", hit.positive_matches),
            ("boundary", hit.boundary_matches),
        ):
            if matches:
                explanation = "; ".join(
                    f"{match.field}={','.join(match.terms)}" for match in matches
                )
                print(f"  {label_name}: {explanation}")
        if hit.unmatched_terms:
            print(f"  unmatched query terms: {', '.join(hit.unmatched_terms)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="natural-language task or blank to list",
    )
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--intent", action="append", default=[])
    parser.add_argument("--input-concept", action="append", default=[])
    parser.add_argument("--output-concept", action="append", default=[])
    parser.add_argument("--lifecycle", action="append", default=[])
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--limit", type=_limit, default=20)
    parser.add_argument("--json", action="store_true", help="emit a stable machine-readable result")
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    filters = DiscoveryFilters(
        categories=tuple(args.category),
        groups=tuple(args.group),
        tags=tuple(args.tag),
        intents=tuple(args.intent),
        input_concepts=tuple(args.input_concept),
        output_concepts=tuple(args.output_concept),
        lifecycles=tuple(args.lifecycle),
        profiles=tuple(args.profile),
    )
    result = search_components(
        args.query,
        filters=filters,
        limit=args.limit,
        root=args.root,
    )

    if not args.json:
        _print_human(args.query, result)
        return 0

    payload = {
        "query": result.query,
        "terms": list(result.terms),
        "filters": asdict(result.filters),
        "total_matches": result.total_matches,
        "facets": {
            name: [asdict(value) for value in values]
            for name, values in result.facets.items()
        },
        "hits": [_hit_json(hit) for hit in result.hits],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
