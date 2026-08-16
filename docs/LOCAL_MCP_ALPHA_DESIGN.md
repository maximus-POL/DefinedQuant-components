# Local MCP alpha technical design

**Status:** frozen implementation contract from PR0. Phase 1 now supplies the shared operation
runtime and `DefinedQuantService`; Phase 2 supplies its immutable import-free contract index;
Phase 3 supplies the canonical dataset/operation records, strict normalizer, bounded projections,
and scoped ephemeral CAS. Phase 4 now supplies the subprocess worker, native platform providers,
and separate `defined-quant-mcp` STDIO transport. Native six-cell release validation remains
mandatory; the repository still does not contain the deferred protocol-0.5 implementation.

This document is the single architectural record for the local MCP alpha. Its companion JSON
files are normative test fixtures, not generated production schemas:

- [`local_mcp/hash_vectors.v1.json`](local_mcp/hash_vectors.v1.json)
- [`local_mcp/host_failures.v1.json`](local_mcp/host_failures.v1.json)
- [`local_mcp/evaluation_cases.v1.json`](local_mcp/evaluation_cases.v1.json)

## Post-freeze amendments

PR0 froze this contract in `95bcd8b` on 2026-08-15. The table records every later commit that
amended this document through the current Phase-4 readiness work; each row is derived from that
commit's actual diff.

| Date | Commit | Phase | Section changed | What changed | Reason |
|---|---|---|---|---|---|
| 2026-08-15 | `727ac430` | Phase 1 | Status | Recorded that `operation_runtime` and `DefinedQuantService` had landed and removed the obsolete claim that no shared host runtime existed. | Keep the frozen contract's implementation starting point consistent with the completed Phase-1 extraction. |
| 2026-08-15 | `5c2d1fb` | Phase 2 | Status; §10 | Recorded the immutable import-free index and replaced the 10,000-record timed acceptance case with a bounded functional case whose timings are diagnostic only. | No portable wall-clock threshold exists, so a timed gate would make deterministic discovery depend on CI hardware. |
| 2026-08-15 | `f025b65` | Phase 3 | Status; §7.1; §7.2 | Recorded the Phase-3 records and CAS, changed cursors to bind a domain-separated SHA-256 digest of selector text, and deferred non-POSIX local-file and session-store behavior pending an equivalent tested boundary. | Match the contract to the implemented opaque-cursor and fail-closed storage security boundaries without claiming untested platform behavior. |
| 2026-08-15 | `c7e5ca8` | Phase 3 / Phase-4 readiness | §7.3; §8; §10 | Scoped the whole MCP alpha, including its worker, SDK-client matrix, and packaging target, to macOS and Linux and made Windows a separate future workstream. | The alpha depends on tested POSIX containment, ownership, locking, cleanup, and process controls for which no Windows equivalent yet exists. |
| 2026-08-15 | `9fcd648` | Phase-4 planning | §3; §10 | Explicitly excluded Phase 4B from Phase 4, froze the no-4B release targets, and made later protocol-0.5 composition a separately reviewed change with its required compatibility and hash tests. | Prevent optional provenance and composition work from expanding the initial transport implementation mid-phase. |
| 2026-08-15 | `8efa80a` | Phase-4 readiness | §7.1 | Added the 10,000-record construction cap, specified but deferred the 500-candidate query cap, and recorded controller memory and timing observations without adding a deadline. | Bound long-lived controller indexing deterministically while preserving frozen search projections and avoiding machine-dependent outcomes. |
| 2026-08-15 | this commit | Phase-4 planning | §7.1; §7.2; §7.3; §8; §10 | Restored Windows as a required alpha release target; froze the cross-platform byte-identity contract, platform-abstraction boundary, local-path policy, and native Windows filesystem, session-store, worker, STDIO, official-client, CI, and packaging acceptance gates. | Platform breadth and deterministic outputs are product requirements; the current fail-closed Windows behavior is unfinished implementation rather than a reason to narrow the alpha. |
| 2026-08-16 | this commit | Phase 4 | Status; §10 | Recorded the in-tree Windows, macOS, and Linux providers, subprocess worker, and separate locked MCP STDIO distribution while retaining native six-cell validation as a release gate. | Keep implementation status truthful without turning unexecuted native validation into a release-support claim. |
| 2026-08-16 | this commit | Phase 4 / release handoff | §7.2; §7.3; §8; §10 | Replaced obsolete future-Windows and uncommitted-spike descriptions with the implemented secure-filesystem, session-state, Job Object, binary-STDIO, SDK-isolated server, exact-wheel, and installation state; marked native six-cell validation as the remaining release gate. | Make the frozen plan directly usable for MCP completion and review without misdescribing landed Windows mechanisms or overstating unexecuted native validation. |
| 2026-08-16 | this commit | Phase-4 remediation | §7.1 | Added the 2 MiB resolved worker-request and 4 MiB worker-control-response ceilings and required their controller-side overflow mappings to remain distinct from worker crashes. | Legal size refusal must occur before worker launch and oversized typed results must retain the closed input/result limit failures rather than collapsing to a retryable crash. |
| 2026-08-16 | this commit | Phase-4 remediation | §7.3 | Made any component stdout or stderr outside the typed control protocol a contract failure, and made Python warnings deterministic errors through the constructed worker environment. | Even small untyped output can disclose data or corrupt a future transport if accidentally forwarded; an explicit warning policy removes interpreter-default variability without exposing captured text. |
| 2026-08-16 | this commit | Phase-4 remediation | §6 | Split invalid launch arguments, unavailable native host capabilities, and unexpected startup failures into distinct redacted audit outcomes and exit statuses while retaining only stable provider codes. | A missing native provider is an operator/platform condition rather than bad configuration, and no native message, path, SID, or exception representation is needed to diagnose that class safely. |
| 2026-08-16 | this commit | Phase-4 remediation | §7.2 | Routed caller output-parent creation and CLI request-file reads through the secure-filesystem provider, including native handle-relative Windows traversal. | Unicode and long local paths must work without depending on the machine-wide `LongPathsEnabled` registry value or test setup that silently assumes it. |
| 2026-08-16 | this commit | Phase-4 remediation | §7.3 | Removed the Windows home-directory staging dependency; placed every worker workspace inside a fixed owner-private, locked, orphan-cleaned session-state namespace on the selected output volume; limited Windows platform environment inheritance to validated `SYSTEMROOT`; and recorded the Windows bootstrap-current-directory mechanism. | A controller crash must not leave unmanaged home/drive-root litter, home paths do not belong in the worker allowlist, and long-path-safe same-volume publication must retain liveness-protected cleanup. |

Going forward, every amendment to this document must add one row here in the same commit that
changes the contract. The row must identify the date, phase, affected section, exact change, and
reason; SDK behavior corrections, limit tuning, and platform findings during Phase 4 are not
exceptions.

## 1. Implementation baseline

PR0 starts from `origin/main` at `417b01bc6cae045316631d17e3efb74c7f065bb6`. The relevant facts are:

| Item | Baseline |
|---|---|
| Core distribution | `defined-quant` `0.1.3` |
| Protocol package | `defined_quant_protocol` `0.4.0` |
| New `AnalysisPlan` records | default to protocol `0.4.0` |
| Catalog | seven components |
| Simple Return | `0.3.4`, subject `ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a` |
| Tests | 525 pass |
| Static checks | Ruff and both documented mypy invocations pass |
| Catalog checker | all seven components pass |
| Installed wheel | already asserts protocol `0.4.0`, the seven exact component IDs, and the Simple Return identity above |

## 2. Frozen architecture

The target architecture is:

```text
MCP client -- local STDIO --> server.py (SDK conversion only)
                                  |
                                  v
                         DefinedQuantService
                       /          |           \
          contract-only index  session CAS  bounded worker
                (no imports)                 /          \
                                    selected inspect  operation_runtime

existing CLI -------------------------------> operation_runtime
evidence execution --------------------------> operation_runtime
```

`DefinedQuantService` is the transport-neutral host API. It owns a process-lifetime catalog
snapshot and discovery index, exact component selection, dataset and operation reference
resolution, port comparison, paging, and dispatch. `operation_runtime` owns canonical unmanaged
execution, validation, manifest construction, artifact publication, and bundle verification. In
Phase 1 the existing CLI and evidence paths move to that same runtime and must retain byte-level
semantic parity; MCP never receives a second implementation of calculation logic.

Search reads contracts only and remains import-free. Only selected schema inspection and selected
execution cross the bounded worker process boundary. Component imports never occur in the
long-lived controller. The MCP package remains an optional local STDIO adapter; all SDK-specific
types, handler registration, initialization, and transport startup are isolated in `server.py`.

Datasets and successful operations move through immutable content-addressed references. The
controller resolves references and explicit field mappings before it builds the existing canonical
operation request. A reference is a scoped identifier, not a component input, path, capability, or
authorization token.

Every MCP execution is explicitly `unmanaged`. A successfully returned operation record is an
unsigned assertion constructed and internally reconciled by the configured local host. Its
digests bind the exact request, selected subject, result, and declared artifact bytes, but do not
independently prove that execution occurred or identify an independent executor. The record does
not authenticate supplied data, attest correctness, or create financial or managed-plan approval.

Deferred scope is closed: providers and network fetch, hosted or HTTP transport, persistent or
cross-session storage, authentication, secrets, managed multi-step execution, arbitrary
transforms, and a UI are not part of the alpha.

### 2.1 Calculation receipt and reproduction boundary

The alpha `CalculationReceiptV1` is a semantic role, not another Pydantic model, serialization,
reference, or hash domain. It consists of one schema-valid, digest-consistent `OperationRecordV1`
together with its exact `OperationManifest` and every manifest-declared member. The
`operation_runtime` owns construction and reconciliation of the manifest and member bytes;
`DefinedQuantService` completes the receipt role when Phase 3 publishes the immutable `dqop:v1`
record. MCP exposes bounded projections of those same records and never authors a second receipt.
The pre-execution managed `ValidationReceipt` is unrelated and must not be reused for this role.

The existing per-operation input, result, manifest, and artifact members are deterministic, but
they are not a portable `ReproductionBundle`: the alpha does not embed accepted raw source bytes,
installed wheels, an environment lock, signatures, or an independently verified replay result,
and its references expire with the session. Portable export, cross-session reopening, independent
verification, and reproduction claims remain deferred and require a separate future design.

## 3. Protocol 0.5 decision for Phase 4B

The repository currently advances the package protocol and the default version of new
`AnalysisPlan` records together at `0.4.0`. Component-output provenance is therefore a protocol
`0.5.0` change, not a `0.4.0` change.

The recommended Phase-4B change preserves lockstep:

| Surface | Current | Phase 4B |
|---|---|---|
| `ProtocolVersion` | literals `0.1.0` through `0.4.0` | add literal `0.5.0` |
| `SUPPORTED_PROTOCOL_VERSIONS` | `0.1.0` through `0.4.0` | retain all four and append `0.5.0` |
| `PROTOCOL_VERSION` | `Final[Literal["0.4.0"]] = "0.4.0"` | `Final[Literal["0.5.0"]] = "0.5.0"` |
| `AnalysisPlan.protocol_version` | accepts `0.2.0`–`0.4.0`; defaults `0.4.0` | accepts `0.2.0`–`0.5.0`; defaults new records to `0.5.0` |

Changing only `PROTOCOL_VERSION` would accidentally remove `0.4.0` from the current tuple because
the tuple ends with that constant. Phase 4B must retain an explicit `"0.4.0"` entry before the new
constant. `protocol/plan.py` must not rewrite an explicit legacy version, change
`managed.analysis_plan.v1`, or alter the semantic projection or canonical hash algorithm. The
existing explicit `0.4.0` plan vector
`e91633457b06fdbf55a4e0a67cf27210008618643b196b5889f35dd161a2748d` must continue to parse and hash
identically.

