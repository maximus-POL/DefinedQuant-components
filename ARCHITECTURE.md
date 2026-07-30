# Components architecture

This document is the implementation specification for the simplified public components project.

## 1. Project boundary

The local workspace has two sibling projects:

```text
Defined Quant/
├── components/       public component library and catalog producer
└── website/          private site consuming static catalog data
```

GitHub visibility applies to a repository, not a folder. The final publication step therefore
gives each child its own Git history. During migration, the existing parent Git repository remains
only to preserve reviewable history; nested repositories are not initialized.

The existing mixed repository must not be made public because its history includes website code.

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
│   └── charts.py
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

## 3. Installed Python namespace

The browseable source folders are not the import names:

- `shared/` is installed as the root package `defined_quant`.
- category folders are installed beneath the same namespace.
- a component callable therefore uses a stable path such as
  `defined_quant.market_data.simple_return.component:simple_return`.

This keeps the public Python name while removing the `src/defined_quant/` browsing indirection.
Packaging tests must verify both editable installation and wheel contents.

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
that closed model. A component may also add a typed `DashboardSpec` field to its own `Output` when
one primary composition must be identical across hosts. That specification binds chart order,
primary-versus-secondary layout, table rows and columns, numeric formats, captions, and notes. The
component may pair it with a closed `ViewBundleSpec` that declares responsive HTML as the default
chat view and SVG as the portable fallback. The generic adapter applies deterministic
capability-and-use-case selection, renders each declared format, and marks exactly one as primary
before the supporting charts.

A component never returns arbitrary HTML, JavaScript, remote resources, or a third-party figure
object. Trusted shared renderers own the fixed HTML, CSS, and permitted interaction behavior; all
component text and values are escaped into that template. Hosts and agents must not replace a
declared dashboard with a separately generated presentation.

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
visualization and optional dashboard specifications through `shared/charts.py`. Discovery never
imports calculation code. It returns stable scores, positive field matches, and separate boundary
matches from do-not-use and unsupported-scope fields. A boundary-only match never recommends a
component.
The integration must not describe or special-case an individual component in production code. A
component's financial instructions remain in its own `contract.yaml`.

The adapter is a host convenience layer, not another source of truth. It cannot redefine inputs,
defaults, constraints, outputs, or presentation semantics. New components become available to
agent hosts through catalog discovery without adding another skill or editing the generic one.

`shared/agent.py` defines the closed catalog-wide operation protocol. An `OperationRequest`
contains a selected component ID, its candidate input object, explicit interpretation provenance,
host view capabilities, and host-local output settings. The adapter validates the request and the
selected component's Pydantic `Inputs`, then emits a typed `OperationSuccess` or
`OperationFailure`. Its `OperationManifest` binds the semantic request independently of local
paths, the normalized input, validated result, component subject, selected view, renderers, and
materialized files.

Natural-language understanding remains a host responsibility. When a caller authorizes AI
interpretation, the host may map prompt or attachment data into the canonical input, but it must
record that mapping as unverified provenance and list inferred conventions. A future provider
adapter replaces only that ingestion step; component behavior and presentation do not change.

## 9. Static publication

`authoring/export_catalog.py` runs in the public components project after validation. It emits a
deterministic, website-safe catalog artifact containing sanitized component metadata and trust
bindings. Catalog schema v2 also exports each component's canonical Pydantic input/output JSON
Schemas and the generic operation request, success, failure, and manifest schemas, alongside
component groups, tags, discovery fields, use/do-not-use boundaries, limitations, and
deterministic top-level facet values.

The private website:

- pins an immutable catalog release and checksum;
- consumes JSON/static assets only;
- publishes a well-known agent manifest and per-component schema-bearing records;
- never imports or executes component Python;
- contains no manually maintained component list.

Adding a component therefore changes the public project and its generated catalog, not website
source code.
