# Defined Quant architecture

This document is the implementation specification for the public Defined Quant repository. The
methods-first architecture described here is now canonical. Simple Return is its first complete
vertical-slice template; all seven bundled Methods now use that complete path. Their old five-file
component folders and direct runtime remain a time-bounded compatibility surface only.

## 1. Architecture and project boundary

Defined Quant separates the financial method a user requests from the concrete system that performs
each atomic operation. The governed relationship is:

```text
User request
  -> Method
  -> backend-neutral Recipe
  -> Capabilities
  -> explicit resolution constraints
  -> policy, trust, and Availability
  -> exact Implementations
  -> trusted Adapters
  -> Backends
  -> canonical step results
  -> complete Run record
  -> agent explanation
```

These terms have distinct meanings:

| Term | Meaning |
|---|---|
| Method | The professional financial contract: purpose, canonical inputs and outputs, methodology, conventions, constraints, assumptions, limitations, interpretation, authored defaults, and a backend-neutral recipe. |
| Recipe | The backend-neutral dataflow needed to realize a method. A recipe names capabilities, never providers or executable code. |
| Capability | One atomic backend-neutral typed operation, such as acquisition, calculation, transformation, diagnostic, or verification. |
| Backend | A library, local runtime, data provider, analytics service, database, HTTP API, or external MCP system, with an explicit backend kind. |
| Implementation | One exact registered realization of one capability using declared backend bindings. Multiple implementations may satisfy the same capability. |
| Adapter | Trusted executable code that maps canonical inputs to one backend invocation, maps the response into canonical outputs and failures, and performs cleanup. |
| Policy | The host-owned allowlist, trust requirements, restrictions, and priority rules used to select an implementation. |
| Availability | An explicit, non-secret snapshot of whether an exact registered implementation is currently usable. |
| Resolution constraint | A bounded user- or host-supplied requirement or preference over eligible implementations. |
| Run record | An immutable binding of the method, compiled plan, exact implementations, inputs, step records, provenance, warnings, artifacts, and failure or success. |
| Component | A website presentation term and temporary compatibility name for the old bundled DQ-native format; it is not the core architecture. |

The repository currently has one canonical runtime and one compatibility lane:

| Lane | Current status | Boundary |
|---|---|---|
| Seven bundled Methods | Canonical execution complete | Seven authored Methods, nine atomic Capabilities, one DQ-native Backend, nine exact Implementations and Adapters, closed conformance and evidence, metadata-only discovery, constrained compilation, exact trusted-adapter execution, immutable plan/step/run records, and seven static website projections. |
| Legacy component compatibility | Removal scheduled | The seven five-file folders, direct imports, unmanaged workers, and explicitly named component APIs preserve useful compatibility but no longer define financial meaning or canonical execution. |
| External backend ecosystem | Deferred | No production OpenBB, LSEG, statsmodels, QuantLib, database, HTTP, or external-MCP implementation is advertised. An actual adapter distribution and evidence must exist before registration. |

The current product flow is therefore split rather than silently connected:

```text
user or agent
    |
    v
DefinedQuantService
    |
    +-- search_methods / inspect_method            metadata only
    +-- compile_plan                               pure exact resolution
    +-- execute_plan                               exact compiled binding only
    +-- get_plan / get_run / get_dataset / read_artifact
    |
    `-- explicitly named component compatibility methods during migration