Phase-4B tests must prove that manifests default to `0.5.0` and accept `0.1.0`–`0.4.0`; plans
default to `0.5.0`, accept explicit `0.2.0`–`0.4.0`, keep the fixed `0.4.0` hash, and still reject
`0.1.0`; and both exported descriptors expose the intended version and nested plan enum/default.
If package and plan versions ever deliberately diverge, the change must document a technical
reason and add descriptor, compatibility, default, downgrade, and hash tests that demonstrate the
split. Silent divergence is not allowed.

Phase 4B also adds `SourceKind.COMPONENT_OUTPUT` and
`InterpretationMethod.COMPONENT_DERIVED` in `protocol/operation.py`, permits them only as a valid
pair, and then enables one compatible `dqop` output-to-input hop. Complete upstream lineage stays
in the host operation record. This does not add `execute_plan`, retries, rollback, or a managed
execution claim. PR0 changes neither `protocol/version.py`, `protocol/plan.py`, nor
`protocol/operation.py`.

The frozen Phase-4 release excludes Phase 4B. Its targets are `defined-quant` `0.2.0`, unchanged
`defined_quant_protocol` `0.4.0`, and the optional `defined-quant-mcp` `0.1.0a1`; the MCP alpha
remains dataset-to-one-component only and no component output is relabelled as another source
kind. A later Phase-4B release would retain the core and MCP targets while advancing
`defined_quant_protocol` to `0.5.0` through the separately reviewed lockstep change above.

## 4. Version-1 host interface

### 4.1 Shared wire rules

In the schemas below, an unmarked field is required. `field?` is optional; `= value` is its
default. Every object is closed: an unknown field is `invalid_tool_request`. Strings are Unicode,
but byte limits apply to their UTF-8 form. `JsonValue` is null, boolean, a finite IEEE-754-safe
number, a string without lone surrogates, an array, or a string-keyed object. Integer-valued
numbers are limited to the IEEE-754 safe range so they remain canonical.

```text
Sha256       = lowercase hex string matching ^[0-9a-f]{64}$
SafeId       = string matching ^[a-z][a-z0-9_]{0,63}$
ComponentId  = ASCII string[1..240 bytes] matching ^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$
SemVer       = canonical semantic-version string, at most 128 UTF-8 bytes
RelativeMemberPath = exact existing protocol ASCII string[1..512 bytes] matching
                     ^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)*$
DatasetRef   = ASCII string matching ^dqds:v[0-9]{1,3}:[0-9a-f]{64}$
OperationRef = ASCII string matching ^dqop:v[0-9]{1,3}:[0-9a-f]{64}$
Cursor       = opaque base64url string, 1..512 ASCII bytes, scoped to the session and query

ComponentRef = {
  id: ComponentId,
  version: SemVer,
  subject_hash: Sha256
}

SemanticPort = the exact existing defined_quant_protocol.SemanticPort object {
  direction: PortDirection,
  concept: PortConcept,
  unit: PortUnit,
  shape: PortShape,
  cardinality: PortCardinality,
  convention: PortConvention,
  ordering: PortOrdering,
  frequency: PortFrequency,
  provenance_requirement: PortProvenanceRequirement
}

PortDifference = the exact existing defined_quant_protocol.PortDifference object {
  dimension: "concept" | "unit" | "shape" | "cardinality" | "convention" |
             "ordering" | "frequency" | "provenance_requirement",
  producer_value: the existing PortValue member selected by dimension,
  consumer_value: the existing PortValue member selected by dimension
}

ProvenanceInput = {
  source_kind: "user_prompt" | "user_attachment" | "external_provider" | "synthetic",
  interpretation_method: "ai_interpreted" | "caller_structured" | "adapter_normalized",
  verification_status?: "unverified" | "caller_confirmed" = "unverified",
  label: string[1..240 bytes],
  references?: string[1..500 bytes][0..50] = [],
  content_sha256?: Sha256,
  assumptions?: string[1..500 bytes][0..50] = []
}

ExternalPreprocessing =
  {status:"none_declared"} |
  {status:"unknown"} |
  {status:"receipt_supplied",
   receipt:{schema_id:SafeId, sha256:Sha256}}

NormalizationEvent = {
  code: "json_missing_field_to_null" | "csv_empty_field_to_null" |
        "csv_number_parsed" | "csv_integer_parsed" | "csv_boolean_parsed" |
        "timestamp_to_canonical_utc",
  field_id: SafeId,
  count: integer[1..250000]
}

TrustNotice = {
  label: TrustLabel,
  statement: fixed statement selected by TrustLabel
}

Success<T> = {
  host_schema_version: 1,
  outcome: "ok",
  data: T,
  trust: TrustNotice
}

ClosedFailureDetails = the exact per-code object in host_failures.v1.json;
codes without a listed schema require exactly {}.

Failure = {
  host_schema_version: 1,
  outcome: "needs_information" | "refused" | "failed",
  error: {
    code: HostFailureCode,
    message: fixed redacted string[1..500 bytes],
    retry_allowed: boolean,
    details: ClosedFailureDetails
  },
  trust: TrustNotice
}
```

`ProvenanceInput` is caller-authored. `caller_confirmed` means only that the caller made the
assertion; it is not host or provider verification. `label`, `references`, and `assumptions` are
stored and hash-bound verbatim and may be returned by an explicit manifest view. Callers must not
place secrets, credentials, or local filesystem paths in those fields.

Every request accepts `host_schema_version?: integer = 1`. An integer other than `1` produces
`unsupported_host_schema` with that integer as `requested_version`; a non-integer produces
`invalid_tool_request`. Expected host failures remain a `CallToolResult`, not an MCP protocol
exception: success sets `structuredContent=Success`, `isError=false`, and one bounded text block
`Defined Quant host outcome: ok; use structuredContent.`; failure sets
`structuredContent=Failure`, `isError=true`, and one text block equal to the same fixed
`Failure.error.message`. Text content never adds data absent from the envelope. Malformed JSON-RPC,
an unknown MCP method, a malformed resource URI, or an unavailable process remains an MCP
protocol condition. An unknown tool name is `-32602` with fixed message `Unknown tool name.` and no
data; JSON-RPC parse/invalid-request/method errors retain standard `-32700`/`-32600`/`-32601`
codes and text; an unavailable process has no wire response. The closed code definitions,
surface-specific
allowed sets, safe messages, retry
rules, and mapping from every current `OperationErrorCode` are authoritative in
[`host_failures.v1.json`](local_mcp/host_failures.v1.json).

Reference inputs admit only a one-to-three-digit version token so the host can return
`unsupported_reference_version` without reflecting unbounded text; an overlong token is
`invalid_tool_request`. Only `v1` is supported and every successful response emits `v1`.

Initialization instructions begin with this fixed workflow text:

> For financial calculations, search the Defined Quant catalog, inspect only selected canonical
> contracts, register or reference data, execute through Defined Quant tools, and read the typed
> outcome. Do not inspect component source unless the user explicitly requests development,
> review, or debugging. Never infer an answer-changing convention. Operations are unmanaged and
> supplied data is not authenticated.

Because every tool object is closed, none accepts a catalog/component/import path, output/cache
directory, arbitrary URL or network option, secret or credential, arbitrary expression or code,
unregistered transform, or provider selection. Catalog, configured input roots, and state
locations are launch-time settings only.

The response trust labels and exact wording are:

| Label | Fixed statement |
|---|---|
| `contract_metadata_only` | Contract-only catalog metadata; no component was imported or executed. |
| `installed_subject_inspected` | Installed component identity and schema were inspected; no calculation was executed. |
| `unverified_caller_data` | Registered caller-supplied data is unverified and unauthenticated. |
| `structural_compatibility_only` | Semantic-port compatibility is structural only; it neither transfers data nor authorizes execution. |
| `unmanaged_execution` | Unsigned host-reconciled record for an unmanaged local operation; digests bind its request, selected subject, result, and declared artifact bytes. Data authenticity, financial approval, correctness, and independent execution are not attested. |
| `no_verified_result` | No dataset, component result, or digest-checked artifact was returned. |

All tools return structured JSON. Search `CallToolResult` objects are at most 64 KiB; every other
tool `CallToolResult`, including failures, is at most 256 KiB. The count is the compact UTF-8 JSON
encoding of the complete MCP result object, including `content`, `structuredContent`, and
`isError`, before the JSON-RPC envelope. An input `params.arguments` object is at most 1 MiB by the
same measure. Successful and failed tool/resource results set SDK TTL to zero and cache scope to
private. A server may configure lower limits, never higher ones.

### 4.2 Tool contracts

The JSON blocks below are concise, schema-valid wire-shape examples, not catalog snapshots or hash
vectors; repeated hexadecimal placeholders and shortened synthetic prose are intentional. The
three companion fixtures, not these examples, carry exact future-test values.

#### `search_components`

```text
Request = {
  host_schema_version?: integer = 1,
  query: string[0..512 bytes],
  filters?: {
    categories?: string[1..128 bytes][0..64] = [],
    groups?: string[1..128 bytes][0..64] = [],
    tags?: string[1..128 bytes][0..64] = [],
    intents?: string[1..128 bytes][0..64] = [],
    input_concepts?: string[1..128 bytes][0..64] = [],
    output_concepts?: string[1..128 bytes][0..64] = [],
    lifecycles?: string[1..128 bytes][0..64] = [],
    profiles?: string[1..128 bytes][0..64] = []
  } = {},
  limit?: integer[1..5] = 5
}

SearchData = {
  compact: true,
  query: string,
  terms: string[],
  total_matches: integer >= 0,
  hits: {
    component_id: ComponentId,
    version: SemVer,
    title: string,
    lifecycle: string,
    summary: string,
    score: integer,
    matched_terms: string[],
    unmatched_terms: string[],
    positive_matches: {field: string, terms: string[], values: string[]}[],
    boundary_matches: {field: string, terms: string[], values: string[]}[]
  }[0..limit],
  facets: {
    categories: Facet, groups: Facet, tags: Facet, intents: Facet,
    input_concepts: Facet, output_concepts: Facet, lifecycles: Facet, profiles: Facet
  },
  truncated: boolean
}

Facet = {values:{value:string, count:integer>=1}[0..10], other_count:integer>=0}
```

Filters are OR within one facet and AND across populated facets. Ranking, explanations, facets,
and tie-breaking are deterministic. All eight facets are always present in the order shown and are
computed over the complete eligible set after filtering; values sort by descending count, then
UTF-8 bytes, and `other_count` covers values beyond ten. Search has no cursor and never exposes a
catalog page: at most five hits are returned and the caller refines the query or filters.
`truncated` is true exactly when `total_matches` exceeds returned hits. Search is contract-only and
import-free.

Annotations are `{readOnlyHint:true, destructiveHint:false, idempotentHint:true,
openWorldHint:false}`. It uses trust label `contract_metadata_only`; its closed failures are the
`search_components` set in the failure fixture.

Success example:

```json
{"host_schema_version":1,"outcome":"ok","data":{"compact":true,"query":"zephyr","terms":["zephyr"],"total_matches":1,"hits":[{"component_id":"dq.benchmark.synthetic_transform","version":"1.0.0","title":"Synthetic Transform","lifecycle":"draft","summary":"Synthetic contract-only search example.","score":13,"matched_terms":["zephyr"],"unmatched_terms":[],"positive_matches":[{"field":"discovery.aliases","terms":["zephyr"],"values":["zephyr"]}],"boundary_matches":[]}],"facets":{"categories":{"values":[{"value":"benchmark","count":1}],"other_count":0},"groups":{"values":[{"value":"evaluation","count":1}],"other_count":0},"tags":{"values":[{"value":"benchmark","count":1},{"value":"synthetic","count":1}],"other_count":0},"intents":{"values":[{"value":"benchmark_transform","count":1}],"other_count":0},"input_concepts":{"values":[{"value":"synthetic_input","count":1}],"other_count":0},"output_concepts":{"values":[{"value":"synthetic_output","count":1}],"other_count":0},"lifecycles":{"values":[{"value":"draft","count":1}],"other_count":0},"profiles":{"values":[{"value":"statistic","count":1}],"other_count":0}},"truncated":false},"trust":{"label":"contract_metadata_only","statement":"Contract-only catalog metadata; no component was imported or executed."}}
```

