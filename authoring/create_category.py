#!/usr/bin/env python3
"""Create one human-readable category folder under ``categories/``."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _project_root(override: Path | None) -> Path:
    return override.resolve() if override else Path(__file__).resolve().parents[1]


def _identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "category id must be lower snake_case and begin with a letter"
        )
    return value


def _front_matter_string(value: str) -> str:
    return json.dumps(value.strip(), ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True, type=_identifier, dest="category_id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = _project_root(args.root)
    categories = root / "categories"
    destination = categories / args.category_id
    title = args.title.strip()
    summary = args.summary.strip() if args.summary else f"Deterministic components for {title}."

    if not categories.is_dir():
        parser.error(f"missing categories directory: {categories}")
    if destination.exists():
        parser.error(f"category already exists: {destination}")
    if not title:
        parser.error("--title must not be empty")
    if not summary:
        parser.error("--summary must not be empty")
    if len(title) > 100:
        parser.error("--title must be at most 100 characters")
    if len(summary) > 240:
        parser.error("--summary must be at most 240 characters")

    content = (
        "---\n"
        f"id: {_front_matter_string(args.category_id)}\n"
        f"title: {_front_matter_string(title)}\n"
        f"summary: {_front_matter_string(summary)}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{summary}\n\n"
        "## Components\n\n"
        "Add each component as one direct subfolder. The generated catalog supplies the "
        "searchable index, so do not duplicate a component list here.\n"
    )
    destination.mkdir()
    readme = destination / "README.md"
    readme.write_text(content, encoding="utf-8")

    checker = root / "authoring" / "check_component.py"
    result = subprocess.run(
        [sys.executable, str(checker), "--root", str(root), str(destination)],
        cwd=root,
        check=False,
    )
    if result.returncode:
        print(
            f"Category was created at {destination}, but validation failed.",
            file=sys.stderr,
        )
        return result.returncode

    print(f"Created category: {destination}")
    print("Next:")
    print("1. Improve its README.md scope and shared conventions.")
    print(
        "2. Create a component with: uv run python authoring/create_component.py "
        f"--category {args.category_id} --group <group> --slug <slug> "
        "--profile <profile>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
