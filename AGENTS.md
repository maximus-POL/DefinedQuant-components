# AGENTS.md

Persistent instructions for coding agents working on the public Defined Quant components project.
Runtime instructions for using a calculation live under `guidance` in its `contract.yaml`; do not
conflate them with these repository instructions.

## Orientation

| Path | Purpose |
|---|---|
| `categories/<category>/README.md` | Human scope and conventions for one financial topic |
| `categories/<category>/<component>/` | One component, exactly two directory levels below `categories/` |
| `shared/types/` | Canonical financial types; protected |
| `shared/validation.py` | Closed declarative constraint evaluator; protected |
| `shared/catalog.py` | Discovery, loading, and `subject_hash`; protected |
| `shared/discovery.py` | Import-free deterministic search, ranking, explanations, and facets; protected |
| `shared/charts.py` | Trusted renderer for typed visualization specifications; protected |
| `authoring/component-template/` | The one canonical five-file component template |
| `authoring/schemas/` | JSON Schemas for `contract.yaml` and `evidence.yaml`; protected |
| `authoring/*.py` | Explicit creation, checking, and catalog-export tools; protected |
| `.agents/skills/use-defined-quant/` | Optional catalog-wide Codex adapter; never component-specific |
| `ARCHITECTURE.md` | Structure, package projection, trust binding, and publication boundary |

## Commands

Run these from the `components/` folder:

```bash
uv sync
uv run pytest
uv run pytest categories/market_data/simple_return
uv run python authoring/check_component.py
uv run python authoring/check_component.py --bless categories/market_data/simple_return
uv run python authoring/search_catalog.py "calculate returns from prices"
uv run python authoring/create_component.py \
  --category <category> --group <group> --slug <slug> --profile <profile>
uv run python authoring/create_category.py --id <id> --title "<title>"
uv run python authoring/export_catalog.py
uv run ruff check shared categories authoring
uv run --no-editable mypy shared categories authoring/*.py
```

## Invariants

1. Pydantic models in `component.py` are the single source of truth for inputs, outputs, units,
   and defaults. `contract.yaml` must not contain input or output type declarations. `README.md`
   may explain a convention but may never define one. Every `Output` extends the shared
   `defined_quant.types.ComponentOutput` provenance and visualization envelope.
2. Components are exactly `categories/<category>/<component>/`. Do not add a third component
   nesting level.
3. Every component has five visible files: `README.md`, `component.py`, `contract.yaml`,
   `evidence.yaml`, and `test_component.py`. Do not add empty optional files.
4. Trust uses separate bindings. `subject_hash` identifies behavior and enforceable contract;
   evidence content is bound separately; exact-commit CI proves what ran. Domain review remains a
   distinct assertion.
5. Status and evidence displays are derived. The only authored maturity field is `lifecycle`.
   Never add an authored composite status or badge.
6. Constraints are data, evaluated by `shared/validation.py` with a closed vocabulary. Never
   `eval`, `exec`, or import a component merely to evaluate its rules.
7. A state that makes output meaningless is blocking; a merely uncertain state is a warning.
8. No implicit default may change the answer: annualization factors, day counts, compounding,
   return kinds, and similar conventions must be supplied, visibly defaulted, or refused.
9. The wheel must include component contracts, evidence, and README files.
10. `pydantic` is the only runtime dependency unless an explicit decision changes that.
11. Test data is synthetic and seeded. Never commit vendor or scraped market data.
12. Preserve the honest preview copy: explicit evidence types, `Domain review: none`, the
    non-claims block, and the “Experimental Technical Preview” label.
13. The private website is not part of this project. Export static catalog data; do not add site
    routes, React code, or deployment state here.
14. Agent-host integration is catalog-wide. It discovers canonical component contracts and must
    not duplicate a component's financial logic, defaults, examples, or guidance.
15. Every contract has bounded `discovery` aliases, intents, input concepts, and output concepts.
    Intents and concepts are stable lower-snake-case identifiers. Search and filtering must read
    contracts only and must never import component code.

## Adding a component

1. Create the category with `authoring/create_category.py` if it does not exist.
2. Use `authoring/create_component.py`; do not hand-create the folder.
3. Complete all five files. The important work is explicit guidance, unsupported scope, and the
   blocking-versus-warning split.
4. Run the component tests and checker.
5. Bless the evidence binding only after tests pass, then open a pull request. Never push a
   component directly to `main`.

When a financial convention is unclear, stop and ask. A plausible guess is worse than an explicit
blocker in this project.

## Delivery policy

This folder is the independent `maximus-POL/DefinedQuant-components` repository. After a
completed task changes tracked source, contracts, evidence, tests, documentation, dependencies,
or CI, publish the coherent change automatically unless the user explicitly requested local-only
work, no commit, or no PR.

1. Inspect `git status` and the complete diff. Do not absorb unrelated or pre-existing manual
   changes.
2. Run the relevant checks above; component, schema, shared-runtime, and authoring changes require
   the catalog checker and the proportionate Python test/lint/type gates.
3. Create or reuse an `agent/<short-task-name>` branch. Never commit directly to `main`.
4. Stage explicit intended paths, commit the coherent change, push the branch, and open a draft
   PR against `main`.
5. Update an existing PR for the same task instead of opening a duplicate.
6. If the private website must change too, publish that work as a separate PR in
   `maximus-POL/DefinedQuant-website` and cross-reference both PRs.
7. Never merge, enable auto-merge, publish a release, or delete a branch without a separate user
   request.

Read-only work, planning, generated catalogs, caches, failed validation, and unfinished
experiments do not create PRs. If GitHub authentication or networking is unavailable, keep the
local change and report the exact blocker.