Failure example:

```json
{"host_schema_version":1,"outcome":"refused","error":{"code":"invalid_tool_request","message":"The request does not satisfy the closed tool schema.","retry_allowed":false,"details":{"fields":["limit"]}},"trust":{"label":"contract_metadata_only","statement":"Contract-only catalog metadata; no component was imported or executed."}}
```

#### `inspect_component`

```text
Request = {
  host_schema_version?: integer = 1,
  component_id: ComponentId,
  expected_version?: SemVer,
  expected_subject_hash?: Sha256,
  view?: "compact" | "schemas" = "compact"
}

InspectionData = {
  compact: boolean,
  view: "compact" | "schemas",
  component: ComponentRef,
  title: string,
  lifecycle: string,
  summary: string,
  guidance: {
    use_when: string[], do_not_use_when: string[], unsupported_scope: string[],
    required_questions: {id: SafeId, ask: string, why: string, resolves_to: string,
                         has_default: boolean, default?: JsonValue}[]
  },
  input_ports: {name: string, required: boolean, has_default: boolean, default?: JsonValue,
                semantic_port: SemanticPort}[],
  output_ports: {name: string, semantic_port: SemanticPort}[],
  unported_input_fields: string[],
  unported_output_fields: string[],
  input_schema_sha256: Sha256,
  output_schema_sha256: Sha256,
  input_schema?: JsonValue object,
  output_schema?: JsonValue object
}
```

`input_ports` and `output_ports` contain every field bearing semantic-port metadata, in JSON-Schema
property order; the two `unported_*` arrays contain every remaining field in that same order. Thus
the compact view is complete without duplicating full field schemas. `schemas` includes both
canonical Pydantic JSON Schemas; `compact` omits them and sets `compact=true`. Schemas are never
paged. If the selected schemas cannot fit the response limit,
`schemas` fails with `result_limit_exceeded`; the compact view must remain available. The selected
component is imported only inside the worker. An expected identity mismatch fails before any
calculation. Each schema digest is ordinary SHA-256 of `canonical_json_bytes(schema)`.

Annotations are `{readOnlyHint:true, destructiveHint:false, idempotentHint:true,
openWorldHint:false}`. Success uses `installed_subject_inspected`; failures use
`no_verified_result` and the `inspect_component` set in the failure fixture.

Success example:

```json
{"host_schema_version":1,"outcome":"ok","data":{"compact":true,"view":"compact","component":{"id":"dq.market_data.simple_return","version":"0.3.4","subject_hash":"ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a"},"title":"Simple Return","lifecycle":"draft","summary":"Computes decimal simple returns between successive caller-supplied price observations.","guidance":{"use_when":["The caller needs simple decimal returns between successive explicit price observations."],"do_not_use_when":["The adjusted or unadjusted price convention is unknown."],"unsupported_scope":["Fetching or cleaning market observations."],"required_questions":[{"id":"price_kind","ask":"Are these adjusted or unadjusted prices?","why":"The convention changes the economic interpretation even when the arithmetic is identical.","resolves_to":"price_kind","has_default":false}]},"input_ports":[{"name":"prices","required":true,"has_default":false,"semantic_port":{"direction":"input","concept":"price_series","unit":"price","shape":"ordered_series","cardinality":"one_or_more","convention":"ordered_positive_prices","ordering":"preserve_source_order","frequency":"inherited","provenance_requirement":"not_required"}},{"name":"price_kind","required":true,"has_default":false,"semantic_port":{"direction":"input","concept":"price_adjustment_kind","unit":"unitless","shape":"scalar","cardinality":"exactly_one","convention":"adjusted_or_unadjusted_price_kind","ordering":"not_applicable","frequency":"not_applicable","provenance_requirement":"not_required"}}],"output_ports":[{"name":"returns","semantic_port":{"direction":"output","concept":"periodic_return_series","unit":"decimal","shape":"ordered_series","cardinality":"one_or_more","convention":"simple_periodic_return","ordering":"preserve_source_order","frequency":"inherited","provenance_requirement":"component_bound"}},{"name":"return_kind","semantic_port":{"direction":"output","concept":"return_convention","unit":"unitless","shape":"scalar","cardinality":"exactly_one","convention":"simple_periodic_return","ordering":"not_applicable","frequency":"not_applicable","provenance_requirement":"component_bound"}}],"unported_input_fields":["timestamps","declared_frequency"],"unported_output_fields":["component_id","version","subject_hash","unit","assumptions","disclosures","warnings","transformations","derivations","visualizations","price_kind","return_timestamps","declared_frequency","ordering_status","gap_check"],"input_schema_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","output_schema_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"trust":{"label":"installed_subject_inspected","statement":"Installed component identity and schema were inspected; no calculation was executed."}}
```

Failure example:

```json
{"host_schema_version":1,"outcome":"refused","error":{"code":"component_identity_mismatch","message":"The requested component ID, version, or subject hash does not match the verified installed identity.","retry_allowed":false,"details":{"component_id":"dq.market_data.simple_return"}},"trust":{"label":"no_verified_result","statement":"No dataset, component result, or digest-checked artifact was returned."}}
```

#### `register_dataset`

```text
Source =
  {kind:"inline_rows", rows: {[source_name:string[1..240 bytes]]:JsonValue}[1..250000]} |
  {kind:"inline_json", text: string[1..524288 bytes]} |
  {kind:"local_file", path:string[1..4096 bytes], format:"csv",
   csv?: {delimiter?: ","|";"|"\t" = ",", header?:true = true} = {}} |
  {kind:"local_file", path:string[1..4096 bytes], format:"json"}

Column = {
  source_name: string[1..240 bytes],
  field_id: SafeId,
  data_type: "number" | "integer" | "string" | "boolean" | "timestamp",
  role: "value" | "timestamp" | "instrument" | "label" | "other",
  semantic_port?: SemanticPort
}

DatasetSemantics = {
  instrument?: {namespace: string[1..64 bytes], symbol: string[1..128 bytes]},
  currency?: string[1..32 bytes],
  frequency?: "intraday"|"daily"|"weekly"|"monthly"|"quarterly"|"annual"|"irregular",
  timezone?: string[1..128 bytes],
  ordering: "preserve_source_order",
  price_kind?: "adjusted" | "unadjusted",
  adjustment_policy?: string[1..240 bytes],
  distribution_policy?: string[1..240 bytes],
  calendar?: string[1..240 bytes],
  partial_period_policy?: string[1..240 bytes]
}

Request = {
  host_schema_version?: integer = 1,
  source: Source,
  columns: Column[1..128],
  semantics: DatasetSemantics,
  provenance: ProvenanceInput,
  external_preprocessing?: ExternalPreprocessing = {status:"unknown"}
}

DatasetRegistrationData = {
  compact: true,
  dataset_ref: DatasetRef,
  normalized_payload_sha256: Sha256,
  raw_source_sha256: Sha256,
  row_count: integer[1..250000],
  columns: {source_name: string[1..240 bytes], field_id: SafeId, data_type: string, role: string,
            semantic_port: SemanticPort|null}[1..128],
  semantics: DatasetSemantics,
  external_preprocessing: ExternalPreprocessing,
  normalization_events: NormalizationEvent[0..768],
  findings: {code: SafeId, severity: "warning", message: string[1..500 bytes],
             count: integer >= 1}[0..50],
  expires: "session_end"
}
```

Inline JSON and JSON files must be a top-level array of objects. Text JSON refuses duplicate keys
and non-finite numbers; already-parsed `inline_rows` is validated after the client parser and
cannot claim duplicate-key detection. Text JSON and CSV use strict UTF-8 without a BOM; accepted
source bytes and line endings are never rewritten before their raw digest. CSV has a header and
duplicate headers are refused.
`columns` declares the complete allowed source-field set: every declared `source_name` appears
exactly once, any undeclared field in a JSON object or CSV header is `invalid_dataset`, and field
IDs are unique. JSON rows may omit a declared field, which normalizes to null; a CSV header must
contain every declared source name exactly once. An optional
`semantic_port` uses the complete existing `SemanticPort` vocabulary with direction `output`.
Registration preserves row order and performs no sorting, filtering, filling, resampling,
deduplication, conversion, weighting, or calculation. Missing answer-changing semantics are
reported; they are never inferred. Finding messages are fixed by finding code and contain no
source values. Any blocking finding returns `invalid_dataset` and publishes no record, so a
successful record contains warnings only. Registration returns no values or preview.

`normalization_events` is the closed, machine-readable ledger of host-applied changes between the
accepted source and normalized payload; it is separate from quality `findings`. Events aggregate
exact affected-cell counts by `(code,field_id)`, contain no values, are unique by that pair, and
sort by code then field ID UTF-8 bytes. The sum of event counts is
`normalization_event_application_count` in an operation summary; a cell affected by two distinct
normalizations contributes once to each event. `json_missing_field_to_null` records an omitted
declared JSON field; `csv_empty_field_to_null` records an empty non-string CSV field; the three
`csv_*_parsed` codes record CSV text converted to number, integer, or boolean; and
`timestamp_to_canonical_utc` records an offset conversion or canonical timestamp text change.
Source-to-field renaming is already explicit in `columns` and is not duplicated as an event.
Every event field ID must name a declared column and its count cannot exceed `row_count`;
JSON-missing events apply only to JSON/object sources, CSV events only to CSV and the matching
declared type, and timestamp events only to timestamp columns.
Every accepted-source-to-payload value change must be accounted for by the complete column mapping
or one of these events; an unaccounted change is `internal_failure` and publishes no dataset.
The code list, uniqueness/sort keys, maximum, preprocessing statuses, and safe default are repeated
machine-readably in `hash_vectors.v1.json`; prose and fixture must compare equal in Phase-3 tests.

`external_preprocessing` describes only transformations before the accepted source reached this
host. Its safe default is `unknown`. `none_declared` and `receipt_supplied` are caller assertions;
for `receipt_supplied`, the alpha binds only the declared receipt schema ID and digest and neither
stores its bytes nor verifies its meaning. The host never infers `none_declared` from an empty
event ledger. Component-applied transformations remain in the component result rather than this
dataset ledger. Phase-3 tests must materialize the omitted-field `unknown` default, accept each
closed union branch, reject mixed or incomplete branches, and prove that changing status or a
receipt binding changes the resulting `dqds`.

For `inline_json` and `local_file`, `raw_source_sha256` is ordinary SHA-256 of the exact accepted
UTF-8 bytes. For `inline_rows`, it is ordinary SHA-256 of `canonical_json_bytes(rows)` and is
labelled as canonical structured input rather than original transport bytes. If
`provenance.content_sha256` is supplied, it must match this digest. Findings are grouped by stable
code and capped at 50; `count` preserves multiplicity. Registration has no paging.

The ledger comparison boundary begins with logical source cells: parsed request values for
`inline_rows`; values after strict UTF-8, duplicate-key-safe JSON decoding, and object-field
association for JSON text/files; or text/empty values after strict UTF-8 CSV lexical decoding and
header association. It ends before missing/empty handling, declared CSV scalar typing, timestamp
canonicalization, and the explicit source-name-to-field-ID projection. JSON/CSV syntax decoding,
unescaping, and header association are not events; the raw digest separately binds the bytes or
canonical structured input before that boundary. JSON logical cells already carry typed
`JsonValue` scalars. CSV number, integer, and boolean typing is recorded by the closed event codes;
timestamps remain strings and receive an event only when their canonical text changes.

Annotations are `{readOnlyHint:false, destructiveHint:false, idempotentHint:false,
openWorldHint:false}`. The session store is the only side effect. Success uses
`unverified_caller_data`; failures use `no_verified_result` and the `register_dataset` set in the
failure fixture.

