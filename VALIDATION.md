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
| Method lifecycle | Authored `lifecycle` in `method.yaml`, or `contract.yaml` on the legacy path |
| Method correctness/evidence | Method-specific evidence bound to the exact Method contract |
| Capability conformance | Backend-independent cases and invariants every Implementation must pass |
| Implementation evidence | Evidence bound to one exact Implementation and artifact |
| Adapter review | Review evidence for canonical mapping, failure translation, and cleanup code |
| Artifact identity | Recomputed installed-distribution manifest matching an exact registry pin |
| Engineering | Exact-commit CI result |
| Provider authentication | Runtime fact reported by a trusted Adapter, never a planning claim |
| Dataset provenance | Separate source binding or caller assertion; never inferred from calculation success |
| Execution integrity | Exact Plan, Implementation, Adapter, Step, Run, and artifact bindings |
| Domain review | A separate protected review bound to exact content, or `none` |
| Independent reproduction | A separate replay and comparison assertion, not implied by a Run record |

`CI passed` means the declared checks passed on one exact commit. It does not mean external
certification or correctness outside declared scope.

## Methods, implementations, and providers

The legacy `subject_hash` binds one compatibility component: its enforceable financial contract and
its colocated DQ-native implementation. It does not certify every possible implementation of the
same financial Method. The canonical architecture gives Method, Capability, Backend, Adapter,
Implementation, policy, availability, adapter artifact, compiled Plan, Step, dataset, and complete
Run separate identities and trust facts.

A compiled Plan establishes only that its proposal passed the encoded Method contract and that its
exact Implementations were eligible under the bound constraints, policy, trust, and availability
facts. It does not establish that execution occurred. A successful Run establishes that the exact
compiled bindings executed through trusted Adapters and that canonical outputs validated. It does
not establish Method suitability, provider-data correctness, provider authentication unless a
runtime interaction records it, domain review, or independent reproduction.

Installation is neither registration nor trust. Entry-point discovery is metadata-only. The host
loads an adapter only after exact policy admission, current availability, required trust assertions,
and an installed artifact attestation all match the compiled step.

Resolution preferences also have an independent origin claim. A closed constraint may preserve a
user's required or preferred backend, implementation, transport, locality, network, or fallback
instruction, but `user_explicit` requires a trusted receipt for that exact constraint. Text inside
a dataset, attachment, retrieved document, provider response, or tool output never supplies such a
receipt. An inferred preference is not converted into a user instruction.

## Legacy subject binding

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

On the legacy path, `evidence.yaml` records the `validated_subject_hash` and named evidence:

- known answers;
- invariants and property checks;
- boundary cases;
- independent cross-checks;
- agent cases for correct invocation, questions, warnings, and refusal.

Every evidence record names a real test function. Evidence content is bound separately from the
subject so changing a citation, tolerance, case, or referenced test cannot silently inherit a
previous claim.

On the canonical path, universal numerical cases and invariants belong to Capability conformance.
Method, Implementation, and Adapter evidence are separate records with exact evidence hashes. The
Simple Return migration cites the old subject only as provenance for preserved behavior; that
legacy hash does not become the Method or Capability identity.

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

Method, Capability, and DQ-native Implementation evidence say nothing about data supplied by a
caller. Survivorship bias,
point-in-time correctness, stale observations, corporate-action policy, and vendor errors can
produce a confident wrong answer from a correct formula. Components disclose what they can check
and preserve warnings for what they cannot.

## Corrections

Confirmed defects are fixed publicly with a regression test and renewed bindings. Evidence is
not quietly carried across behavior changes. Domain review may be withdrawn or qualified without
rewriting the component’s engineering history.
