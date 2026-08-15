# Components architecture

This document is the implementation specification for the simplified public components project.

## 1. Project boundary

This public repository owns deterministic component implementations, their contracts and
evidence, shared calculation infrastructure, the typed operation protocol, and static catalog
generation. It does not contain website source, provider credentials, vendor data, or a private
application runtime.

External applications consume a pinned static catalog artifact or install the public wheel. A
website may display exported metadata and schemas, but it must not import or execute component
Python. Data acquisition and source authentication remain separate host responsibilities.

## 2. Author-facing structure

```text
components/
├── categories/
│   ├── README.md
│   └── <category>/
│       ├── README.md
│       └── <component>/
│           ├── README.md
│           ├── component.py
│           ├── contract.yaml
│           ├── evidence.yaml
│           └── test_component.py
├── shared/
│   ├── README.md
│   ├── types/
│   ├── validation.py
│   ├── catalog.py
│   ├── charts.py
│   ├── managed_profiles/
│   ├── plan_validation.py
│   └── agent.py                 compatibility imports only
├── protocol/
│   ├── __init__.py
│   ├── README.md
│   ├── authorization.py
│   ├── canonical.py
│   ├── operation.py
│   ├── plan.py
│   ├── ports.py
│   ├── policy.py
│   ├── version.py
│   └── py.typed
├── authoring/
│   ├── README.md
│   ├── component-template/
│   ├── schemas/
│   ├── create_category.py
│   ├── create_component.py
│   ├── check_component.py
│   └── export_catalog.py
├── .agents/
│   └── skills/
│       └── use-defined-quant/
└── pyproject.toml
```

Only real categories exist. Placeholder category folders are not created.

## 3. Installed Python namespaces

The browseable source folders are not the import names:

- `shared/` is installed as the root package `defined_quant`.
- category folders are installed beneath the same namespace.
- a component callable therefore uses a stable path such as
  `defined_quant.market_data.simple_return.component:simple_return`.
- `protocol/` is installed as the separate namespace `defined_quant_protocol` in the same wheel.

This keeps the public Python name while removing the `src/defined_quant/` browsing indirection.
The protocol has its own SemVer because transport compatibility is distinct from component
catalog releases. It initially ships in the `defined-quant` distribution so the project has one
release train. Packaging tests must verify both namespaces in editable installation and wheel
contents.

`defined_quant_protocol` depends only on Pydantic and the Python standard library. It never
imports components, discovery, renderers, or application code. Runtime adapters under
`defined_quant` or `.agents/` may depend on both namespaces.

`defined_quant.agent` is a compatibility import surface for operation types that previously lived
under the component namespace. It re-exports the canonical protocol models and contains no second
model definitions. New integrations should import `defined_quant_protocol` directly.

## 4. Component contract

Each component has one canonical implementation file and two machine-readable documents.

`component.py`

- Defines Pydantic `Inputs` and `Output`.
- Defines the deterministic callable.
- Is canonical for types, units, conventions, and defaults.
- Declares closed `x-defined-quant-port` metadata on composable fields; the authoring checker
  rejects partial, invented, wrongly directed, or sibling ad-hoc port metadata.
- Separates assumptions, permanent disclosures, transformations, and state-dependent warnings in
  the shared output envelope; input-independent comparisons in contract warning rules are
  rejected.
- May return typed, renderer-neutral visualization specifications.

`contract.yaml`

- Identity, version, category, callable, lifecycle, authorship, assumptions, limitations, and
  dependency declarations.
- Required bounded `discovery` metadata: natural-language aliases plus stable intent, input
  concept, and output concept identifiers. These fields route both humans and agents.
- Nested `guidance`: use/do-not-use cases, unsupported scope, required questions, allowed
  defaults, declarative constraints, and interpretation.
- Nested `display`: subject-bound formula, intent, and output copy plus non-semantic layout hints.
- Never repeats input/output schemas and never stores derived hashes or status.

`evidence.yaml`

- The subject hash that the evidence applies to.
- Known answers, boundary cases, and cross-checks are single-invocation fixtures collected and
  executed directly by pytest through a closed assertion vocabulary.
- Invariants name hand-written property tests; agent cases execute structured requests through the
  real adapter and assert compute, clarification, or refusal behavior.
- Does not grant domain review.

`README.md` is the human trust surface. It explains the implementation but cannot redefine it.

## 5. Trust bindings

`subject_hash` covers behavior and enforceable contract:

- behavior-defining component Python;
- the behavior projection of `contract.yaml`, including nested guidance and the semantic formula,
  intent, and output copy while excluding layout-only hints;
- generated Pydantic input/output schemas;
- public exports;
- the shared validation/types code used by the component;
- declared component and external dependencies.
- all contract fields used by canonical agent routing, including discovery metadata.

Evidence content binds separately to the subject and to the generated or named tests that consumed
it. Exact-commit CI records what actually ran. Domain review, when added, binds the exact reviewed
subject and explanatory content. None of these dimensions is collapsed into a single trust badge.

## 6. Visualization boundary