Success example:

```json
{"host_schema_version":1,"outcome":"ok","data":{"compact":true,"dataset_ref":"dqds:v1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","normalized_payload_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","raw_source_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","row_count":2,"columns":[{"source_name":"observed_at","field_id":"timestamp","data_type":"timestamp","role":"timestamp","semantic_port":null},{"source_name":"adjusted_close","field_id":"price","data_type":"number","role":"value","semantic_port":null}],"semantics":{"instrument":{"namespace":"ticker","symbol":"ACME"},"frequency":"daily","timezone":"UTC","ordering":"preserve_source_order","price_kind":"adjusted"},"external_preprocessing":{"status":"none_declared"},"normalization_events":[{"code":"timestamp_to_canonical_utc","field_id":"timestamp","count":2}],"findings":[],"expires":"session_end"},"trust":{"label":"unverified_caller_data","statement":"Registered caller-supplied data is unverified and unauthenticated."}}
```

Failure example:

```json
{"host_schema_version":1,"outcome":"refused","error":{"code":"unsafe_input_path","message":"The local input path failed closed path and file-type checks.","retry_allowed":false,"details":{}},"trust":{"label":"no_verified_result","statement":"No dataset, component result, or digest-checked artifact was returned."}}
```

#### `describe_dataset`

```text
Request = {
  host_schema_version?: integer = 1,
  dataset_ref: DatasetRef,
  view?: "metadata" | "preview" = "metadata",
  cursor?: Cursor,
  limit?: integer[1..50] = 20
}

DatasetDescriptionData = {
  compact: boolean,
  dataset_ref: DatasetRef,
  normalized_payload_sha256: Sha256,
  raw_source_sha256: Sha256,
  row_count: integer,
  columns: {source_name: string[1..240 bytes], field_id: SafeId, data_type: string, role: string,
            semantic_port: SemanticPort|null}[],
  semantics: DatasetSemantics,
  external_preprocessing: ExternalPreprocessing,
  normalization_events: NormalizationEvent[0..768],
  findings: {code: SafeId, severity: "warning", message: string,
             count: integer >= 1}[0..50],
  expires: "session_end",
  preview?: {
    rows: JsonValue object[0..50],
    start: integer >= 0,
    returned: integer[0..50],
    next_cursor: Cursor | null,
    complete: boolean
  }
}
```

`metadata` is the default, sets `compact=true`, forbids `cursor` and a non-default `limit`, and
emits no values. `preview`
is explicit model-visible egress, sets `compact=false`, and pages immutable source-order rows.
`cursor` is valid only with `preview`; it binds the dataset digest, next row, limit-independent
query shape, and session. Preview row objects use normalized `field_id` keys in declared column
order. A page may contain fewer than `limit` rows to stay under 256 KiB.

Annotations are `{readOnlyHint:true, destructiveHint:false, idempotentHint:true,
openWorldHint:false}`. Success uses `unverified_caller_data`; failures use `no_verified_result` and
the `describe_dataset` set in the failure fixture.

Success example:

```json
{"host_schema_version":1,"outcome":"ok","data":{"compact":true,"dataset_ref":"dqds:v1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","normalized_payload_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","raw_source_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","row_count":2,"columns":[{"source_name":"observed_at","field_id":"timestamp","data_type":"timestamp","role":"timestamp","semantic_port":null},{"source_name":"adjusted_close","field_id":"price","data_type":"number","role":"value","semantic_port":null}],"semantics":{"ordering":"preserve_source_order"},"external_preprocessing":{"status":"none_declared"},"normalization_events":[{"code":"timestamp_to_canonical_utc","field_id":"timestamp","count":2}],"findings":[],"expires":"session_end"},"trust":{"label":"unverified_caller_data","statement":"Registered caller-supplied data is unverified and unauthenticated."}}
```

Failure example:

```json
{"host_schema_version":1,"outcome":"refused","error":{"code":"reference_scope_denied","message":"The requested immutable reference is not available to the active session scope.","retry_allowed":false,"details":{}},"trust":{"label":"no_verified_result","statement":"No dataset, component result, or digest-checked artifact was returned."}}
```

#### `compare_ports`

```text
PortRef = {component: ComponentRef, field: string[1..240 bytes]}

Request = {
  host_schema_version?: integer = 1,
  producer: PortRef,
  consumer: PortRef
}

CompatibilityData = {
  compatible: true,
  producer: {component: ComponentRef, field: string, port: SemanticPort},
  consumer: {component: ComponentRef, field: string, port: SemanticPort},
  differences: []
}
```

The producer field must contain a complete output `SemanticPort`; the consumer must contain a
complete input `SemanticPort`. Exact identity is checked in the worker. Compatible comparison is
`ok`; incompatibility is `refused` with `incompatible_ports` and the ordered existing
`PortDifference` objects in `error.details.differences`. There is no paging.
The response is always compact and contains no component schemas or values.

Annotations are `{readOnlyHint:true, destructiveHint:false, idempotentHint:true,
openWorldHint:false}`. It uses `structural_compatibility_only`; failures are the `compare_ports`
set in the failure fixture.

Success example:

```json
{"host_schema_version":1,"outcome":"ok","data":{"compatible":true,"producer":{"component":{"id":"dq.market_data.log_return","version":"0.1.2","subject_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"field":"returns","port":{"direction":"output","concept":"periodic_return_series","unit":"decimal","shape":"ordered_series","cardinality":"one_or_more","convention":"log_periodic_return","ordering":"preserve_source_order","frequency":"inherited","provenance_requirement":"component_bound"}},"consumer":{"component":{"id":"dq.volatility.rolling_historical_volatility","version":"0.1.2","subject_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"field":"returns","port":{"direction":"input","concept":"periodic_return_series","unit":"decimal","shape":"ordered_series","cardinality":"one_or_more","convention":"log_periodic_return","ordering":"preserve_source_order","frequency":"inherited","provenance_requirement":"not_required"}},"differences":[]},"trust":{"label":"structural_compatibility_only","statement":"Semantic-port compatibility is structural only; it neither transfers data nor authorizes execution."}}
```

Failure example:

```json
{"host_schema_version":1,"outcome":"refused","error":{"code":"incompatible_ports","message":"The selected output and input semantic ports are not compatible.","retry_allowed":false,"details":{"differences":[{"dimension":"convention","producer_value":"simple_periodic_return","consumer_value":"log_periodic_return"}]}},"trust":{"label":"structural_compatibility_only","statement":"Semantic-port compatibility is structural only; it neither transfers data nor authorizes execution."}}
```

#### `execute_component`

```text
FieldMapping = {source_field: string[1..240 bytes], input_field: string[1..240 bytes]}

NonLiteralSource =
  {kind:"dataset", ref:DatasetRef, mappings:FieldMapping[1..128]} |
  {kind:"operation", ref:OperationRef, mappings:FieldMapping[1..128]}

Request = {
  host_schema_version?: integer = 1,
  component: ComponentRef,
  literals?: {[input_field: string[1..240 bytes]]: JsonValue} = {},
  sources?: NonLiteralSource[0..128] = [],
  provenance?: ProvenanceInput,
  artifacts?: "none" | "svg_all" = "none"
}

JsonScalar = null | boolean | finite number | string
StructuredFieldSummary = {
  kind: "structured",
  value_kind: "array" | "object",
  count: integer >= 0,
  sha256: Sha256,
  page_available: true
}

OperationSummary = {
  compact: true,
  operation_ref: OperationRef,
  operation_hash: Sha256,
  manifest_sha256: Sha256,
  component: ComponentRef,
  protocol_version: ProtocolVersion,
  execution_mode: "unmanaged",
  source_context?:
    {kind:"dataset", ref:DatasetRef,
     external_preprocessing_status:"none_declared"|"unknown"|"receipt_supplied",
     normalization_event_count:integer[0..768],
     normalization_event_application_count:integer>=0} |
    {kind:"operation", ref:OperationRef},
  result: {
    [field: string]: JsonScalar | StructuredFieldSummary
  } with 1..256 fields,
  messages: {items:{kind:"warning"|"disclosure", index:integer>=0,
                    text:string}[0..20], total:integer,
             digest:Sha256, complete:boolean, next_cursor:Cursor|null},
  artifacts: {artifact_id:SafeId, media_type:string, size_bytes:integer,
              sha256:Sha256, resource_uri:string}[0..128],
  expires: "session_end"
}
```

The request can represent multiple candidate sources so the host can return the specific
`multiple_non_literal_sources` outcome. It accepts zero or one; two or more fail before any
reference resolution or component import. A dataset source is available in Phase 4. An operation
source is enabled only after protocol `0.5.0` Phase 4B; before then it returns
`unsupported_binding`. Mappings are ordered, unique by source and target field, and must not
overlap `literals`.

For a dataset, `source_field` names one normalized `field_id` and resolves to the complete
source-order column array, including null cells; that one array becomes the whole `input_field`
value. For an operation, `source_field` names one complete top-level result field in the
schema-validated, digest-checked result member; its scalar, array, or object value becomes the
whole `input_field` value. Neither
form accepts an index, key, member path, slice, join, broadcast, coercion, or transform. The target
component's canonical input model decides whether the resolved whole value is valid.

At most 64 literal fields are accepted, each canonical value is at most 64 KiB, and the complete
canonical `literals` object is at most 512 KiB. The request-wide 1 MiB ceiling still applies.

`provenance` is required when `sources` is empty and forbidden when it contains one item. Dataset
provenance is copied from the immutable record as caller-asserted provenance. Phase-4B operation
provenance is component-bound lineage derived from a schema-validated, digest-checked upstream
record as the paired component-output/component-derived `0.5.0` value. Neither authenticates a
source nor independently attests execution. The host resolves fields to actual values, constructs
the canonical `OperationRequest`, and then the component input model validates the result. No host
transform is implicit.

The compact result includes all top-level scalar and enum fields. Arrays and objects become
count/digest/page descriptors; the host computes no new financial statistics and has no
component-ID branches. `source_context` is a bounded projection of the immutable source binding;
its dataset reference lets a caller retrieve the complete normalization ledger and preprocessing
status through `describe_dataset`. A literal-only operation omits it. `messages.items` is the first
at-most-20-value slice of the same combined
ordering used by `get_operation`: warnings in stored order, then disclosures in stored order, with
an index within each kind. If the combined sequence is incomplete, `messages.complete=false` and
its one cursor points to the next combined index; the caller must retrieve every page before
presenting the calculation. A successful call writes an immutable operation record; the same
semantic request and exact bundle returns the same reference.

Structured-field and message digests are ordinary SHA-256 of
`canonical_json_bytes(field_value)` and of
`canonical_json_bytes({"warnings": warnings, "disclosures": disclosures})`, respectively. The
complete field and message values remain bound through the result member and operation record.
`execute_component` neither pages result fields nor returns result-field cursors;
`page_available=true` directs a fresh `get_operation` call with the field selector. Only an
incomplete combined message slice returns a cursor.

Annotations are `{readOnlyHint:false, destructiveHint:false, idempotentHint:true,
openWorldHint:false}`. Operational idempotence follows from exact component identities, immutable
references, hash-bound literals, and atomic content-addressed publication. Success uses
`unmanaged_execution`; failures use `no_verified_result` and the `execute_component` set in the
failure fixture.

Success example:

