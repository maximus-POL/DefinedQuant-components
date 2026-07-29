#!/usr/bin/env python3
"""Create one component from the single canonical component template."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
PROFILES = ("convention", "statistic", "pricing_model", "time_series", "diagnostic")
TEMPLATE_FILES = (
    "README.md",
    "component.py",
    "contract.yaml",
    "evidence.yaml",
    "test_component.py",
)


def _project_root(override: Path | None) -> Path:
    return override.resolve() if override else Path(__file__).resolve().parents[1]


def _identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise argparse.ArgumentTypeError("values must be lower snake_case and begin with a letter")
    return value


def _render(source: Path, replacements: dict[str, str]) -> str:
    rendered = source.read_text(encoding="utf-8")
    for name, value in replacements.items():
        rendered = rendered.replace(f"{{{{{name}}}}}", value)
    rendered = rendered.replace("COMPONENT_FUNCTION", replacements["COMPONENT_FUNCTION"])
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"{source} contains an unresolved placeholder")
    return rendered.rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", required=True, type=_identifier)
    parser.add_argument("--slug", required=True, type=_identifier)
    parser.add_argument(
        "--group",
        required=True,
        type=_identifier,
        help="stable thematic group used for catalog filtering",
    )
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--title")
    parser.add_argument("--author-name", default="TODO")
    parser.add_argument("--author-github", default="TODO")
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = _project_root(args.root)
    template = root / "authoring" / "component-template"
    category = root / "categories" / args.category
    destination = category / args.slug
    title = (args.title or args.slug.replace("_", " ").title()).strip()
    author_name = args.author_name.strip()
    author_github = args.author_github.strip()

    if not (category / "README.md").is_file():
        parser.error(
            f"category does not exist or has no README.md: {category}; "
            "use authoring/create_category.py first"
        )
    if destination.exists():
        parser.error(f"component already exists: {destination}")
    if not title:
        parser.error("--title must not be empty")
    if not author_name or not author_github:
        parser.error("author values must not be empty")

    missing_templates = [name for name in TEMPLATE_FILES if not (template / name).is_file()]
    if missing_templates:
        parser.error("canonical template is incomplete: " + ", ".join(missing_templates))

    replacements = {
        "CATEGORY": args.category,
        "GROUP": args.group,
        "SLUG": args.slug,
        "COMPONENT_FUNCTION": args.slug,
        "PROFILE": args.profile,
        "TITLE": title,
        "TITLE_JSON": json.dumps(title, ensure_ascii=False),
        "AUTHOR_NAME_JSON": json.dumps(author_name, ensure_ascii=False),
        "AUTHOR_GITHUB_JSON": json.dumps(author_github, ensure_ascii=False),
        "TODAY": dt.date.today().isoformat(),
    }

    try:
        rendered = {name: _render(template / name, replacements) for name in TEMPLATE_FILES}
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))

    destination.mkdir()
    for name, content in rendered.items():
        (destination / name).write_text(content, encoding="utf-8")

    checker = root / "authoring" / "check_component.py"
    result = subprocess.run(
        [sys.executable, str(checker), "--root", str(root), str(destination)],
        cwd=root,
        check=False,
    )
    if result.returncode:
        print(
            f"Component was created at {destination}, but validation failed.",
            file=sys.stderr,
        )
        return result.returncode

    print(f"Created component: {destination}")
    print("Next:")
    print("1. Replace every TODO in the five files; do not add a third folder level.")
    print("2. Replace the skipped scaffold test with deterministic evidence tests.")
    print(
        f"3. Run: uv run python authoring/check_component.py categories/{args.category}/{args.slug}"
    )
    print(
        "4. After tests pass, bind evidence with: "
        "uv run python authoring/check_component.py --bless "
        f"categories/{args.category}/{args.slug}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
