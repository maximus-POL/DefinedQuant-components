# Defined Quant components

Defined Quant is a public catalog of deterministic, inspectable financial calculations for
humans and agents. Each component keeps its code, contract, evidence, tests, and explanation
together in one small folder.

> **Experimental Technical Preview**
>
> Components are reference implementations, not certified models, production prices, or
> investment advice. Engineering checks, numerical evidence, provenance, and domain review are
> reported separately.

Every component currently published here has lifecycle `draft`. Its numerical evidence is
author-asserted, not independently reviewed, and domain review is `none`. Those dimensions remain
visible separately; passing repository checks does not upgrade a component's financial maturity.

## Find a component

Start in [`categories/`](categories/). Categories are ordinary folders with a `README.md` that
explains their scope. The first working component is:

[`categories/market_data/simple_return/`](categories/market_data/simple_return/)

```text
categories/
└── market_data/
    ├── README.md
    └── simple_return/
        ├── README.md
        ├── component.py
        ├── contract.yaml
        ├── evidence.yaml
        └── test_component.py
```

There is no generated folder maze and no profile-specific template tree. A component always has
the same five visible files.

## Repository map

```text
components/
├── categories/             browseable financial topics and components
├── shared/                 reusable types, validation, catalog loading, and charts
├── protocol/               closed typed records installed as defined_quant_protocol
├── authoring/              one template, two schemas, and explicit Python tools
├── .agents/                optional repository-wide agent integration
├── .github/                contribution and CI configuration
├── pyproject.toml
└── README.md
```

`categories` and `shared` are source folders. Packaging projects them into the installed Python
namespace `defined_quant`. The same wheel installs the separately versioned
`defined_quant_protocol` namespace from `protocol/`. This keeps the typed transport boundary
independent from component implementations without introducing a second distribution yet.

`.agents/` is integration metadata for agent hosts such as Codex. It is not a financial category
and it does not implement a calculation. Its single catalog-wide skill discovers, inspects, and
runs any component through the same canonical contracts used by Python callers. Component-specific
formulas and guidance remain beside the component under `categories/`; the integration layer must
not hard-code one component.

## Run the working example

From this folder:

```bash
uv sync
uv run python - <<'PY'
from defined_quant.market_data.simple_return.component import simple_return

result = simple_return(
    prices=[100, 103, 101, 105],
    price_kind="adjusted",
    timestamps=[
        "2026-07-20T00:00:00Z",
        "2026-07-21T00:00:00Z",
        "2026-07-22T00:00:00Z",
        "2026-07-23T00:00:00Z",
    ],
)
print(result.returns)
PY
```

The result is a typed object, not a bare number. It includes units, assumptions, constant
disclosures, state-dependent warnings, datapoint derivations, provenance, and a renderer-neutral
visualization specification. The shared chart renderer can turn that specification into
deterministic SVG without adding a plotting-library dependency.

## Typed generic operations

The repository-wide adapter can execute any conforming component through a closed
`defined_quant_protocol.OperationRequest`. The request binds the exact component ID, version, and
`subject_hash`; the selected component's Pydantic `Inputs` and `Output` models still perform the
financial validation. Runtime locations such as the catalog root and output directory are host
settings, not serialized request fields. A successful manifest names only relative output members
and binds their hashes. The runner refuses a pre-existing output directory, stages every member,
and publishes the complete new directory in one rename.

This direct operation path remains deliberately labeled **unmanaged**. The trusted local runner
enforces typed component execution and records the exact request, result members, identities, and
hashes. A
manifest is an internally reconciled record, not a signed or independent execution attestation.
It does not prove that caller-supplied data is true, authorize an analysis, create an approved
`AnalysisPlan`, or produce a portable `ResearchBundle`.