```json
{"host_schema_version":1,"outcome":"ok","data":{"compact":true,"operation_ref":"dqop:v1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","operation_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","manifest_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","component":{"id":"dq.market_data.simple_return","version":"0.3.4","subject_hash":"ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a"},"protocol_version":"0.4.0","execution_mode":"unmanaged","source_context":{"kind":"dataset","ref":"dqds:v1:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","external_preprocessing_status":"none_declared","normalization_event_count":0,"normalization_event_application_count":0},"result":{"component_id":"dq.market_data.simple_return","version":"0.3.4","subject_hash":"ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a","unit":"decimal","assumptions":{"kind":"structured","value_kind":"array","count":3,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"disclosures":{"kind":"structured","value_kind":"array","count":1,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"warnings":{"kind":"structured","value_kind":"array","count":0,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"transformations":{"kind":"structured","value_kind":"array","count":0,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"derivations":{"kind":"structured","value_kind":"array","count":3,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"visualizations":{"kind":"structured","value_kind":"array","count":1,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"returns":{"kind":"structured","value_kind":"array","count":3,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"return_kind":"simple","price_kind":"adjusted","return_timestamps":{"kind":"structured","value_kind":"array","count":3,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","page_available":true},"declared_frequency":"daily","ordering_status":"verified","gap_check":"not_assessed"},"messages":{"items":[{"kind":"disclosure","index":0,"text":"Calendar-aware gaps were not assessed."}],"total":1,"digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","complete":true,"next_cursor":null},"artifacts":[],"expires":"session_end"},"trust":{"label":"unmanaged_execution","statement":"Unsigned host-reconciled record for an unmanaged local operation; digests bind its request, selected subject, result, and declared artifact bytes. Data authenticity, financial approval, correctness, and independent execution are not attested."}}
```

This is the Phase-4 compatibility shape: it emits protocol `0.4.0`. After Phase 4B, new operations
emit `0.5.0`; explicit legacy `0.4.0` manifests and the fixed vector remain accepted.

Failure example:

```json
{"host_schema_version":1,"outcome":"needs_information","error":{"code":"missing_convention","message":"An answer-changing financial convention must be supplied explicitly.","retry_allowed":false,"details":{"fields":["price_kind"]}},"trust":{"label":"no_verified_result","statement":"No dataset, component result, or digest-checked artifact was returned."}}
```

#### `get_operation`

```text
Request = {
  host_schema_version?: integer = 1,
  operation_ref: OperationRef,
  view?: "summary" | "manifest" | "result_field" | "messages" | "artifact_metadata"
         = "summary",
  field?: string[1..240 bytes],
  artifact_id?: SafeId,
  cursor?: Cursor,
  limit?: integer[1..1000]
}

Page = {start: integer >= 0, returned: integer[0..1000],
        next_cursor: Cursor|null, complete: boolean}

OperationViewData =
  {compact:true, operation_ref:OperationRef, view:"summary",
   summary_sha256:Sha256, summary:OperationSummary, expires:"session_end"} |
  {compact:false, operation_ref:OperationRef, view:"manifest",
   manifest_sha256:Sha256, manifest:defined_quant_protocol.OperationManifest,
   expires:"session_end"} |
  {compact:false, operation_ref:OperationRef, view:"result_field", field:string,
   result_sha256:Sha256, field_sha256:Sha256, value_kind:"scalar", value:JsonScalar,
   expires:"session_end"} |
  {compact:false, operation_ref:OperationRef, view:"result_field", field:string,
   result_sha256:Sha256, field_sha256:Sha256, value_kind:"array",
   items:JsonValue[0..1000], page:Page, expires:"session_end"} |
  {compact:false, operation_ref:OperationRef, view:"result_field", field:string,
   result_sha256:Sha256, field_sha256:Sha256, value_kind:"object",
   entries:{key:string, value:JsonValue}[0..1000], page:Page, expires:"session_end"} |
  {compact:false, operation_ref:OperationRef, view:"messages", result_sha256:Sha256,
   messages_sha256:Sha256,
   messages:{kind:"warning"|"disclosure", index:integer>=0, text:string}[0..100],
   page:Page, expires:"session_end"} |
  {compact:true, operation_ref:OperationRef, view:"artifact_metadata",
   manifest_sha256:Sha256,
   artifact:{artifact_id:SafeId, media_type:string, size_bytes:integer>=0,
             sha256:Sha256, resource_uri:string}, expires:"session_end"}
```

`summary` is the compact default and returns the same complete summary shape as execution.
`manifest` returns the existing closed `OperationManifest`. Those two views forbid `field`,
`artifact_id`, `cursor`, and `limit`. `result_field` requires `field` and forbids `artifact_id`; an
array or object accepts paging, while a scalar forbids `cursor` and `limit`. `messages` forbids both
selectors and accepts paging. `artifact_metadata` requires `artifact_id`, forbids `field`, `cursor`,
and `limit`, and returns metadata plus the scoped resource URI, never bytes. Any other selector
combination is `invalid_tool_request`; no view accepts a member path.

Array pages preserve value order. Object pages sort top-level keys by UTF-8 bytes and return
`entries`; nested values are not flattened. Message pages contain warnings in stored order, then
disclosures in stored order, each with its within-kind index. A cursor binds the operation digest,
view, selector, next index, and session. A result page defaults to 100 and permits at most 1,000; a
message page defaults to 20 and permits at most 100. A message request with `limit>100` is
`invalid_tool_request`, never clamped. A response may return fewer items to honor 256 KiB.

`manifest_sha256` is ordinary SHA-256 of the exact existing portable `manifest.json` member bytes
using `defined-quant-pretty-json-v1` below and equals the operation-record binding.
`result_sha256` is checked against the digest declared by `manifest.result`.
`field_sha256`, `messages_sha256`, and `summary_sha256` are ordinary SHA-256 of
`canonical_json_bytes` over the complete field, the complete `{warnings,disclosures}` object, and
the complete summary. Page slicing never changes those complete-value digests.

Every view is a projection of the authoritative operation record, manifest, and declared members.
No view creates another calculation receipt, record, reference, or semantic hash root.

Annotations are `{readOnlyHint:true, destructiveHint:false, idempotentHint:true,
openWorldHint:false}`. Success uses `unmanaged_execution`; failures use `no_verified_result` and the
`get_operation` set in the failure fixture.

Success example:

```json
{"host_schema_version":1,"outcome":"ok","data":{"compact":false,"operation_ref":"dqop:v1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","view":"result_field","field":"returns","result_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","field_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","value_kind":"array","items":[0.01,-0.02],"page":{"start":0,"returned":2,"next_cursor":null,"complete":true},"expires":"session_end"},"trust":{"label":"unmanaged_execution","statement":"Unsigned host-reconciled record for an unmanaged local operation; digests bind its request, selected subject, result, and declared artifact bytes. Data authenticity, financial approval, correctness, and independent execution are not attested."}}
```

Failure example:

```json
{"host_schema_version":1,"outcome":"failed","error":{"code":"record_corrupt","message":"The immutable stored record failed schema or digest verification and was refused.","retry_allowed":false,"details":{}},"trust":{"label":"no_verified_result","statement":"No dataset, component result, or digest-checked artifact was returned."}}
```

### 4.3 Scoped `dqop://` resource

The server exposes exactly one read-only resource template:

```text
dqop://v1/{operation_sha256}/artifact/{artifact_id}
```

`operation_sha256` is 64 lowercase hex characters and `artifact_id` is `SafeId`. There is no query
string, fragment, arbitrary path, directory, result-member, or dataset resource. The handler checks
session scope, validates the operation record and manifest, resolves only a declared artifact ID,
verifies its digest and media type, and returns one MCP `BlobResourceContents`:

```text
{
  uri: exact requested URI,
  mimeType: manifest-declared media type,
  blob: base64 of exact artifact bytes,
  _meta: {sha256: Sha256, size_bytes: integer, execution_mode: "unmanaged",
          trust: TrustNotice}
}
```

The raw artifact is at most 5 MiB and the complete base64 MCP response frame is at most 8 MiB.
Larger artifacts fail rather than stream. Resource reads do not support paging. Because MCP
resource reads do not carry the tool outcome envelope, failures are MCP resource errors whose
code is `-32002`, message is the fixture's fixed redacted message, and data is exactly
`{host_schema_version:1,outcome,host_failure_code,retry_allowed,details,trust}`. The host code
belongs to the `dqop_resource` set in the failure fixture, and outcome/retry/details obey that same
definition.
Within the `dqop` scheme, a URI that fails the exact template syntax uses that set's
`invalid_tool_request`; a different scheme is MCP error `-32002` with fixed message
`Resource URI is not available.` and no data. An unknown MCP method remains standard `-32601`
`Method not found`. The resource handler never redirects or falls back to a filesystem read.

The resource is side-effect-free and session-scoped. Returned artifact bytes are digest-checked
against the validated manifest and use
`unmanaged_execution`; failure uses `no_verified_result`. A URI is an identifier, not
authorization, and expires when the server session ends. The template annotation is
`{audience:["assistant"],priority:0.5}` with no `lastModified`; MCP resource annotations have no
tool-style read-only/destructive fields, so read-only behavior is inherent in `resources/read` and
enforced by the absence of any resource mutation method.

Success example:

```json
{"contents":[{"uri":"dqop://v1/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifact/returns_chart","mimeType":"image/svg+xml","blob":"PHN2Zy8+","_meta":{"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","size_bytes":6,"execution_mode":"unmanaged","trust":{"label":"unmanaged_execution","statement":"Unsigned host-reconciled record for an unmanaged local operation; digests bind its request, selected subject, result, and declared artifact bytes. Data authenticity, financial approval, correctness, and independent execution are not attested."}}}]}
```

Failure example:

```json
{"code":-32002,"message":"The requested artifact is not declared by the validated operation manifest.","data":{"host_schema_version":1,"outcome":"failed","host_failure_code":"artifact_not_found","retry_allowed":false,"details":{"artifact_id":"returns_chart"},"trust":{"label":"no_verified_result","statement":"No dataset, component result, or digest-checked artifact was returned."}}}
```

## 5. References, canonicalization, and storage

The only alpha reference forms are:

```text
dqds:v1:<sha256>
dqop:v1:<sha256>
```

The digest is lowercase SHA-256 over the existing `dq-tagged-json-v1` canonicalizer with exact
framing:

```text
sha256(
  utf8("defined-quant") || 0x00 ||
  ascii("dq-tagged-json-v1") || 0x00 ||
  ascii(domain) || 0x00 ||
  canonical_json_bytes(value)
)
```

Object keys sort by UTF-8 bytes. Types are tagged so strings, numbers, and booleans cannot collide;
numbers are encoded as big-endian IEEE-754 binary64 hex after negative zero normalization. Values
with non-finite numbers, unsafe integers, lone surrogates, non-string object keys, duplicate text-
JSON keys, or unsupported types are refused.

The domains are `mcp.dataset.payload.v1` for a normalized table member, `mcp.dataset.v1` for the
immutable dataset record, and `mcp.operation.v1` for the immutable successful host operation
record. Reusing a domain for another record type is forbidden. Raw file/source bytes and manifest
members use ordinary byte SHA-256 and are explicitly labelled as such. The normative records,
canonical bytes, domain-separation checks, digests, and reference strings are in
[`hash_vectors.v1.json`](local_mcp/hash_vectors.v1.json); Phase 3 tests must reproduce every vector
on supported platforms without importing the MCP SDK.

The linked dataset vector accepts two timestamp spellings that normalize to canonical UTC, binds
the canonical structured-input digest, records a two-cell `timestamp_to_canonical_utc` event, and
then feeds its recomputed `dqds` into the operation vector. This prevents an implementation from
hashing the new ledger fields without actually reconciling them to the normalized payload: the
Phase-3 test must pass the byte-vector value through the normalizer and reproduce the payload,
event, dataset record, `dqds`, operation record, and `dqop` chain.

`defined-quant-pretty-json-v1` preserves Phase-1 CLI bytes: UTF-8 JSON from
`json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)` followed by one
LF byte. It is used only for existing portable `input.json`, `result.json`, and `manifest.json`
members; their digests are ordinary SHA-256 of those exact bytes, not domain-separated tagged
hashes. The fixture includes a fixed serialization/digest smoke vector.

The hash projections are closed and versioned independently from their transport models:

