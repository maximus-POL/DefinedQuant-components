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
│   └── agent.py                 compatibility imports only
├── protocol/
│   ├── __init__.py
│   ├── README.md
│   ├── canonical.py
│   ├── operation.py
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
- Known answers, invariants, boundary cases, and cross-checks, each tied to an executable test.
- Agent-use cases used to test correct invocation/refusal behavior.
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

Evidence content binds separately to the subject and named tests. Exact-commit CI records what
actually ran. Domain review, when added, binds the exact reviewed subject and explanatory content.
None of these dimensions is collapsed into a single trust badge.

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

The adapter is a host convenience layer, not another source of truth. It cannot redefine inputs,
defaults, constraints, outputs, or presentation semantics. New components become available to
agent hosts through catalog discovery without adding another skill or editing the generic one.

`defined_quant_protocol` provides the closed operation records. An `OperationRequest` binds an
exact component ID, version, and `subject_hash` to a candidate input object, explicit caller
provenance, and a bounded artifact request. Catalog roots and output directories are trusted
runtime settings and never fields in the semantic request. The request schema contains no host
filesystem path. The C2 runner requires a new output directory and publishes a fully staged
directory with an atomic no-replace rename; mutable overwrite semantics are deliberately
unsupported. Linux, macOS, and Windows use their native no-clobber behavior, and unsupported hosts
fail rather than falling back to a replace operation.

The generic adapter then performs the following catalog-wide sequence:

1. Validate the closed operation request.
2. Discover the component by stable ID and refuse a version or `subject_hash` mismatch.
3. Validate input through that component's canonical Pydantic `Inputs` model.
4. Invoke only the callable declared by the component catalog.
5. Validate the return value through the canonical Pydantic `Output` model and verify its
   component identity and subject provenance.
6. Materialize normalized input, typed result, and requested SVG artifacts.
7. Emit a typed result whose manifest records relative POSIX member names and content hashes.

No production branch selects behavior by component ID. A component can serve as a test fixture,
but adding another conforming component requires no runner edit. Pydantic models also remain the
canonical source for structural compatibility between possible component steps; an empty or
populated `depends_on` list in `contract.yaml` is not a substitute for model compatibility.

The operation result is intentionally **unmanaged**. The trusted local runner enforces the sequence
above; its manifest records an internally reconciled assertion about the exact request, component,
input, and materialized bytes. Because anyone can construct an unsigned Pydantic-valid manifest,
the record is not independent execution attestation. It also does not establish that the caller's
data is authentic, that an interpretation is correct, that a person approved an analysis plan, or
that a portable research bundle passed independent verification. Caller provenance is a recorded
assertion, not a provider or Defined Quant attestation.

## 9. Static publication

`authoring/export_catalog.py` runs in the public components project after validation. It emits a
deterministic, website-safe catalog artifact containing sanitized component metadata and trust
bindings. Catalog schema v2 exports component groups, tags, discovery fields, use/do-not-use
boundaries, limitations, deterministic top-level facet values, and each component's Pydantic
input/output JSON Schemas. It also exports request, manifest, success, failure, and result schemas
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

All current components have lifecycle `draft`, author-asserted evidence, and no independent domain
review. The typed operation protocol improves reproducibility and interface verification without
changing those facts.

The current release does not define `AnalysisPlan`, validation receipt, approval record,
source-bound dataset, deterministic evaluation record, or `ResearchBundle`. Those records belong
to the later managed trust path. Until that path exists, direct Python calls and generic operation
requests may produce valid calculations and manifests but cannot receive a managed or independently
verified label.