Protocol 0.2.0 separately introduces an atomic managed-authorization foundation. Its packaged
`simple_return_csv_v1` policy allowlists only `dq.market_data.simple_return` version `0.3.1` at
subject `b16826e2babe46b8c483d352be92ee07be7463d1658c11a096108b0ba835470a`, with explicit opt-in to
its draft lifecycle. One immutable, one-step plan can be validated into a deterministic receipt,
manually approved, and revalidated against exact plan, policy, component, dataset, receipt, and
approval hashes. The approval model has no automatic or policy-approval mode.

Resolution timestamps are operational audit metadata and do not change a plan's semantic hash;
resolved answers do. Dataset timestamps are semantic input and are hash-bound. Because frozen
Pydantic models are shallow, callers must treat nested JSON as immutable and revalidate after any
nested mutation. This foundation performs no calculation and makes no source-verification claim.
Simple Return now emits one machine-validated derivation per result datapoint, with nullable
citation join keys; authentic source bindings and citations remain deferred, as do semantic port
metadata, multi-step composition, source-bound execution, and portable research bundles.

See [the catalog-wide host skill](.agents/skills/use-defined-quant/SKILL.md) for discovery,
inspection, request construction, execution, and result-handling instructions.

## Check the catalog

The project intentionally exposes plain commands rather than a fictional `dq` CLI:

```bash
uv run pytest
uv run python authoring/check_component.py
uv run python authoring/search_catalog.py "calculate returns from prices"
uv run python authoring/export_catalog.py
uv run ruff check protocol shared categories authoring \
  .agents/skills/use-defined-quant/scripts .agents/skills/use-defined-quant/tests
uv run --no-editable mypy shared categories authoring/*.py
uv run --no-editable mypy -p defined_quant_protocol
```

Normal development uses an editable install. The type-check command asks `uv` to check the same
merged package layout users receive in the wheel, because static type checkers do not execute the
small runtime path extension used by the readable two-source layout.

Runtime discovery is filesystem-backed. Stable component-ID subject hashes are memoized per
catalog root for ordinary calculations, while the runner and managed validator explicitly
invalidate and freshly verify the installed subject at their trust boundaries. Zipimport and
single-file frozen packaging are not currently supported because contracts must remain readable to
both discovery and preflight.

To add a component:

```bash
uv run python authoring/create_category.py --id performance --title "Performance"
uv run python authoring/create_component.py \
  --category performance \
  --group risk_adjusted_performance \
  --slug sortino_ratio \
  --profile statistic
```

The generator copies the one canonical folder in
[`authoring/component-template/`](authoring/component-template/). See
[`authoring/README.md`](authoring/README.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Website boundary

The website is a separate private project. Components CI exports deterministic static catalog
JSON; the website consumes a pinned copy of that artifact and never imports or executes component
Python. Adding a component therefore requires no website code change.

## Trust model

- Pydantic `Inputs` and `Output` in `component.py` are canonical for types, units, and defaults.
- `contract.yaml` contains identity, scope, declarative guidance, and display metadata, but never
  duplicates the Pydantic interface.
- Required discovery aliases and stable intent/input/output concepts make the same catalog
  searchable by humans, developer tools, websites, and autonomous agents without loading every
  calculation.
- `evidence.yaml` supplies closed, executable fixtures and assertions and binds their passing run
  to the exact behavior hash.
- Every current component remains lifecycle `draft`, with author-asserted evidence and no
  independent domain review.
- Constraints use a closed operator vocabulary; no contract content is evaluated as Python.
- Anything that changes the answer must be supplied or declared explicitly.
- A typed operation binds what was calculated. Caller provenance records what the caller asserts;
  it is not source authentication, authorization, or a managed verification claim.

See [`VALIDATION.md`](VALIDATION.md) for the exact meaning of each trust claim.

## Licence

Code is Apache-2.0. Component explanations, documentation, and visuals are CC BY 4.0. The Defined
Quant and Defined Flow names and visual identity are not granted by those licences; see
[`TRADEMARKS.md`](TRADEMARKS.md).