```text
CanonicalCell = null | boolean | finite IEEE-754-safe number | string

DatasetPayloadV1 = {
  schema_version: 1,
  serialization: "dq-table-v1",
  columns: {field_id:SafeId, data_type:Column.data_type}[1..128],
  rows: (CanonicalCell[1..128])[1..250000]
}

DatasetRecordV1 = {
  host_schema_version: 1,
  record_kind: "dataset",
  payload: {serialization:"dq-table-v1", sha256:Sha256},
  raw_source_sha256: Sha256,
  source:
    {kind:"inline_rows", provenance:ProvenanceInput-with-defaults-materialized} |
    {kind:"inline_json", provenance:ProvenanceInput-with-defaults-materialized} |
    {kind:"local_file", format:"csv", csv:Source.csv-with-defaults-materialized,
     provenance:ProvenanceInput-with-defaults-materialized} |
    {kind:"local_file", format:"json",
     provenance:ProvenanceInput-with-defaults-materialized},
  external_preprocessing: ExternalPreprocessing-with-defaults-materialized,
  columns: {source_name:string, field_id:SafeId, data_type:Column.data_type,
            role:Column.role, semantic_port:SemanticPort|null}[1..128],
  semantics: DatasetSemantics,
  row_count: integer[1..250000],
  column_count: integer[1..128],
  observation_bounds: {first:string, last:string}|null,
  normalization_events: NormalizationEvent[0..768],
  findings: DatasetRegistrationData.findings,
  normalizer: {name:SafeId, version:SemVer}
}

OperationMemberV1 = {
  kind:"normalized_input"|"result"|"artifact", path:RelativeMemberPath[1..512 bytes],
  sha256:Sha256, size_bytes:integer[0..134217728]
}

OperationRecordV1 = {
  host_schema_version: 1,
  record_kind: "operation",
  host_binding_version: 1,
  execution_mode: "unmanaged",
  protocol_version: ProtocolVersion,
  component: ComponentRef,
  operation_hash: Sha256,
  manifest_sha256: Sha256,
  members: OperationMemberV1[2..130],
  runner: {name:SafeId, version:SemVer},
  renderers: {name:SafeId, version:SemVer}[0..128],
  binding: {
    literals: {[input_field:string]:JsonValue},
    source?:
      {kind:"dataset", ref:DatasetRef, mappings:FieldMapping[1..128]} |
      {kind:"operation", ref:OperationRef, mappings:FieldMapping[1..128]}
  },
  compatibility: defined_quant_protocol.PortCompatibility[0..128]
}
```

Normalization preserves request column order and source row order. Every payload row has exactly
the declared width; an absent cell becomes `null`, but arrays and objects are never cells. Integer
and number cells obey the shared finite/safe-number rules. Timestamp cells are RFC 3339 strings
normalized to UTC with `Z`; string, boolean, and null cells are unchanged. The payload contains no
source names, roles, semantic metadata, findings, or provenance.

The accepted timestamp grammar is uppercase
`YYYY-MM-DDTHH:MM:SS[.fraction](Z|+HH:MM|-HH:MM)` with a valid Gregorian date, seconds present,
one-to-six fractional digits when a fraction is present, and a valid numeric offset. Leap seconds,
`24:00`, named/local zones, lowercase `t`/`z`, more than microsecond precision, and offset-free
values are refused. Conversion is exact, never rounded. The canonical UTC form is
`YYYY-MM-DDTHH:MM:SS[.fraction]Z`, with trailing fractional zeros removed and the fraction omitted
when zero.

For `inline_rows` and decoded inline/file JSON, null remains null and every non-null value must
already have the declared JSON type: `number` accepts an integer or number but not boolean;
`integer` accepts only a safe integer; `string` and `boolean` are strict; and `timestamp` accepts
only an RFC 3339 string with an explicit offset. There is no string-to-number or scalar-to-string
coercion. CSV rows must match the header width. An empty CSV field is null for non-string columns
and the empty string for `string`; other values use JSON number/integer grammar, exact lowercase
`true` or `false`, exact decoded text, or offset-bearing RFC 3339 respectively. Parsing must reject
overflow, non-finite values, ambiguous local timestamps, and trailing characters.

Records materialize provenance and external-preprocessing defaults, empty arrays, and nullable
`semantic_port` fields exactly as shown; absent optional semantic declarations are omitted rather
than written as null. Dataset
columns have unique `field_id` and `source_name` values and at most one column with
`role:"timestamp"`. `observation_bounds` uses that one normalized timestamp column and is null
when no such column exists. On every schema-validated, digest-checked read, `row_count`,
`column_count`, payload column order/types, and every row width must cross-match the loaded
payload. Normalization events are unique and sort by `(code,field_id)` UTF-8 bytes; warning
findings sort by code UTF-8 bytes.

An operation has exactly one `normalized_input` member, exactly one `result` member, and zero to
128 `artifact` members. All member paths are unique and each member must equal the corresponding
entry in the validated `OperationManifest`; duplicate artifact identifiers are refused by that
manifest. Operation members sort normalized input, result, then artifacts by path UTF-8 bytes.
Renderers are unique by `(name,version)`, derive only from declared artifacts, and sort by that
pair's UTF-8 bytes. Mappings have unique target input fields and preserve request order.
Compatibility entries are bounded to one result for each operation-source mapping, in mapping
order; dataset and literal bindings use an empty list. A literal-only operation omits
`binding.source`. Neither projection permits another field, a wall-clock value, session ID, path
supplied by the caller, mutable status, or host exception text.

A dataset record binds its normalized-payload digest and serialization version; raw-source digest;
source kind and exact caller-supplied provenance with defaults materialized and
`Source.local_file.path` omitted; external-preprocessing status and optional receipt binding;
column mapping and semantic ports; instrument, unit/currency, frequency, timezone, ordering,
adjustment, distribution, calendar, and partial-period declarations; row/column counts and
observation bounds; the closed normalization-event ledger; quality findings; and normalizer
identity/version. Registration time and session identity are operational metadata and are excluded
from the digest.

An operation record binds the canonical `OperationRequest.operation_hash`; complete manifest and
member digests; runner and renderer identities; exact literals; either the wire `sources[0]`
dataset as one internal `binding.source` with its `dqds` plus every
ordered dataset mapping or, after Phase 4B, the source `dqop` plus every ordered output mapping and
port-compatibility result; and host schema/binding versions. It records no wall-clock time.

`OperationRecordV1`, its exact manifest, and its declared members are the sole authoritative alpha
calculation-receipt evidence set. The dataset `dqds` in its binding transitively commits to the
normalization ledger and external-preprocessing status; the operation record never copies those
facts. Summaries, pages, resource metadata, and future receipt cards are projections and create no
new record, reference, or semantic hash root.

Publication is staged and atomic with no replacement. Equal canonical records yield the same
reference; an existing equal record is reused only after its bytes, schema, and digest verify.
The stored record serialization is `defined-quant-cas-json-v1`: strict UTF-8 JSON with keys sorted,
`(',', ':')` separators, `ensure_ascii=False`, `allow_nan=False`, and one trailing LF. On read the
controller first enforces the record byte ceiling, then decodes strict UTF-8, rejects duplicate
keys, parses with non-finite values disabled, validates the closed record schema and all cross-
record invariants, recomputes the tagged canonical projection and domain-separated digest, and
only then trusts or returns it. Records and referenced members are immutable. A digest mismatch,
missing member, member-digest mismatch, invalid record schema, or failed cross-check is
`record_corrupt`; the entry is quarantined and never repaired, overwritten, or partially returned.

References are valid only in the server session that registered or produced them. Scope is checked
separately from content identity and is not hashed. On session end every reference expires and the
private store is removed. Persistent storage, cross-session reopening, migrations, and garbage
collection are deferred.

## 6. Failure and redaction contract

[`host_failures.v1.json`](local_mcp/host_failures.v1.json) is the one authoritative closed
vocabulary. Future tests must compare its `operation_error_mappings[].operation_error_code` set to
the live `OperationErrorCode` enum and fail if either side has an unmapped value. Conditional
mapping is allowed only for `component_refused`: a safe nested component code identifying missing
or ambiguous input maps to `missing_convention`; all other component refusals remain
`component_refused`.

The fixture also freezes each mapping's detail projection. `field_name` means only an exact field
declared by the selected closed tool schema or verified component model; raw validation locations,
unknown or extra request keys, and invalid or overlong names are omitted. Field projections
preserve source order, first-occurrence deduplicate, cap at 32, and safely fall back to `[]`.
Required component IDs come from the already validated operation request, never exception text;
if one is unavailable or invalid, mapping fails closed to `internal_failure`. No implementation
may copy an arbitrary `OperationError.details` value into a host response.

Host-only failures retain distinct codes for invalid tool/component input, component contract
failure, output validation, component identity/subject mismatch, unsupported host or protocol
version, timeout, cancellation, worker crash or occupied capacity, output limit, record corruption, input-root or
reference-scope refusal, and internal failure. No catch-all may erase those distinctions before
the mapping is applied.

`retry_allowed` never authorizes an automatic retry. It says whether a caller may retry after the
fixture's stated condition. Unchanged invalid input, identity, contract, output-validation,
corruption, access, and internal failures are not retryable. Timeout or crash may be tried again
only under the fixture's condition; missing information requires a new corrected request rather
than a retry; capacity failures require a state change.

Messages are selected from the fixture, not exception text. Model-visible responses and normal
logs never include tracebacks, exception representations, secrets, cookies, tokens, environment
values, raw input cells, raw files, provider bodies, absolute paths, or cache paths.
`reference_not_found` is used only when the active-session index definitively has no entry;
`reference_scope_denied` preserves the separate access/scope refusal. `details` contains only field
names, safe component IDs, counts, bounded semantic-port differences, and other values explicitly
allowed by the failure fixture.

## 7. Security and operational limits

### 7.1 Closed limits

| Boundary | Default | Hard alpha maximum |
|---|---:|---:|
| Raw STDIO request frame, including LF | 2 MiB | 2 MiB |
| Tool arguments | 1 MiB | 1 MiB |
| Resolved worker request | 2 MiB | 2 MiB |
| Worker control response | 4 MiB | 4 MiB |
| Inline JSON source | 512 KiB | 512 KiB |
| Configured local-file roots | 8 | 8 |
| Local CSV/JSON file | 64 MiB | 64 MiB |
| Normalized dataset payload | 128 MiB | 128 MiB |
| CAS record document | 2 MiB | 2 MiB |
| Dataset rows | 250,000 | 250,000 |
| Dataset columns | 128 | 128 |
| One decoded cell | 64 KiB | 64 KiB |
| Indexed component records | current catalog: 7 | 10,000 |
| Search candidates considered per query | unbounded today | 500 specified; enforcement deferred |
| Expected retained index memory per record | about 29 KB | observational planning value; not enforced |
| Search response | 64 KiB | 64 KiB |
| Other structured tool response | 256 KiB | 256 KiB |
| Initialization and list response frame | 256 KiB | 256 KiB |
| Search hits / values per facet | 5 / 10 | 5 / 10 |
| Dataset preview page | 20 rows | 50 rows |
| Result page | 100 values | 1,000 values |
| Message page | 20 messages | 100 messages |
| Decoded artifact resource | 5 MiB | 5 MiB |
| Resource response frame | 8 MiB | 8 MiB |
| Normalized result member | 64 MiB | 64 MiB |
| Result top-level fields | 256 | 256 |
| Artifacts per operation | 128 | 128 |
| Complete operation bundle | 128 MiB | 128 MiB |
| Session store | 1 GiB | 1 GiB |
| Concurrent workers | 2 | 4 |
| Pending worker queue | 0 | 0 |
| Worker wall time | 60 seconds | 60 seconds |
| Grace before forced kill | 5 seconds | 5 seconds |
| Captured worker stdout / stderr | 1 MiB each | 1 MiB each |
| Worker memory | 512 MiB | 512 MiB |
| Orphan-session age before eligible cleanup | 24 hours | 24 hours |