Components return declarative `VisualizationSpec` data. `shared/charts.py` renders trusted SVG from
that closed model. A component never returns arbitrary HTML, JavaScript, remote resources, or a
third-party figure object.

Transformations that change the analytical answer belong in typed component inputs and outputs,
not in the renderer. Renderer choices such as output path are host concerns.

## 7. Authoring

There is one literal `authoring/component-template/` with the same five filenames contributors
will edit. Clear placeholders appear inside those files; `.tmpl` suffixes, profile template trees,
and extension overlays do not exist.

Profile is contract data that activates validation requirements. It does not select another
folder template.

The project currently exposes explicit Python scripts, not a `dq` executable. A single CLI may be
added later only if it reduces real friction.

## 8. Agent-host integration

`.agents/` contains optional repository-level metadata and adapters for agent hosts. It belongs
at the project root because hosts discover skills for the repository as a whole; it is not part
of the financial category hierarchy.

The integration exposes one catalog-wide workflow. It discovers components from
`shared/catalog.py` and ranks their contracts through `shared/discovery.py`, obtains the selected
component's canonical Pydantic interface, invokes the declared callable, and renders typed
visualization specifications through `shared/charts.py`. Discovery never imports calculation
code. It returns stable scores, positive field matches, and separate boundary matches from
do-not-use and unsupported-scope fields. A boundary-only match never recommends a component.
The integration must not describe or special-case an individual component in production code. A
component's financial instructions remain in its own `contract.yaml`.

`ContractIndex` takes one deep-immutable metadata snapshot, orders it by stable component ID, and
precomputes normalized positive-term and facet postings without importing a component callable.
It retains immutable authored field values so exact phrase scoring and boundary explanations are
computed only for shortlisted records rather than stored as hundreds of thousands of tiny sets.
`DefinedQuantService` owns exactly one such index for its lifetime; repeated searches and stable-ID
discovery lookups never reopen the catalog. Constructing a new service is the explicit refresh
boundary. The compatibility `search_components()` function builds the same index for an explicit
in-memory catalog or one standalone catalog query. Ranking weights, explanations, exact filters,
complete pre-limit facets, and `(-score, component_id)` ordering remain the existing public
discovery semantics.

The adapter is a host convenience layer, not another source of truth. It cannot redefine inputs,
defaults, constraints, outputs, or presentation semantics. New components become available to
agent hosts through catalog discovery without adding another skill or editing the generic one.

`defined_quant_protocol` 0.4.0 provides the closed operation, semantic-port, and
managed-authorization records. An
`OperationRequest` binds an exact component ID, version, and `subject_hash` to a candidate input
object, explicit caller provenance, and a bounded artifact request. Catalog roots and output
directories are trusted runtime settings and never fields in the semantic request. The request
schema contains no host filesystem path. The C2 runner requires a new output directory and
publishes a fully staged directory with an atomic no-replace rename; mutable overwrite semantics
are deliberately unsupported. Linux, macOS, and Windows use their native no-clobber behavior, and
unsupported hosts fail rather than falling back to a replace operation.

`defined_quant.operation_runtime` performs the following catalog-wide sequence for the thin CLI,
numerical evidence execution, and `DefinedQuantService`:

1. Validate the closed operation request.
2. Discover the component by stable ID, freshly recompute and cache its filesystem-backed
   `subject_hash`, and refuse an exact version or subject mismatch.
3. Validate input through that component's canonical Pydantic `Inputs` model.
4. Invoke only the callable declared by the component catalog.
5. Validate the return value through the canonical Pydantic `Output` model and verify its
   component identity and subject provenance.
6. Materialize normalized input, typed result, and requested SVG artifacts in a sibling staging
   directory.
7. Reconcile the exact staged members, canonical manifest bytes, and every declared digest.
8. Publish once with atomic no-replace rename and emit the existing typed result.

The CLI retains only strict JSON and argument handling, legacy request construction, protocol
response serialization, and exit status. Numerical evidence uses the lower raw-mapping execution
seam because its deliberate non-finite fixtures are not valid `OperationRequest` JSON; it does not
maintain a second component invocation path. `DefinedQuantService` exposes Phase-1 canonical
unmanaged execution, Phase-2 indexed discovery, and Phase-3 session-scoped data and operation
records. The discovery snapshot is never an execution trust source: operation execution still
resolves and verifies the selected filesystem subject freshly.

Phase 3 normalizes only the frozen structural JSON/CSV cases, records every host-applied change,
and publishes `DatasetPayloadV1`, `DatasetRecordV1`, and manifest-reconciled `OperationRecordV1`
through owner-private, content-addressed session storage. Every read revalidates schema, hashes,
members, cross-record dimensions, and source bindings before returning a bounded page. References
expire with the session; corruption is quarantined and never repaired or partially returned.
The store retains no accepted raw source bytes or caller file path. Its hashes establish immutable
byte consistency, not source authenticity, financial correctness, or independent execution
attestation. The alpha storage and local-file boundary is enabled only on tested POSIX hosts and
fails startup closed elsewhere until equivalent ACL, reparse-point, handle-containment, locking,
and cleanup behavior is implemented and tested. Worker isolation and transport methods remain
Phase 4.