```

This public repository owns authored provider-neutral registry data, the canonical protocol and
compiler, trusted execution boundaries, the DQ-native adapter distribution, immutable
records, the optional local MCP transport, static website projections, and the remaining legacy
components. It does not contain website source, provider credentials, vendor data, or production
external adapters.

External applications may consume a pinned static page artifact or install the public wheels. A
website displays registered support from exported metadata; it never imports or executes method,
implementation, or adapter code and never presents installation-specific credential or entitlement
state. The current methods-first runtime accepts caller-supplied canonical inputs. Data acquisition, source
authentication, and provider entitlement remain deferred until separately governed adapters exist.

## 2. Author-facing structure

This is a selected source map. The `shared/` and `protocol/` inventories are exhaustive because
their module boundaries are architectural; the other folders are summarized by responsibility.

```text
components/
├── registry/                    canonical, data-only methods-first registry
│   ├── taxonomy/                discovery and website navigation only
│   ├── methods/                 financial contracts and recipes
│   ├── capabilities/            atomic interfaces and conformance
│   ├── backends/                backend identities and boundaries
│   ├── adapters/                trusted adapter metadata
│   ├── implementations/         exact capability realizations
│   ├── evidence/                separately scoped evidence
│   └── artifacts/               canonical installed-distribution manifests
├── adapters/
│   └── dq_native/               independently installable bundled native adapters
├── categories/                  migration-only DQ-native compatibility components
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
│   ├── README.md                shared-runtime guide
│   ├── __init__.py              public exports and source-layout bridge
│   ├── _immutable_json.py       recursively immutable JSON containers
│   ├── _posix_local_host.py     POSIX secure-filesystem provider
│   ├── _posix_worker.py         POSIX subprocess-group provider
│   ├── _posix_worker_entry.py   POSIX pre-import resource-limit bootstrap
│   ├── _windows_local_host.py   Windows secure filesystem and session-state provider
│   ├── _windows_worker.py       Windows suspended-process Job Object provider
│   ├── _windows_worker_entry.py  Windows binary-STDIO bootstrap
│   ├── adapter_artifacts.py     installed adapter artifact verification and attestation
│   ├── adapter_catalog.py       explicitly trusted exact adapter invocation catalog
│   ├── adapter_discovery.py     metadata-only installed entry-point discovery
│   ├── agent.py                 compatibility imports only
│   ├── catalog.py               component discovery, loading, and subject binding
│   ├── charts.py                deterministic trusted SVG rendering
│   ├── constraint_evaluation.py  closed methods-first constraint evaluator
│   ├── data_records.py          immutable dataset and operation record models
│   ├── dataset_registry.py      strict normalization and configured-root ingestion
│   ├── discovery.py             import-free indexed contract search
│   ├── execution.py             exact compiled-plan execution and validation
│   ├── host_failures.py         closed host failures, outcomes, and trust labels
│   ├── local_host_platform.py   transport-neutral host facade and provider selection
│   ├── managed_profiles/        packaged managed-authorization allowlists
│   ├── method_records.py        immutable plan, step, and complete-run storage
│   ├── method_registry.py       deprecated component-to-governance projection
│   ├── method_service.py        explicit methods-first runtime session
│   ├── operation_records.py     manifest-to-operation-record reconciliation
│   ├── operation_runtime.py     canonical native validation, execution, and publication
│   ├── plan_compiler.py         deprecated compatibility-plan compiler
│   ├── plan_validation.py       managed plan validation and authorization
│   ├── planning.py              canonical preference-aware recipe compiler
│   ├── py.typed                 installed-package typing marker
│   ├── record_views.py          bounded dataset and operation projections
│   ├── run_views.py             bounded plan, step, and run projections
│   ├── schema_validation.py     closed canonical JSON Schema subset
│   ├── service.py               transport-neutral host API
│   ├── session_cas.py           owner-private session-scoped content store
│   ├── stdio_framing.py         bounded binary LF framing and exact byte I/O
│   ├── types/                   canonical financial and presentation types
│   ├── validation.py            closed declarative constraint evaluator
│   ├── worker_entry.py          strict one-request subprocess entry point
│   ├── worker_inspection.py     selected-subject inspection inside the worker
│   ├── worker_limits.py         stable worker channel byte ceilings
│   ├── worker_process.py        transport-neutral native process contract
│   ├── worker_runtime.py        shared bounded-worker lifecycle and publication
│   └── registry/                metadata-only loading, search, and inspection
├── protocol/
│   ├── _immutable_json.py       recursively immutable governance JSON
│   ├── __init__.py
│   ├── README.md
│   ├── authorization.py
│   ├── canonical.py
│   ├── evidence.py              closed evidence and conformance records
│   ├── execution.py             adapter envelopes and immutable execution records
│   ├── governance.py
│   ├── operation.py
│   ├── plan.py
│   ├── ports.py
│   ├── policy.py
│   ├── registry.py              methods-first registry identities and schemas
│   ├── resolution.py            preferences, policy, availability, and compiled plans
│   ├── version.py
│   └── py.typed
├── mcp_server/                  separate optional STDIO transport distribution
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── src/defined_quant_mcp/   SDK-isolated transport package
│   └── tests/                   official-client, wire, and packaging tests
├── authoring/                   creation, checking, bundle, export, and release tooling
│   ├── schemas/                 compatibility contract and evidence JSON Schemas
│   └── tests/                   system, portability, and authoring tests
├── docs/                        technical designs and normative protocol fixtures
├── .agents/                     optional catalog-wide agent-host adapter
├── .github/                     native CI and repository policy
└── pyproject.toml               core build, namespace mapping, and development configuration
```

All seven bundled Methods are complete canonical execution slices. Their category folders are no
longer financial sources of truth; the old runtime remains compatibility-only until its dated
removal milestone. Placeholder external-provider records are prohibited.

## 3. Installed Python namespaces

The browsable source folders are deliberately not the installed import names. Hatch maps:

- `shared/` to the root package `defined_quant`;
- category folders beneath the same `defined_quant` namespace; and
- `protocol/` to the separate namespace `defined_quant_protocol` in the same core wheel;
- authored `registry/` data plus a deterministic compiled JSON bundle into the core wheel; and
- `adapters/dq_native/src/` into the separate `defined-quant-adapter-dq-native` distribution.

A native callable therefore has a stable installed path such as
`defined_quant.market_data.simple_return.component:simple_return`, even though contributors browse
it under `categories/market_data/simple_return/`. This retains the public `defined_quant` import
name without requiring a generated or physical `src/defined_quant/` source tree. The bridge in
`shared/__init__.py` keeps both source halves live in editable installations.

The project has separate version axes:

| Axis | Current value | Meaning |
|---|---:|---|
| Core distribution | `defined-quant` 0.1.3 | Release version of the wheel containing `defined_quant` and `defined_quant_protocol`. |
| Protocol records | 0.4.0 | Compatibility version of the operation, port, authorization, and governance interchange records. |
| MCP distribution | `defined-quant-mcp` 0.1.0a1 | Release version of the optional STDIO transport wheel. |
| Registry entity | record-specific SemVer | Version of an exact Method, Capability, Backend, Adapter, or Implementation. |
| DQ-native adapter | distribution and artifact pin | Exact installed distribution bytes admitted for the nine bundled native Implementations. |
| Legacy component | component-specific | Version of one exact bundled compatibility contract and implementation. |

The protocol version is independent of the core wheel and native component versions because record
compatibility is a different concern from package and catalog releases. It currently ships in the
core distribution so there is one core release train. Packaging tests verify both namespaces in
editable installation and in the built wheel.

`mcp_server/` is a second optional distribution, not a second calculation runtime. It installs the
`defined_quant_mcp` namespace and `defined-quant-mcp` console entry point, pins the exact compatible
core, and owns the MCP SDK dependency. All tool and resource dispatch crosses
`DefinedQuantService`; SDK types never enter `shared/`, `protocol/`, canonical records, or hashes.

`defined_quant_protocol` depends only on Pydantic and the standard library. It never imports
components, discovery, renderers, or host application code. `defined_quant.agent` is a compatibility
surface for operation types that previously lived under the component namespace; it re-exports the
canonical protocol models and defines no second model. New integrations should import
`defined_quant_protocol` directly.

The build hook compiles authored registry YAML into deterministic JSON so an installed core wheel
can discover and inspect methods without PyYAML. Authored YAML is validated during the build; the
installed runtime reads inert JSON and does not import adapters. The DQ-native adapter distribution
uses the `defined_quant.adapters` entry-point group, but discovery reads only entry-point metadata.
Loading an entry point is permitted only after exact plan, policy, availability, adapter identity,
and installed artifact attestation checks pass.

## 4. Canonical registry sources of truth

Each concern has one authored authority:

| Source | Owns | Must not own |
|---|---|---|
| `method.yaml` | Method identity and version; title, summary, taxonomy and discovery metadata; canonical user-facing inputs and outputs; formula, methodology, interpretation, assumptions and limitations; explicit conventions and defaults; method constraints; and a backend-neutral recipe. | Backend, adapter, provider, transport, implementation choice, credentials, executable code, or availability. |
| `capability.yaml` | Capability identity and version; kind; atomic input and output schemas; semantic ports; backend-neutral constraints; and the contract every implementation must satisfy. | User-facing financial interpretation or backend-specific behavior. |
| `conformance.yaml` | Backend-independent known answers, invalid and boundary cases, numerical tolerances, and invariants. | Implementation-specific evidence or provider responses. |
| `backend.yaml` | Backend identity and kind; locality, transport, network, credential, entitlement, licensing, and data-egress boundaries; no secrets. | Financial methodology or adapter dispatch. |
| `adapter.yaml` | Trusted adapter identity, family, distribution and dispatch key, supported transport, and adapter evidence references. | Exact artifact pins, financial defaults, fallback, or provider credentials. |
| `implementation.yaml` | Exact capability, backend bindings by role, adapter, sole exact artifact pin, support level, restrictions, implementation-specific dependency probes, trust assertions, and implementation evidence. | Method meaning, duplicated backend operational requirements, or runtime implementation selection. |
| Adapter package | Canonical-input mapping, one exact backend invocation, canonical-output and failure mapping, and cleanup. | Methodology, defaults, policy, resolution, or fallback. |

Categories are taxonomy metadata only. A method may be stored beneath a category for human
browsing, but category neither controls code location nor implies a handwritten component.

### 4.1 Method and capability schemas

Capability schemas are authoritative for fields that cross the method/capability boundary. A method
does not silently copy those field definitions. Instead, its schema places an
`x-defined-quant-capability-field` directive on the method field, naming an exact capability,
direction, and capability field. The metadata-only registry loader:

1. resolves the exact capability reference;
2. replaces the directive with that capability field's complete schema;
3. preserves the method field name; and
4. derives a `SchemaFieldBinding` audit record.

Method-only fields remain authored in `method.yaml`. For Simple Return, `prices`, `timestamps`,
`returns`, `return_kind`, `return_timestamps`, and `ordering_status` come from `returns.simple`;
`price_kind`, `declared_frequency`, and `gap_check` remain method-owned. The expanded Method and
Capability protocol records are closed and hashed independently. This makes the relationship
explicit without creating two editable definitions.

When a Recipe publishes a Method input directly as a Method output, the loader compares the two
expanded executable field schemas byte-for-byte after removing only titles, descriptions, and the
semantic port's input/output direction. Types, bounds, enumerations, constants, shape, units,
conventions, provenance requirements, and every other semantic port dimension must remain equal.
An incompatible passthrough is rejected during registry loading.

### 4.2 Simple Return reference vertical slice

The canonical slice contains:

- Method `dq.market_data.simple_return` with one recipe step referencing capability
  `returns.simple`;
- Backend `dq_native`, classified as a local DQ-native runtime;
- Adapter and Implementation `dq_native.simple_return`;
- a separately installable `defined-quant-adapter-dq-native` distribution with a pure calculation
  kernel and trusted mapping adapter;
- capability conformance plus separate method, adapter, and implementation evidence; and
- an exported website Component page at `/components/simple-return`.

The other six bundled Methods follow the same separation, with multi-step volatility recipes
decomposed into their atomic statistical Capabilities. Every old component folder remains only to
prove preserved behavior and compatibility. Its `subject_hash` may be retained as migration
provenance on implementation evidence; it is not a Method or Capability identity and does not
enter new hash semantics.

## 5. Resolution, trusted execution, and records

### 5.1 Proposal and preference boundary

A `PlanProposal` contains an exact Method reference, structured financial input, declared
conventions, and a closed `ResolutionConstraintSet`. Constraints may target an implementation,
backend, adapter family, backend kind, transport, locality, or network behavior globally, per step,
or per capability. Their modes are required, preferred, forbidden, allowed set, or automatic.

- Required means select the named eligible choice or stop.
- Preferred means consider the ranked choices first; alternatives are permitted only by an
  explicit fallback setting.
- Forbidden removes the named choices.
- Allowed set limits resolution to named choices.
- Automatic lets service policy resolve only when no user preference was expressed.
- Local-only and network-forbidden constraints exclude remote or networked realizations.

Origins remain explicit: `user_explicit`, `user_profile`, `host_policy`, or
`automatic_resolution`. A claimed user or profile origin requires a separate host-recognized
receipt bound to the exact constraint and trusted session. The proposal cannot mint that receipt.
Instructions found in datasets, retrieved documents, provider responses, or other untrusted content
are data, never implementation preferences. An agent interpretation is not `user_explicit` unless
the user actually expressed or confirmed it.

Proposal financial fields and constraint targets are closed. Credentials, tokens, secrets,
connection strings, executable code, imports, arbitrary URLs, raw SQL, and arbitrary MCP tools are
rejected and never enter registry or identity hashes.

### 5.2 Deterministic compilation

`compile_plan` receives the proposal plus an exact registry, host policy, explicit availability
snapshot, trusted origin receipts, and host constraints. It performs no adapter import, filesystem
probe, provider call, credential lookup, or ambient fallback. It:

1. validates the Method and applies only authored defaults;
2. evaluates closed Method constraints and material conventions;
3. validates the recipe, exact Capability references, schema fields, and semantic ports;
4. collects exact registered Implementations for every step;
5. applies user constraints without changing their origin or fallback semantics;
6. applies policy, trust, implementation restrictions, backend boundaries, data handling, and
   explicit availability;
7. deterministically selects one eligible Implementation per step; and
8. records every candidate, rejection, selected identity, fallback decision, and explanation.

The result is `compiled`, `needs_information`, or `refused`. Unknown availability is not assumed
available. An unavailable or forbidden required choice is never silently substituted. Unrelated
registry, policy, and availability additions are excluded from the bound slices and therefore do
not change a plan identity.

### 5.3 Adapter discovery, installation, and trust

Adapter discovery reads installed distribution and entry-point metadata only; it never calls
`EntryPoint.load`. The host recomputes a canonical manifest from installed distribution metadata,
the declared entry point, and installed package resource bytes. An `ArtifactAttestation` is issued
only when that manifest matches the registry's exact distribution, version, and artifact hash.

`TrustedAdapterCatalog` is host-constructed from explicit policy and attestations. Installation is
not trust and does not make an implementation eligible. Immediately before one step is invoked, the
catalog rechecks the compiled Implementation, Adapter, backend bindings, policy admission,
availability, and artifact attestation. Only then may it load the exact dispatch entry point. The
adapter cannot resolve a different implementation or perform fallback.

Backend records are the sole authority for network, credential, licensing, entitlement, locality,
transport, and data-egress boundaries. The host derives the corresponding availability checks from
the exact backend bindings; an Implementation authors only dependency probes unique to that exact
realization. Adapter records identify a distribution and dispatch surface, while the Implementation
alone owns its exact distribution version and artifact hash.

The core distribution contains no provider SDK, credential, or optional backend dependency.
External adapters should be separate distributions. No production OpenBB, LSEG, statsmodels,
QuantLib, database, HTTP, or external-MCP record is present until its code and evidence exist.

### 5.4 Exact execution and immutable records

`execute_plan` accepts only a retained exact compiled-plan reference. It rechecks registry identity,
the bound policy slice, and current exact availability, materializes recipe inputs, validates each
Capability input, invokes only the selected trusted Adapter, validates each canonical step output,
builds the Method output, and validates that output. There is no runtime resolution or fallback. If
an exact implementation becomes unavailable, the run fails; choosing another implementation
requires recompilation and a new plan identity.

Identity domains are separate:

- Method and Capability contract hashes;
- Backend, Adapter, and Implementation specification hashes;
- policy and availability hashes;
- compiled plan hash and `dqplan:` reference;
- adapter artifact hash and attestation;
- dataset identity;
- canonical step hash and `dqstep:` reference; and
- complete run hash and `dqrun:` reference.

A `PlanRecord` binds the compiled plan and compiler runtime. Each `StepRecord` binds the exact
Capability, Implementation, Adapter, backend roles, canonical inputs, input and output validation,
adapter request/result, provider interactions, warnings, artifacts, timestamps, and failure or
success. A `RunRecord` binds the Method, Plan, execution request, datasets, ordered Step records,
canonical Method output, Method-output validation, warnings, exact executor identity, and complete
failure or success. A failed validation or step cannot produce a successful run. Provider
authentication and entitlement are runtime facts, never planning claims.

## 6. Legacy direct component compatibility

### 6.1 Discovery, inspection, and agent integration

All seven calculations are migrated. The component catalog remains available only while old clients
move to the methods-first surface, and is removed on 2026-12-31 or the first 0.2.0 release,
whichever comes first. Discovery reads only contracts; it does not import calculation code. It
returns stable scores, positive field matches, and separate boundary matches from do-not-use and
unsupported-scope fields. A boundary-only match never recommends a component.

`ContractIndex` takes one deep-immutable metadata snapshot, orders it by stable component ID, and
precomputes normalized positive-term and facet postings. It retains immutable authored values so
exact phrase scoring and boundary explanations are computed only for shortlisted records.
The compatibility service owns one process-lifetime index; constructing a new service is the
explicit refresh boundary. The discovery snapshot is never an execution trust source: execution
resolves and verifies the selected filesystem subject again.

The repo-local `.agents/` skill is a host convenience layer for the canonical Method API. It uses
Method discovery, exact compilation, exact execution, and bounded Run retrieval without redefining
inputs, defaults, constraints, outputs, or presentation semantics. Its old catalog and direct-run
helpers are explicitly legacy compatibility scripts until the same deletion milestone. This
agent-host skill is distinct from a trusted implementation Adapter in the governed registry.

### 6.2 Native operation execution

The legacy unmanaged `OperationRequest` binds an exact component ID, component version, and `subject_hash`
to candidate input, caller-asserted provenance, and a bounded artifact request. Catalog roots and
output directories are trusted launch configuration and never semantic request fields.

`defined_quant.operation_runtime` provides the one canonical native invocation path used by the
thin CLI, executable numerical evidence, and `DefinedQuantService`:

1. Validate the closed operation request.
2. Resolve the component by stable ID, freshly verify its filesystem-backed subject, and refuse an
   exact version or subject mismatch.
3. Validate candidate input through the component's canonical Pydantic `Inputs` model.
4. Invoke only the callable declared by the component contract.
5. Validate the result through the canonical `Output` model and verify component identity and
   subject provenance.
6. Materialize normalized input, typed result, and requested SVG artifacts in a sibling staging
   directory.
7. Reconcile exact staged members, canonical manifest bytes, and every declared digest.
8. Publish once with atomic no-replace semantics and return the typed outcome.

Numerical evidence enters the same invocation seam through a raw mapping because deliberate
non-finite fixtures cannot be represented in `OperationRequest` JSON. A direct Python call to a
component remains possible for library users, but it bypasses the operation request, worker,
manifest, session record, and host trust boundary.

Compatibility dispatch does not hardcode component IDs. Do not add another component to exploit
that generic machinery: all seven calculations already execute through the canonical registry, and
the remaining work is removal of the compatibility runtime at the stated milestone. The old catalog
and native preflight require filesystem-backed contracts, so zipimport and single-file frozen
layouts remain outside that compatibility boundary.

### 6.3 Datasets, records, and bounded retrieval

The current dataset registry normalizes only the supported structural JSON and CSV forms. It
records every host-applied normalization event and publishes `DatasetPayloadV1`, `DatasetRecordV1`,
and manifest-reconciled `OperationRecordV1` through owner-private, content-addressed session
storage. Configured file roots are launch-time host configuration, not model-controlled paths.

A dataset-backed operation resolves declared dataset fields and literals into the same component
input used by native execution. `OperationManifest` records the successful unmanaged bundle;
`OperationRecordV1` reconciles that manifest with its binding, dataset, result, and member bytes.
Session references expose bounded metadata, result fields, messages, and artifacts instead of
returning an entire dataset or operation to the model.

Every read revalidates schemas, hashes, members, cross-record dimensions, and source bindings.
References expire with the session; corruption is quarantined and never repaired or partially
returned. The store retains no accepted raw source bytes or caller file path. Digests establish
immutable byte consistency, not source authenticity, financial correctness, or independent
execution.

### 6.4 Host, worker, and MCP boundaries

`DefinedQuantService` is the only transport dispatch boundary. `mcp_server/` owns SDK
conversion, response bounds, framing, and STDIO transport; it does not reimplement discovery,
validation, calculation, storage, or governed resolution.

Native component inspection and execution occur in bounded worker processes. The controller
allowlists inherited environment, enforces request and control-channel ceilings, captures component
stdout and stderr, applies process, descendant, memory, timeout, cancellation, and cleanup limits,
and refuses publication after worker failure. This is bounded process containment, not a complete
hostile-code sandbox; installed code still runs with the local user's authority.

`local_host_platform` is the sole production platform selector. The POSIX provider implements
handle-contained roots, owner modes, locking, atomic no-replace publication, and process groups.
The Windows provider implements handle-relative reparse refusal, local-volume containment,
owner-only protected DACLs, `LockFileEx` locking, handle-based no-replace publication, tombstone
cleanup, and suspended-process Job Objects. Callers observe only opaque handles, bytes, and stable
failures. Platform mechanisms may differ, but canonical records, digests, references, failures,
trust wording, envelopes, resource projections, and CLI output may not.

The optional local MCP alpha is a separate STDIO distribution. The in-tree host, worker, and
transport do not by themselves establish release support: the same core and MCP wheels must pass
the complete Windows, macOS, and Linux matrix on Python 3.11 and 3.13 through the official client.
Its frozen host and transport contract is
[`docs/LOCAL_MCP_ALPHA_DESIGN.md`](docs/LOCAL_MCP_ALPHA_DESIGN.md).

### 6.5 Unmanaged trust boundary

The compatibility operation result is intentionally unmanaged. Its manifest is an internally reconciled
assertion about the exact request, component, normalized input, result, and materialized bytes.
Anyone can construct an unsigned Pydantic-valid manifest, so it is not independent execution
attestation. It does not establish that caller data is authentic, a method is suitable, an
interpretation is correct, a person approved an analysis plan, or a portable research bundle was
independently reproduced. Caller provenance is a recorded assertion, not a provider or Defined
Quant attestation.

## 7. Trust bindings and visualization

For a bundled native component, `subject_hash` covers behavior and its enforceable compatibility
contract:

- behavior-defining component Python;
- the behavior projection of `contract.yaml`, including guidance and semantic display meaning while
  excluding layout-only hints;
- generated Pydantic input/output schemas and public exports;
- shared validation and type code used by the component;
- declared component and external dependencies; and
- contract fields used by canonical discovery and routing.

Methods-first records use separate domain-separated identities for Method and Capability contracts;
Backend, Adapter, and Implementation specifications; policy and availability; compiled Plan;
installed adapter artifact; Step; and complete Run. These domains do not reinterpret or replace a
legacy native `subject_hash`. Implementation evidence may cite an old subject as migration
provenance, but changing that implementation does not change the Method contract hash.

Method evidence, Capability conformance, Implementation evidence, Adapter review, artifact
attestation, exact-commit CI, provider authentication, dataset provenance, execution integrity,
domain review, and independent reproduction remain separate claims rather than one badge. Static
registered support is also distinct from one installation's current availability.

Components return closed `VisualizationSpec` data. `shared/charts.py` renders trusted SVG without
accepting arbitrary HTML, JavaScript, remote resources, or third-party figure objects.
Transformations that change an analytical answer belong in typed method or component inputs and
outputs, not in the renderer. Output paths and similar rendering choices remain host concerns.

## 8. Legacy managed authorization and semantic composition

The pre-migration managed-authorization experiment remains separate from both direct native
execution and the canonical methods-first Plan. Its immutable `AnalysisPlan` contains exactly one
step. A packaged data-driven policy
allowlists only `dq.market_data.simple_return` version `0.3.4`, subject
`ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a`, with explicit opt-in to its
draft lifecycle and non-empty timestamps.

The catalog-aware validator checks the exact installed subject, required questions, canonical
Pydantic input, declarative constraints, and policy requirements without executing the calculation.
Success produces a deterministic `ValidationReceipt`; manual approval binds the exact receipt and
plan. Authorization is reproduced against plan, policy, component, dataset, receipt, and approval
roots before any future managed runner could act. Any semantic change requires new validation and
approval. This surface currently authorizes a possible future calculation but performs none.

Closed semantic ports compare direction, concept, unit, shape, cardinality, convention, ordering,
frequency, and provenance requirements. They establish whether a producer field may be considered
for a consumer field; they do not move data, bypass consumer validation, authorize a multi-step
plan, or execute one. A `depends_on` declaration binds implementation dependencies and is not proof
of port compatibility.

Log Return and Historical Volatility form a machine-checkable legacy semantic chain: both
the return-series and log-return-convention ports match, while Simple Return does not satisfy the
log-only convention. This compatibility does not execute or authorize a multi-step legacy plan.
The canonical executor traverses registered single- and multi-step recipes, including the
volatility Methods, through exact compiled bindings.
Exact source citations, production provider authentication, provider-backed execution, and
portable independent reproduction remain deferred.

## 9. Authoring and static publication

Canonical authoring now happens in `registry/`. The build validates exact references, expands
capability-owned schema fields into Method schemas, rejects secret-bearing records, and emits one
deterministic compiled registry bundle. Discovery and inspection consume that bundle or the authored
metadata; neither imports executable code.

`authoring/export_component_pages.py` joins Method, Capability, registered Implementation, Backend,
evidence, and trust-boundary records into deterministic static JSON. Its public page record contains
the stable ID, slug, title, category, summary, financial explanation, canonical schemas, recipe,
capabilities, registered implementations and backend kinds, separate evidence claims, and provider
and trust boundaries. It deliberately excludes dispatch fields and live installation state.

All seven established website slugs remain stable, including `/components/simple-return`,
`/components/log-return`, and `/components/historical-volatility`. A page's implementation list
means registered support, not that one installation currently has the package, credentials,
licence, entitlement, reachability, or policy admission required to execute it. The private website
consumes generated JSON only and never imports Method, Adapter, or Implementation code.

The old `authoring/export_catalog.py` remains only for legacy component validation until
2026-12-31 or the first 0.2.0 release, whichever comes first. Do not add another hand-authored
website catalog.

## 10. Current claim boundary

All seven calculations remain lifecycle `draft`, with author-asserted evidence and no independent
domain review. All seven have crossed the complete canonical registry, compilation, adapter,
execution-record, and website-export boundary. This improves separation, identity, integrity, and
inspectability without certifying financial suitability or universal correctness.

The implemented product boundary is:

| Surface | What exists now | What remains outside the claim |
|---|---|---|
| Method discovery | Metadata-only `search_methods` and joined `inspect_method` over every validated registry Method | Highest-ranked-result suitability or claiming a Method fits beyond its declared scope |
| Registry | Separate Method, Capability, Backend, Adapter, Implementation, conformance, evidence, and taxonomy records; compiled into the core wheel | Production external-backend records without real adapters and evidence |
| Resolution | Closed user/host constraints, origin receipts, explicit policy and availability, deterministic exact binding, and no silent fallback | Treating inferred or retrieved preferences as explicit user instructions |
| Governed execution | Exact compiled single- and multi-step execution for all seven Methods through nine trusted DQ-native adapters with canonical validation | Production provider calls or substituting an unavailable compiled implementation |
| Methods-first records | Immutable session-scoped Plan, Step, and complete Run records with distinct identities and failure semantics | Durable cross-session run retention or independent reproduction |
| Adapter ecosystem | Metadata-only entry-point discovery, installed artifact recomputation, explicit attestation, and policy-gated load for the DQ-native adapter | OpenBB, LSEG, statsmodels, QuantLib, database, HTTP, or external-MCP production adapters |
| Website projection | Seven deterministic registry-derived pages preserving established component URLs | Treating registered support as installation availability on a public page |
| Component compatibility | Import-free old discovery and one exact component per unmanaged worker operation with reconciled records | A permanent parallel architecture or a managed-execution claim |
| Dataset input | Supported structural JSON/CSV registration, configured-root ingestion, immutable session records, and field mapping | Excel, databases, SQL Server, provider APIs, arbitrary external MCP data acquisition, or source authentication |
| Managed authorization | One-step validation, manual approval, and revalidated binding for one exact Simple Return subject | Calculation execution or multi-step authorization |
| Reproduction | Stable canonical inputs, exact identities, records, members, and hashes within declared boundaries | Portable independently verified replay or a reproduced research bundle |

The optional local MCP alpha transport is implemented as `defined-quant-mcp`, but release support
remains gated on the full six-cell native installed-wheel matrix. The canonical public direction is
`search_methods`, `inspect_method`, `compile_plan`, `execute_plan`, `get_plan`, `get_run`,
`get_dataset`, and `read_artifact`. Explicitly named component APIs remain migration aliases with a
deletion milestone, not peers of the methods-first runtime. No current record may receive a
provider-authenticated, independently verified, or reproduced label unless a separately reviewed
runtime fact actually establishes that claim.

The native migration sequence is complete. The next migration action is deletion of the legacy
runtime by the stated milestone, after downstream compatibility checks. Production external
adapters, provider credential brokering, host-confirmed preference-receipt issuance, live provider
availability, market-data acquisition, durable run retention, cancellation-isolated remote
execution, portable provider substitution, and independent reproduction remain deferred.

## 11. Test and migration acceptance

Tests are separated by the claim they support:

1. Registry-schema tests prove closed records, canonical ordering and identities, exact references,
   capability-field expansion, secret rejection, and metadata-only wheel loading.
2. Method tests prove explicit conventions, authored defaults, constraints, recipe validity,
   interpretation, limitations, and method-only fields.
3. Capability-conformance tests run the same known answers, boundaries, tolerances, and invariants
   against every registered Implementation of that Capability.
4. Implementation and Adapter tests prove artifact pins, canonical input/output mapping, sanitized
   failures, malformed-output refusal, cleanup, and that installation without policy and attestation
   is not trust.
5. Differential tests compare distinct Implementations only within declared numerical tolerances;
   matching names never establish equivalence.
6. Compiler tests cover automatic resolution, trusted preference origins, required/preferred/
   forbidden/allowed-set semantics, explicit fallback, policy and availability filtering, stable
   candidate receipts, prohibited controls, unrelated-registry stability, and no silent fallback.
7. Execution tests prove that only the compiled binding runs, availability or policy drift fails,
   canonical inputs and outputs validate, exact identities are recorded, and a failed step or Method
   output cannot produce false success.
8. Website-export tests prove stable slugs and deterministic static records while keeping Method,
   conformance, Implementation, Adapter, and runtime-availability claims separate.
9. Packaging and cross-platform tests prove installed-wheel isolation, canonical bytes and hashes,
   entry-point metadata boundaries, and equivalent behavior on Windows, macOS, and Linux with
   Python 3.11 and 3.13.

A migrated component is accepted only when all relevant layers pass through the canonical service,
not merely when its numerical kernel matches the old function. All seven now meet that structural
path; the legacy compatibility runtime is scheduled for removal rather than maintained as a peer.