During registration, a normalized dataset payload above 128 MiB is
`input_limit_exceeded` with `limit_name:"normalized_dataset_payload_bytes"`. A newly serialized
dataset or operation CAS record above 2 MiB is `record_publication_failed` and no reference is
published; an existing stored record above that ceiling is `record_corrupt`. Normalized result,
artifact, complete-bundle, and structured-response overflow is `result_limit_exceeded` with the
corresponding stable `limit_name`. Session quota exhaustion remains `cache_full`, and captured-
stream overflow remains `worker_resource_limit`. These byte checks precede publication.
The controller checks a resolved worker request before process launch; a request above 2 MiB is
`input_limit_exceeded` with `limit_name:"worker_request_bytes"`. The child repeats that ceiling as
defence in depth. A worker control response above 4 MiB is `result_limit_exceeded` with
`limit_name:"worker_control_response_bytes"`; it is never reported as `worker_crashed` merely
because the complete typed response could not fit the bounded channel.
The only permitted input/result `limit_name` values are the enums in
`host_failures.v1.json`; the field is not free-form. Tool-argument bytes are counted before the
closed request model runs, so every tool surface can return `input_limit_exceeded` for that one
pre-validation boundary.
Initialization and list payloads are serialized and checked before the server advertises
readiness; exceeding their static frame ceiling fails startup with no partial STDIO session.

Every byte ceiling is measured over the same explicitly encoded or raw byte sequence on Windows,
macOS, and Linux. STDIO byte counts include the received LF and occur before text decoding; no
platform newline translation, locale encoding, console code page, or filesystem encoding may alter
a counted or hashed value. For the same request, fixed catalog, fixed session secret where a cursor
is present, and identical source bytes, all three platforms must produce byte-identical canonical
dataset payloads, dataset records, operation records, normalized inputs, normalized results,
manifests, rendered artifacts, CAS records, hashes, digests, references, cursors, failure codes,
failure messages and details, trust wording, response envelopes, resource projections, and CLI
STDOUT/STDERR. In particular, every vector in `hash_vectors.v1.json` and every surface in
`operation_runtime_phase0.v1.json` must reproduce exactly on all three platforms.

Platform mechanisms may differ only behind one transport-neutral host abstraction. POSIX modes and
Windows DACLs, `openat` containment and Windows handle containment, `flock` and `LockFileEx`, native
atomic no-replace publication primitives, and POSIX process groups and Windows Job Objects may use
different system calls. They must expose the same success conditions, stable closed failures, and
cleanup outcome without leaking native paths, error text, numeric OS errors, security identifiers,
or platform names into a canonical value or public response.

The indexed-record ceiling is enforced once, while constructing the immutable process-lifetime
snapshot; an oversized catalog fails startup before a session exists and never fails midway
through that session. The 500-candidate search bound is a frozen controller requirement, but it
is intentionally not enforced in the alpha index yet; enforcement lands when catalog growth
warrants it. Until then a broad query can consider the complete accepted index. No search path
uses a wall-clock deadline: elapsed-time outcomes would vary by machine and violate deterministic
discovery.

Controller measurements are diagnostic observations, not limits, deadlines, or CI thresholds.
For the current seven-component catalog, idle RSS was about 41 MB, cold start about 107 ms, and a
search 1–2 ms. For the synthetic 10,000-component catalog, index construction was about 3.9 s
and retained about 290 MB (about 29 KB per record); selective search was about 0.3 ms, while a
broad search matching every record cost about 0.17–0.27 ms per candidate, or roughly 1.7–2.7 s.
The per-record memory observation is the reported retained index RSS divided by its 10,000
records. These figures were recorded from the diagnostic measurement run and are not re-derived
by tests.

Pages are stable source-order slices. An opaque cursor binds record digest, view, the
domain-separated SHA-256 digest of any selector, next index, and session; selector text is not
embedded. Changing any bound field refuses the cursor. Byte limits take precedence over
requested counts, so a page may be shorter. A single value too large to fit is
`result_limit_exceeded`, not truncated.

### 7.2 Files and session state

Local-file registration is disabled unless launch configuration contains at least one existing
absolute root. Roots, catalog location, and session-state base directory are launch-only and can
never be tool arguments. The server refuses `~`, environment or glob expansion, relative paths,
`..`, archives, devices, sockets, FIFOs, and symlinked or reparse-point intermediate or final path
components. It accepts a regular CSV/JSON file only after handle-based containment checks against
an opened configured root. POSIX uses `openat`/`O_NOFOLLOW`-equivalent traversal plus `fstat`.
Windows uses native handles to refuse every intermediate and final reparse point, validate the
final handle path, volume identity, and regular-file type beneath the pinned root, and prevent
handle inheritance. Windows path checks use Unicode native APIs and support Unicode local paths and
local paths longer than 260 characters without locale, code-page, short-name, or separator changes
to semantic or hashed values. UNC paths, device namespaces, mapped network drives, and other remote
or network filesystem roots fail startup closed for the alpha; they are not silently normalized to
or treated as local configured roots or session-state locations.
The transport-neutral facade now routes configured-root reads, private session state, locking,
atomic no-replace publication, and cleanup through the selected Windows, macOS, or Linux provider.
The in-tree Windows provider implements the owner-only DACL, reparse-point, handle-containment,
locking, atomic-publication, and tombstone-cleanup mechanisms below. This implementation status is
not release support; native Windows acceptance and the complete six-cell matrix remain mandatory.

Files are hashed and size-limited while streaming. The dedicated `Source.local_file.path` value
never enters canonical records, responses, resource URIs, or logs. This is not generic free-text
redaction: caller-authored provenance is stored and hash-bound verbatim and may appear in an
explicit manifest view, so callers must not put secrets, credentials, or local paths there. Raw
bytes are discarded after normalization.

The session directory is created beneath a fixed application temporary root with `0700`
directories and `0600` files. Windows creates and verifies a protected, non-inheriting, owner-only
DACL for the corresponding application root, session root, directories, records, members, staging
files, lock files, quarantine entries, and cleanup markers before exposing any of them. The owner
must be the current token's user SID; a wrong owner, inherited ACE, additional principal, replaced
descriptor, or reparse point fails closed. The store refuses a symlinked, reparse-point, wrongly
owned, or group/world-accessible root. Quota is checked before and during staged writes. A full store
returns `cache_full` and never evicts a live reference. Clean shutdown removes only the exact
active marked directory. Startup cleanup may remove only marker-bearing, correctly owned,
unlocked application session directories older than 24 hours; it never recursively cleans a
caller-supplied path.

Record and bundle publication is atomic and no-replace under concurrent writers. The Windows
implementation uses a handle-based same-volume NTFS operation with replacement disabled and proves
by native contention and forced-termination tests that a destination is either absent or complete;
`MoveFileEx` replacement, `ReplaceFile`, copy/delete, and overwrite fallbacks are forbidden.
Portable member paths reject case-fold collisions, reserved Windows device segments, alternate data
streams, trailing dots, and trailing spaces on every platform before touching disk.

Live-session locking on Windows uses a `LockFileEx`-compatible exclusive byte-range lock held for
the complete live interval. Cleanup acquires the same lock non-blockingly and atomically claims an
eligible tree before deletion. Windows open-file rename/delete behavior must not require releasing
the cleanup claim before the tree is made unreachable. Concurrent startup, shutdown, publication,
and cleanup tests must prove that a live, foreign, replaced, or newly locked session is never
removed.

### 7.3 Worker boundary

Every selected inspection or execution starts in a bounded native process tree with unrelated
descriptors or handles closed and a private working/staging directory. macOS and Linux use a POSIX
process group. Windows uses an in-tree kill-on-close Job Object provider with job-wide memory,
timeout, cancellation, descendant, and forced-cleanup controls. The worker mechanism is a
subprocess with an import-safe entry point, not `multiprocessing`; Windows creates it suspended,
assigns it to the configured Job Object before user code can run, and then resumes it. Native
acceptance must prove those mechanisms before release. The controller sends one strict typed
request and receives one control response.
Worker staging is never created directly as an unmanaged home-directory or drive-root entry. Each
workspace is a child of a fixed owner-private worker-state namespace at the selected existing
output parent, with the same marker, held lock, age check, atomic cleanup claim, and tombstone
deletion protocol as other session state. Normal completion removes the live session; process
loss leaves a locked-session orphan that a later controller can claim only after the frozen orphan
age.
Component stdout and stderr are captured separately, capped, and never enter the MCP wire. Either
capture exceeding 1 MiB terminates the worker and returns `worker_resource_limit`.
Any non-empty capture below that ceiling is still a `component_contract_error`: installed
components may communicate results, warnings, and disclosures only through their typed output.
Captured text is discarded and is never attached to a response or audit record. The constructed
environment sets `PYTHONWARNINGS=error`, so interpreter warning-filter defaults cannot turn the
same component into success on one host and untyped stderr on another.
Worker admission uses a non-blocking semaphore at the configured concurrency value; there is no
hidden server-side queue. A request above that limit returns `worker_capacity` before a process or
staging directory is created.

The controller creates the environment from an allowlist instead of copying the parent. It sets a
private `TMPDIR`/`TEMP`/`TMP`, `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONNOUSERSITE=1`, `PYTHONHASHSEED=0`, `NO_COLOR=1`, and `TZ=UTC`.
It also sets `PYTHONWARNINGS=error` so warning behavior is explicit and independent of interpreter
defaults.
No platform environment is copied wholesale. Windows additionally allows only the required
`SYSTEMROOT` value after validating it at startup. Home directories,
proxy settings, cloud and provider credentials, API keys, tokens,
cookies, user-site settings, and unrelated variables are absent.

Windows process creation allowlists inherited handles as well as environment variables. Only the
exact worker control and redirected STDIO handles may be temporarily inheritable; session, root,
record, lock, job, token, unrelated pipe, and controller handles are absent from the child. The
controller retains the Job Object handle until every cleanup obligation is complete.

POSIX starts the child with the private workspace as its current directory. Windows process
creation uses the validated local `SYSTEMROOT` as its bootstrap current directory because the
Win32 `lpCurrentDirectory` field retains a `MAX_PATH` ceiling even when all workspace I/O is
handle-relative and long-path safe. Components have no supported relative-filesystem contract;
their temporary paths are supplied through the constructed environment and operation publication
uses absolute internal paths. This launcher mechanism difference cannot alter typed outputs.

The launcher enforces the 512 MiB worker memory ceiling with a tested OS mechanism and an
independent controller-side usage monitor; exceeding the memory or captured-stream ceiling kills
the process tree and is `worker_resource_limit`. The server fails startup on a platform where the
memory ceiling or complete process-tree termination cannot be installed and tested; there is no
unbounded fallback. Timeout, client cancellation, crash, captured-
stream overflow, memory overflow, and controller shutdown all follow
the same cleanup sequence: stop accepting output, terminate the complete process tree, wait no
more than five seconds, force kill survivors, close pipes, discard staging, release leases, then
emit the mapped outcome. No partial bundle or reference is published.

The worker is a bounded process boundary, **not an OS security sandbox**. Installed component code
still runs with the local user's operating-system permissions and is trusted as part of the exact
installed Defined Quant release. The alpha exposes no network/provider tool, but it does not claim
that component code is network-isolated. Malicious installed component code requires a separate
container or OS-sandbox design.

### 7.4 Logging and model egress

STDOUT is reserved for MCP frames; JSON-line operational logs go to STDERR. The server explicitly
removes the SDK's default OpenTelemetry middleware, configures no exporter, and scrubs every
`OTEL_*` variable through the environment allowlist; telemetry is off. Logs contain a random
request ID, tool name, safe component ID, outcome code, elapsed
milliseconds, and byte/count metrics only. The redaction contract in section 6 applies before any
log record is emitted.

“Local” describes the process, transport, and session store. Tool and resource results can enter
client/model context. Metadata is the default; dataset previews, result pages, and artifacts
require explicit calls and remain bounded. Raw source bytes are never returned.

