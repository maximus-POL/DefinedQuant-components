# Component authoring

This folder contains the small set of tools used to create and check the public
component catalog. It is intentionally separate from both the financial components
in `categories/` and the reusable runtime in `shared/`.

There is no `dq` command. Every command below is an ordinary Python script run in
the project environment. In other words, `uv run` means “use the Python version and
dependencies locked for this project.”

## The one component template

`component-template/` is the canonical template. Its files have the same names as
the files authors inspect and edit in a real component; there are no `.tmpl` files,
profile overlays, or extension matrices.

| File | Purpose |
|---|---|
| `README.md` | Human explanation, formula, example, assumptions, and limitations. |
| `component.py` | Deterministic function plus canonical Pydantic `Inputs` and `Output`. |
| `contract.yaml` | Identity, discovery metadata, provenance, agent guidance, constraints, and display copy. |
| `evidence.yaml` | Executable numerical fixtures, assertions, and agent adapter cases. |
| `test_component.py` | Behaviour tests and hand-written invariant tests. |

The `{{PLACEHOLDER}}` values are replaced by `create_component.py`. Edit the
canonical folder itself when every future component should start differently.

## Create a category

From the `components/` project root:

```bash
uv run python authoring/create_category.py \
  --id statistics \
  --title "Statistics" \
  --summary "Deterministic descriptive statistics and diagnostics."
```

This creates `categories/statistics/README.md` with short YAML front matter followed
by ordinary Markdown. The README is the category’s human index; there is no hidden
category contract.

## Create a component

```bash
uv run python authoring/create_component.py \
  --category statistics \
  --group descriptive_statistics \
  --slug arithmetic_mean \
  --profile statistic \
  --title "Arithmetic Mean" \
  --author-name "Your Name" \
  --author-github "your-handle"
```

The generator creates exactly one direct child,
`categories/statistics/arithmetic_mean/`, containing the five canonical files. It
does not run Git commands and does not create anything outside this project.

Every generated contract requires four discovery fields:

- `aliases`: bounded natural-language names a user might supply;
- `intents`: stable lower-snake-case task identifiers;
- `input_concepts`: stable identifiers for the information the method consumes;
- `output_concepts`: stable identifiers for what it produces.

These fields are an authored routing contract, not marketing keywords. Keep them specific, avoid
synonym stuffing, and use `do_not_use_when` and `unsupported_scope` for adjacent requests the
component must not answer.

Treat existing intent and concept identifiers as a derived catalog vocabulary. Before introducing
a new identifier, inspect the current facets with `search_catalog.py --json` and reuse an exact
existing value when the semantics truly match. Put natural-language synonyms in `aliases`; do not
create synonymous concept IDs. A new concept must describe a genuinely distinct input or output
contract and should be justified in review.

## Search as a developer or agent

Search reads contract files only; it does not import component Python:

```bash
uv run python authoring/search_catalog.py "calculate returns from prices"
uv run python authoring/search_catalog.py "" \
  --category market_data \
  --input-concept price_series \
  --profile time_series
uv run python authoring/search_catalog.py "period price change" --json
```

Ranking is deterministic and returns explicit positive matches separately from boundary matches.
Values within one filter facet are ORed; populated facets are ANDed. Blank search lists the
filtered catalog. The result limit is bounded to 100. Static catalog schema version 2 binds the
exported discovery shape and the ranking semantics it declares. The repo-local adapter responses
remain adapter schema version 1; that transport version is separate from the static export schema.
Changing either public shape requires an explicit version decision so clients cannot silently
diverge.

## Check the catalog

Check everything:

```bash
uv run python authoring/check_component.py
```

Check one component while editing it:

```bash
uv run python authoring/check_component.py \
  categories/statistics/arithmetic_mean
```

The checker validates the two-level folder rule, both JSON Schemas, the Pydantic
model declarations, declarative constraint references, executable evidence inputs and output
paths, invariant-to-test references, agent-case output fields, and a component’s subject-hash
binding. Every `Output`
must extend `defined_quant.types.ComponentOutput`, which supplies the shared provenance,
interpretation, and visualization envelope. The checker also guarantees that a catalog-wide
adapter can invoke every component uniformly: every `Inputs` field must be accepted by the
component callable as a keyword (or through `**kwargs`), with no positional-only parameters or
hidden required arguments. Legacy split contracts and empty optional files are rejected.

Known answers, boundary cases, and cross-checks use one closed fixture and assertion vocabulary
and are collected directly from `evidence.yaml` by pytest. The only reserved fixture objects are
`$float` for explicitly tagged non-finite boundary values and `$repeat` for bounded repeated
values. Agent cases provide structured adapter inputs and expected compute, ask, or refusal
outcomes. Invariants remain hand-written property tests referenced by `test_id`.

Once every generated evidence case and referenced invariant passes, bind the evidence to the exact
current behaviour:

```bash
uv run python authoring/check_component.py \
  --bless categories/statistics/arithmetic_mean
```

`--bless` refuses scaffold `TODO`s, missing evidence, failed tests, and skipped
evidence tests.

## Export static website data

The website never imports or executes component Python. Component CI validates the
catalog and creates a deterministic JSON artifact instead:

```bash
uv run python authoring/export_catalog.py \
  --commit-sha "$GITHUB_SHA" \
  --release-version "0.1.0" \
  --release-label "Experimental Technical Preview"
```

The default output is `dist/catalog/catalog.json`. Catalog schema v2 contains no timestamp or
local filesystem path, and its component order and JSON keys are stable. Each component exposes
group, tags, discovery metadata, positive use cases, negative boundaries, assumptions,
limitations, trust data, and deterministic JSON Schemas generated from its canonical Pydantic
`Inputs` and `Output` models. The top-level `operation_protocol` record exports request, manifest,
success, failure, and result schemas directly from `defined_quant_protocol`; the same canonical
descriptor labels this operation path `unmanaged` and publishes its hash framing, domain, and
verification vector. Sorted facet arrays cover categories, groups, tags, intents, input concepts,
output concepts, lifecycles, and profiles. The private website can pin and consume that file as
data.

Schema export imports each component model only inside this authoring command. A website or other
consumer reads the generated JSON and never imports or executes component Python.

The exported `numerical: author_supplied` enum reports author-asserted numerical evidence, alongside
`exact_sha_attestation: none` and `domain_review: none`. A future CI attestation step must derive
and replace the exact-commit value only after proving that the checks ran for the exact release
commit; authors cannot claim it through a command-line flag. Every current component remains
lifecycle `draft` regardless of an export or successful engineering check.

For local development, omit the release arguments:

```bash
uv run python authoring/export_catalog.py
```

The exporter uses the current Git commit and labels the result as a development
catalog.

## Schemas

- `schemas/contract.schema.json` validates the merged component contract, including
  nested `guidance` and `display`.
- `schemas/evidence.schema.json` defines the closed executable numerical and agent-case DSL.

The Pydantic models in `component.py` remain the source of truth for input/output
types, units, and answer-changing defaults. Neither YAML schema duplicates them.
