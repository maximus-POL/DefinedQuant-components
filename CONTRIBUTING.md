# Contributing to Defined Quant

Defined Quant grows one inspectable component at a time. External component work is
proposal-first so scope and conventions are agreed before implementation.

## What you edit

Components live exactly here:

```text
categories/<category>/<component>/
├── README.md
├── component.py
├── contract.yaml
├── evidence.yaml
└── test_component.py
```

The files have deliberately plain names:

| File | Purpose |
|---|---|
| `README.md` | Human explanation, formula, worked example, limitations |
| `component.py` | Pydantic `Inputs`/`Output` and deterministic calculation |
| `contract.yaml` | Identity, discovery, scope, guidance, constraints, and display hints |
| `evidence.yaml` | Bound known answers, invariants, boundaries, and agent cases |
| `test_component.py` | Executable checks named by the evidence |

Do not edit the private website when adding a component. Catalog export makes it discoverable.

## Create and check

From the `components/` folder:

```bash
uv sync

uv run python authoring/create_category.py \
  --id performance \
  --title "Performance"

uv run python authoring/create_component.py \
  --category performance \
  --group risk_adjusted_performance \
  --slug sortino_ratio \
  --profile statistic

uv run pytest categories/performance/sortino_ratio
uv run python authoring/check_component.py categories/performance/sortino_ratio
uv run python authoring/check_component.py \
  --bless categories/performance/sortino_ratio
```

The component generator copies the single canonical folder in `authoring/component-template/`.
It does not select a hidden profile matrix.

## Contract rules

1. Pydantic models in `component.py` define input/output types, units, and defaults.
   `contract.yaml` must not restate them.
2. A convention that changes the answer must be a required input or a visible declared default.
3. Constraints are closed declarative data. A blocking rule means no meaningful answer exists;
   a warning means the answer is valid but uncertain.
4. `README.md` explains conventions but never becomes their canonical source.
5. Test data is synthetic and seeded. Do not commit licensed vendor or scraped data.
6. Do not add runtime dependencies without an explicit maintainer decision.
7. Discovery aliases describe names users actually use. Intents, input concepts, and output
   concepts are stable lower-snake-case routing identifiers; keep them specific and bounded.
8. Do not commit generated catalogs, rendered outputs, binary artifacts, secrets, or empty
   optional files.

## Evidence

Every evidence record has a stable ID, a corresponding test function, provenance, and an explicit
tolerance policy. Blessing records the current `subject_hash` only after the named tests pass.
Engineering checks, numerical evidence, provenance, and domain review remain separate claims; an
author cannot self-award independent domain review.

See [`VALIDATION.md`](VALIDATION.md).

## Pull requests

Open a proposal issue before implementing a new component. Keep each component change focused,
run the checker and tests, and open a pull request rather than pushing to `main`.

Shared types, validation, catalog loading, schemas, authoring tools, category creation, CI policy,
and domain-review records are maintainer-controlled during the preview.

By contributing, you agree that code is licensed under Apache-2.0 and authored explanations under
CC BY 4.0. Do not contribute material you cannot license.