## 8. Official Python MCP SDK and transport

Phase 4 selects the official Python SDK distribution `mcp==2.0.0`, the latest stable release at
the spike date (2026-08-14). It is MIT-licensed, requires Python `>=3.10`, and classifies Python
3.10 through 3.14; Defined Quant's Python 3.11–3.13 range is covered. The tagged SDK supports the
MCP `2026-07-28` revision and prior negotiated revisions. Sources: the official
[`latest release`](https://github.com/modelcontextprotocol/python-sdk/releases/latest),
[`v2.0.0` release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0),
[`pyproject.toml`](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/pyproject.toml),
and [MIT license](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v2.0.0/LICENSE).

The direct runtime requirements declared by that tag are:

```text
anyio>=4.9 (<3.14) or >=4.10 (3.14+); httpx2>=2.5.0; jsonschema>=4.20.0;
mcp-types==2.0.0; opentelemetry-api>=1.28.0; pydantic>=2.12.0;
pyjwt[crypto]>=2.10.1; python-multipart>=0.0.9; sse-starlette>=3.0.0;
starlette>=0.27 (<3.14) or >=0.48 (3.14+); typing-extensions>=4.13.0;
typing-inspection>=0.4.1; uvicorn>=0.31.1 except Emscripten; pywin32>=311 on Windows.
```

Relevant transitive families to review and lock are `httpcore2`, `h11`, `truststore`, and `idna`
for HTTP; `attrs`, `jsonschema-specifications`, `referencing`, and `rpds-py` for schema processing;
`annotated-types` and `pydantic-core`; `cryptography`, `cffi`, and `pycparser`; and `click`. Plain
`mcp` still brings HTTP, SSE, auth, telemetry API, and ASGI dependency
families even though this product invokes only STDIO. The `[cli]` extra is excluded because its
Typer and python-dotenv dependencies are unnecessary. The transitive inventory was checked against
the tagged [upstream lock](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/uv.lock).

The exploratory server spike used the high-level `MCPServer` API; the client used
`StdioServerParameters`, `stdio_client`, and the high-level `Client`, then called `list_tools()`,
`call_tool()`, and `read_resource()`. See the official
[server run documentation](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/docs/run/index.md)
and [client transport documentation](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/docs/client/transports.md).

Before transport implementation, a minimal uncommitted interoperability spike ran the official
client against a real subprocess STDIO server with `mcp==2.0.0` on macOS arm64 and Python 3.13.13.
It negotiated protocol
`2026-07-28`; listed the synthetic annotated tool; returned structured
`{"value":"alpha"}` with `is_error == false`; read a deterministic `dqop://` resource; exited 0;
and cleaned up the subprocess. No repository file or dependency was changed.

The committed Phase-4 transport replaces that spike and uses the official low-level
`mcp.server.lowlevel.Server`, not high-level decorators. The constructor registers exactly
`on_list_tools`, `on_call_tool`, `on_list_resources`,
`on_list_resource_templates`, and `on_read_resource`. `on_list_resources` returns an empty list;
it is still required because v2.0.0 advertises the resources capability only when that handler is
present. The other list handlers return exactly the seven frozen tools and one resource template.
Startup uses the tagged
[`stdio_server()` and `Server.run(...)` shape](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/src/mcp/server/lowlevel/server.py#L20-L29),
with `server.create_initialization_options()` and `raise_exceptions=False`. The official
[low-level callbacks](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/src/mcp/server/lowlevel/server.py#L117-L194)
return full `mcp.types` result objects; no high-level output or resource wrapping is used.

The SDK's stock STDIO reader buffers a complete line before JSON parsing, so `server.py` supplies
`stdio_server(stdin=...)` with a project-owned bounded async input stream. It reads the raw STDIN
file descriptor in chunks of at most 64 KiB, permits exactly one strict-UTF-8 JSON-RPC frame per
LF, and buffers at most 2 MiB including that LF. An invalid UTF-8 frame, an unterminated frame that
exceeds the ceiling, or any overlong frame terminates the session without a response (the request
ID is not parsed), emits only a fixed redacted transport code, and runs normal worker/store
cleanup. This is the ingress ceiling before SDK JSON parsing; it is independent of the stricter
1 MiB compact `arguments` ceiling.

On Windows the server places STDIN, STDOUT, and STDERR in binary mode before the first read or
write. LF and CRLF request framing must parse independently without CRT newline conversion; output
uses exact UTF-8 bytes and LF only, and byte `0x1a` is never treated as end-of-file. Native tests
cover split UTF-8 code points, invalid UTF-8, exact and over-limit raw frames, controller shutdown,
and the rule that STDOUT contains MCP frames only.

The advertised tool input schemas do not validate `CallToolRequestParams.arguments`. After SDK
JSON-RPC parsing and before service dispatch, `server.py` rejects an unknown tool name first, then
enforces the argument-byte limit and the project-owned strict closed request model for a known
tool, then maps all validation and service
exceptions to the frozen safe envelope. It also validates and size-checks every result. Resource
errors are constructed only from the fixed failure fixture. Raw validation text, `MCPError` data,
or unexpected exception text is never forwarded. Before serving, `server.py` installs the sole
STDERR handler for already-redacted `defined_quant_mcp.audit` records and disables handlers and
propagation for the SDK namespace; SDK exception logging is discarded. A test injects an
unexpected handler exception and asserts that neither wire output nor STDERR contains its text,
class, traceback, or path. This follows the official warning that
[nothing is checked for a low-level handler](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/docs/advanced/low-level-server.md#nothing-is-checked-for-you).

Immediately after construction, `server.py` removes every `OpenTelemetryMiddleware` instance from
`server.middleware`, configures no exporter, and tests that the pinned SDK leaves none installed.
The private middleware import and this pin-specific assertion remain isolated in `server.py`; see
the official [telemetry opt-out](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v2.0.0/docs/run/opentelemetry.md).

All imports and conversions involving `mcp`, `mcp.types`, low-level handlers, MCP annotations and
content/resource types, telemetry opt-out, and STDIO startup stay in `server.py`. The separate MCP
package pins exactly `mcp==2.0.0` and commits its platform-complete hashed lock; it
does not declare `mcp-types` separately.
An SDK upgrade is a dedicated dependency change that updates pin and lock together, reviews the
full platform-marked dependency/licence/hash diff, and reruns official-client tool, resource,
failure, annotation, cancellation, and cleanup tests on Windows, macOS, and Linux at Python 3.11
and 3.13. All three platforms are required lanes; Windows is neither allowed to fail nor permitted
to skip the public-service product story, filesystem/session-store boundary, worker cleanup, or
official-client interoperability cases.
Automatic major upgrades are forbidden. The original PR0 added no SDK dependency or lock; the
separate Phase-4 distribution now owns both without changing the core dependency boundary.
Supported-line decisions also follow the official
[security policy](https://github.com/modelcontextprotocol/python-sdk/security).

Windows acceptance uses native Windows tests, including owner and replacement DACL cases; every
intermediate and final reparse-point position and swap race; handle-contained Unicode and long
local paths; fail-closed UNC and network roots; case-insensitive member collisions and reserved
names; concurrent atomic no-replace directory publication; `LockFileEx` contention and cleanup;
Job Object descendant, memory, timeout, cancellation, and controller-exit cleanup; inherited-handle
and environment allowlists; binary STDIO; exact hash and byte fixtures; installed core and MCP
wheels; and the official client's complete tool, resource, failure, cancellation, and cleanup
story. POSIX emulation is not Windows validation, and no required case may skip, xfail,
`continue-on-error`, or use an unbounded fallback.

WSL2 is an unsupported convenience for running the Linux build under Linux semantics. It is not a
Windows release path and never counts as validation of Windows paths, DACLs, reparse points,
locking, publication, Job Objects, STDIO, wheels, or official-client behavior.

## 9. Frozen evaluation cases

[`evaluation_cases.v1.json`](local_mcp/evaluation_cases.v1.json) contains exactly five synthetic,
deterministic, data-only contracts:

1. a daily two-stock Simple Return analysis with compact result paging;
2. a volatility request missing answer-changing conventions and requiring clarification;
3. discovery text that occurs only in a negative boundary and therefore returns no candidate;
4. a Simple Return output refused as input to a log-return rolling-volatility port; and
5. a declaratively generated 10,000-component catalog with bounded deterministic discovery.

The fixture freezes objectives, tool sequences, bounded outputs, trust wording, refusal or
clarification behavior, and future runner assertions. It contains no executable runner and does
not commit 10,000 component directories.

## 10. Implementation order and non-goals

**Required MCP alpha release platforms:** Windows, macOS, and Linux. Native verification of the
equivalent Windows boundary remains a release blocker before the alpha is described as supported.

**Currently implemented MCP alpha platform providers:** Windows, macOS, and Linux. This status
describes the in-tree host mechanisms, not release support. The existing public release-support
wording must not be changed until the native tests, six-cell CI matrix, official-client story, and
exact-wheel installation all pass.

The implementation and release order remains fixed so transport work cannot create a second
runtime. Status below describes this working tree; only the native release gate remains open:

1. **Phase 1 — shared runtime extraction and CLI parity: complete.** `operation_runtime` and
   `DefinedQuantService` own the shared runtime; CLI and evidence execution preserve the public
   bytes, identity, failures, and subprocess behavior without an MCP dependency.
2. **Phase 2 — indexed discovery: complete.** One immutable process-lifetime contract index and
   stable-ID map preserve ranking, boundary explanations, filters, and facets and include the
   generated 10,000-record bounded functional case. Local timings are diagnostic only: the frozen
   fixture defines no portable wall-clock acceptance threshold, so CI must not invent one.
3. **Phase 3 — dataset registry and session store: complete.** Canonical records, fixed hash
   vectors, scoped ephemeral CAS, inline/file normalization, paging, and security limits are
   implemented behind the transport-neutral platform facade.
4. **Phase 4 — STDIO MCP server and worker: implemented, native validation pending.** The optional
   package isolates SDK code in `server.py`, exposes the seven tools and one resource, and includes
   official-client and process-cleanup tests. The Windows filesystem, session-store, Job Object,
   environment, locking, cleanup, and binary-STDIO providers are in-tree; the six native CI cells
   must prove them before Phase 4 is declared release-complete.
   Phase 4 ships without Phase 4B: `execute_component` returns `unsupported_binding` for every
   operation source exactly as section 4.2 specifies.
5. **Phase 4B — deferred protocol 0.5 provenance and one-hop composition.** Phase 4B is explicitly
   excluded from Phase 4 and from the initial MCP alpha release. Adopting it later is its own
   change: it must complete the lockstep version work in section 3 and add the required descriptor,
   compatibility, default, downgrade, and hash tests before permitting one exact compatible
   operation source. It still adds no managed plan execution.
6. **Phase 5 — packaging and user documentation: harness and docs in-tree; release versioning and
   native validation pending.** CI builds one exact compatible core/MCP wheel pair and installs it
   in every Windows, macOS, and Linux cell. The package README documents local installation, launch
   configuration, privacy, trust, platform scope, WSL2, and the exact-wheel rule. Publication
   remains blocked until the target version pair is applied and all six cells pass.

For this no-4B release, Phase 5 follows Phase 4 directly. The release targets are
`defined-quant` `0.2.0`, `defined_quant_protocol` unchanged at `0.4.0`, and
`defined-quant-mcp` `0.1.0a1`.

Receipt ownership follows that order: Phase 1 creates and reconciles the existing manifest and
members, Phase 3 publishes their `OperationRecordV1` and `dqop:v1` reference, and Phase 4 exposes
only bounded projections. No phase in the alpha adds a separate calculation-receipt model or a
portable reproduction bundle.

The original PR0 was intentionally limited and did not add shared runtime code; the MCP package or
dependency; server or worker code;
storage or ingestion code; protocol implementation changes; component, contract, version, or
evidence changes; provider/network code; persistent or managed execution; or generated schemas
presented as production models.
