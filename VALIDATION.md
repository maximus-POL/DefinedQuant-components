# Validation

Defined Quant records evidence; it does not certify universal correctness.

> **Experimental Technical Preview**
>
> A passing component is not a production price, investment advice, or proof that the component
> suits a particular instrument, market, data source, or regime.

## Independent trust dimensions

The catalog never collapses these into one badge:

| Dimension | Source |
|---|---|
| Lifecycle | Authored `lifecycle` in `contract.yaml` |
| Engineering | Exact-commit CI result |
| Numerical evidence | Named records and executed tests in `evidence.yaml` |
| Provenance | Authorship, sources, version, commit, and hashes |
| Domain review | A separate protected review bound to exact content, or `none` |

`CI passed` means the declared checks passed on one exact commit. It does not mean external
certification or correctness outside declared scope.

## Subject binding

`subject_hash` identifies behavior and enforceable contract. Its canonical manifest includes:

1. behavior-defining Python in the component folder, excluding tests;
2. identity, version, callable, routing metadata (including discovery fields), assumptions,
   limitations, dependencies, and nested guidance from `contract.yaml`;
3. generated Pydantic input and output schemas;
4. public exports;
5. the shared types, validation logic, and renderer used by the component;
6. recursively bound component dependencies and declared external dependency specifications.

The displayed formula, intent, and output interpretation are subject-bound because they make
semantic claims. Layout-only display hints, `README.md`, tests, evidence content, and generated
artifacts are excluded. Changing behavior, enforceable scope, or published semantic
interpretation therefore changes the hash and invalidates old evidence.

## Evidence binding

`evidence.yaml` records the `validated_subject_hash` and named evidence:

- known answers;
- invariants and property checks;
- boundary cases;
- independent cross-checks;
- agent cases for correct invocation, questions, warnings, and refusal.

Every evidence record names a real test function. Evidence content is bound separately from the
subject so changing a citation, tolerance, case, or referenced test cannot silently inherit a
previous claim.

## Exact-commit execution

The checker validates structure and schemas, imports component code only in an isolated
development/CI context, verifies evidence references, runs tests before blessing, and recomputes
bindings.

```bash
uv run python authoring/check_component.py
uv run python authoring/check_component.py \
  --bless categories/market_data/simple_return
```

Release CI then records the exact commit, environment, subject hash, evidence hash, and executed
test IDs. A website may display `Engineering: passed` only when its pinned catalog artifact
contains a matching exact-commit attestation; otherwise it displays `unverified`.

## Your data is separate

Component evidence says nothing about the data supplied by a caller. Survivorship bias,
point-in-time correctness, stale observations, corporate-action policy, and vendor errors can
produce a confident wrong answer from a correct formula. Components disclose what they can check
and preserve warnings for what they cannot.

## Corrections

Confirmed defects are fixed publicly with a regression test and renewed bindings. Evidence is
not quietly carried across behavior changes. Domain review may be withdrawn or qualified without
rewriting the component’s engineering history.