No production branch selects behavior by component ID. A component can serve as a test fixture,
but adding another conforming component requires no runner edit. Pydantic models remain canonical
for field structure, and their closed semantic-port metadata establishes field-level meaning.
Composition compares direction, concept, unit, shape, cardinality, convention, ordering,
frequency, and provenance requirements before a producer output can feed a consumer input. An
empty or populated `depends_on` list in `contract.yaml` binds implementation dependencies; it is
not a substitute for port compatibility.
Ordinary stable-ID subject lookups reuse the freshly verified cache entry; path and explicit-record
lookups remain uncached for authoring. The current catalog and preflight require filesystem-backed
contracts, so zipimport and single-file frozen layouts are outside the supported runtime boundary.

The operation result is intentionally **unmanaged**. The trusted local runner enforces the sequence
above; its manifest records an internally reconciled assertion about the exact request, component,
input, and materialized bytes. Because anyone can construct an unsigned Pydantic-valid manifest,
the record is not independent execution attestation. It also does not establish that the caller's
data is authentic, that an interpretation is correct, that a person approved an analysis plan, or
that a portable research bundle passed independent verification. Caller provenance is a recorded
assertion, not a provider or Defined Quant attestation.

The separate C3B surface is atomic managed authorization, not managed execution. An immutable
`AnalysisPlan` contains exactly one step. A data-driven packaged policy currently allowlists only
`dq.market_data.simple_return` version `0.3.4`, subject
`ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a`, with explicit opt-in to its
draft lifecycle and non-empty timestamps. The catalog-aware validator checks the exact installed
subject, the component's required questions and Pydantic input model, declarative constraints, and
the outer policy requirements without calling the calculation. A successful evaluation emits a
deterministic `ValidationReceipt`; approval is manual-only and binds that receipt and exact plan.

Before any future managed runner may act, authorization is reproduced against the plan, policy,
component, dataset, receipt, and approval roots. Any semantic change requires a new validation and
manual approval. `ResolvedQuestion.resolved_at` is operational audit metadata and is excluded from
the semantic plan hash, while the answer and other resolution meaning remain bound. Dataset
timestamps are semantic data and are included in both the dataset and plan hashes. Frozen Pydantic
records provide shallow immutability only, so mutation of nested JSON requires revalidation.

This boundary neither executes Simple Return nor authenticates the dataset source. Simple Return's
output binds every return index to its two source-price indices through a closed derivation record,
but its nullable citation IDs make no source claim. Log Return and Historical Volatility now form
the first machine-checkable semantic chain: both the return series and the log-return convention
ports match, while the simple-return convention does not. This compatibility neither executes nor
authorizes a multi-step plan. Exact source citations, execution attestation, evaluation records,
managed composition, `ResearchBundle` generation, and source-bound execution remain deferred.

## 9. Static publication

`authoring/export_catalog.py` runs in the public components project after validation. It emits a
deterministic, website-safe catalog artifact containing sanitized component metadata and trust
bindings. Catalog schema v2 exports component groups, tags, discovery fields, use/do-not-use
boundaries, limitations, deterministic top-level facet values, and each component's Pydantic
input/output JSON Schemas, including their closed semantic-port extensions. It also exports
request, manifest, success, failure, and result schemas
plus canonicalization, hash framing, domain, and a fixed verification vector directly from
`defined_quant_protocol`, labeled as unmanaged.

The artifact contains a source commit identity but no generation timestamp or local filesystem
path. Components and object keys are deterministically ordered. Source export reports numerical
evidence as author-asserted, exact-commit attestation as none, and domain review as none; a later
attestation system must prove stronger claims rather than infer them from a successful export.

The private website:

- pins an immutable catalog release and checksum;
- consumes JSON/static assets only;
- never imports or executes component Python;
- contains no manually maintained component list.

Adding a component therefore changes the public project and its generated catalog, not website
source code.

## 10. Current claim boundary

All seven current components have lifecycle `draft`, author-asserted evidence, and no independent
domain review. The typed operation protocol improves reproducibility and interface verification
without changing those facts.

The optional local MCP alpha transport is not yet implemented. Its frozen transport, host,
reference, and security design is recorded in
[`docs/LOCAL_MCP_ALPHA_DESIGN.md`](docs/LOCAL_MCP_ALPHA_DESIGN.md).
It gives the existing operation record, manifest, and declared members the calculation-receipt
role without adding a second record model; portable reproduction remains the deferred
`ResearchBundle` capability.

**Supported MCP alpha platforms:** macOS and Linux. Windows support requires a separate future
security and operations boundary and is not part of the alpha.

The current release defines an atomic `AnalysisPlan`, deterministic validation receipt,
manual-only approval record, and revalidated authorization binding for one exact Simple Return
subject. These records authorize a future calculation but do not perform one. The bound dataset is
explicitly `unverified`. Datapoint derivations expose calculation lineage, but no source-bound
dataset, populated citation set, deterministic evaluation record, managed execution result, or
`ResearchBundle` exists yet. Direct Python calls and generic operation requests remain unmanaged
and cannot receive a managed or independently verified label.
