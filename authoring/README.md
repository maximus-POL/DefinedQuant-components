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
| `evidence.yaml` | Numerical evidence, test bindings, and agent evaluation cases. |
| `test_component.py` | Behaviour and evidence tests referenced by `evidence.yaml`. |

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
filtered catalog. The result limit is bounded to 100. Catalog schema version 2 binds the exported
discovery shape, canonical component schemas, operation protocol, and these ranking semantics;
changing any of them requires a schema-version
decision so agent and website clients cannot silently diverge.

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
model declarations, declarative constraint references, evidence-to-test references,
agent-case output fields, and a component’s subject-hash binding. Every `Output`
must extend `defined_quant.types.ComponentOutput`, which supplies the shared provenance,
interpretation, and visualization envelope. The checker also guarantees that a catalog-wide
adapter can invoke every component uniformly: every `Inputs` field must be accepted by the
component callable as a keyword (or through `**kwargs`), with no positional-only parameters or
hidden required arguments. Legacy split contracts and empty optional files are rejected.

A component that requires one reproducible primary view may add a `DashboardSpec` field to its
own `Output`. Build the dashboard from component-calculated values and declared
`VisualizationSpec` IDs. Add a `ViewBundleSpec` when the component should declare responsive HTML
as its default chat representation and SVG as its portable fallback. The generic adapter performs
format selection and renders the primary artifact before supporting charts; do not add arbitrary
HTML, component-supplied JavaScript, component-specific host dispatch, or a sixth component file.

Once all referenced evidence tests pass, bind the evidence to the exact current
behaviour:

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
limitations, trust data, and canonical Pydantic input/output JSON Schemas. The top-level
`agent_protocol` publishes the generic request, success, failure, and manifest schemas. Sorted
facet arrays cover categories, groups, tags, intents, input concepts, output concepts,
lifecycles, and profiles. The private website can pin and consume that file as static data.

The exporter reports `exact_sha_attestation: none`. A future CI attestation step
must derive and replace that value only after proving that the checks ran for the
exact release commit; authors cannot claim it through a command-line flag.

For local development, omit the release arguments:

```bash
uv run python authoring/export_catalog.py
```

The exporter uses the current Git commit and labels the result as a development
catalog.

## Schemas

- `schemas/contract.schema.json` validates the merged component contract, including
  nested `guidance` and `display`.
- `schemas/evidence.schema.json` validates numerical records and `agent_cases`.

The Pydantic models in `component.py` remain the source of truth for input/output
types, units, and answer-changing defaults. Neither YAML schema duplicates them.
