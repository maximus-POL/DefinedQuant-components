# Defined Quant protocols

`defined_quant_protocol` 0.4.0 provides closed, typed interchange formats for canonical methods,
capabilities, backends, adapters, implementations, resolution preferences, policy, availability,
compiled plans, trusted-adapter invocation, immutable step/run records, and the legacy component
and authorization surfaces. Record schema versions remain metadata; canonical methods-first files,
functions, and classes use unsuffixed names. The package is versioned independently and ships in
the core distribution for now. It depends only on Pydantic and the Python standard library and does
not import implementations, discovery, renderers, provider SDKs, or any host application.

## Unmanaged component operations

An `OperationRequest` binds the exact component ID, version, and `subject_hash` plus candidate
input and caller-asserted provenance. The selected component's Pydantic input model remains the
canonical authority that validates and normalizes that candidate input before execution. The
request intentionally contains no catalog root, output directory, timestamp, or other host-runtime
setting. Those settings are trusted runtime configuration and are not semantic request data. The
current runner requires a new output directory and does not support mutable overwrite behavior.
Publication uses the host's atomic no-replace rename primitive on Linux, macOS, and Windows; a
host without that primitive receives a typed failure instead of a weaker overwrite fallback.

The direct `OperationRequest` path remains explicitly unmanaged in protocol 0.4.0. Its provenance
status is either `unverified` or `caller_confirmed`; it cannot claim that values are source-bound.
An operation request does not become managed merely because the package now also defines managed
authorization records.

New operation manifests default to protocol 0.4.0. The unchanged unmanaged manifest shape still
parses protocol 0.1.0, 0.2.0, and 0.3.0 records, so extending the semantic-port vocabulary does
not invalidate existing operation bundles.

Every path stored in an `OperationManifest` is a relative POSIX bundle-member path using portable
safe-ASCII names. Each segment contains only letters, digits, underscores, and hyphens, with dots
allowed only internally; `/` is the only segment separator. Leading or trailing dots, empty
segments, colons, backslashes, controls, spaces, and Unicode are refused. The output root is
supplied separately to the runner and is never serialized or hashed. Request hashes use canonical
JSON with an explicit domain separator, and manifests carry the exact request and component
reference they bind. The current artifact surface is deterministic SVG only.

## Canonical bytes and hashes

The normative canonicalization is `dq-tagged-json-v1`. It first replaces every input value with a
collision-free tagged tree:

| Input | Tagged node |
|---|---|
| null | `["null"]` |
| boolean | `["boolean", true-or-false]` |
| string | `["string", value]` |
| number | `["number", binary64-hex]` |
| array | `["array", [tagged-items]]` |
| object | `["object", [[key, tagged-value], ...]]` |

Object entries are sorted lexicographically by the valid UTF-8 bytes of their keys. Unicode is not
normalized, and lone surrogates are refused. Every number is encoded as 16 lowercase hexadecimal
digits containing its IEEE-754 binary64 big-endian bytes. Safe integers and their equivalent float
values therefore match; signed zero becomes positive zero. Non-finite values and Python integers
outside `-(2^53-1)` through `2^53-1` are refused. Every finite float is accepted, including an
integer-valued binary64 outside that range, because its exact floating-point bytes are retained.

The tagged tree is serialized as compact JSON arrays with no whitespace. Strings escape quote and
backslash as `\"` and `\\`; backspace, tab, line feed, form feed, and carriage return as `\b`,
`\t`, `\n`, `\f`, and `\r`; and other U+0000 through U+001F controls as lowercase `\u00xx`.
Every other scalar is emitted literally as valid UTF-8, including `/` and non-ASCII characters.

For a domain `D` and canonical bytes `C`, the lowercase hash is exactly
`SHA-256("defined-quant" || NUL || "dq-tagged-json-v1" || NUL || D || NUL || C)`. The canonicalizer
ID and domain are therefore both bound into every digest; no host-formatted JSON number text enters
the hash.

`operation_protocol_schema()` publishes the package and protocol versions, unmanaged execution
mode, canonicalizer ID, SHA-256 framing, operation-request domain, one fixed `test.vector`
byte/hash vector, and the nested request, manifest, success, failure, and result schemas.
Integrations should consume that descriptor instead of reconstructing its fields. The complete
normative vector suite, including Unicode ordering, number equivalence, safe-integer boundaries,
and refusal cases, is executable in `authoring/tests/test_agent_protocol.py`.

## Semantic component ports

Protocol 0.3.0 introduced the closed `x-defined-quant-port` JSON Schema extension carried by
selected Pydantic input and output fields. Protocol 0.4.0 extends its closed concept, convention,
and frequency vocabulary for rebased indices, drawdowns, rolling volatility, and monthly series.
A `SemanticPort` declares direction, concept, unit, shape, cardinality, convention, ordering,
frequency, and provenance requirement. No partial payload, sibling ad-hoc metadata key, or
invented vocabulary value is accepted.

`compare_semantic_ports()` compares an output field with an input field across all eight semantic
dimensions and returns typed differences; `require_compatible_ports()` fails closed on any
difference. A producer's provenance guarantee must satisfy the consumer's declared requirement.
Current unmanaged component inputs declare `not_required`, because this protocol does not
authenticate caller data. Component-produced outputs may truthfully declare `component_bound`.
An `exactly_one` port is non-nullable, and an ordered `one_or_more` port must expose
`minItems >= 1` in its generated field schema. Compatibility JSON Schema enforces producer/output
and consumer/input direction plus empty-versus-nonempty difference consistency. Recomputing the
exact differences from both port payloads remains a runtime Pydantic invariant.

Port compatibility establishes that one field can be considered for another field; it does not
move data, bypass the consumer's input constraints, authorize a multi-step plan, or turn an
unverified input into a source-bound value. `depends_on` binds implementation dependencies into a
subject hash and is not a substitute for port compatibility.

## Atomic managed authorization

Protocol 0.2.0 introduced the authorization-only foundation for one immutable, one-step
`AnalysisPlan`. New plans use protocol 0.4.0 while protocol 0.2.0 and 0.3.0 plans remain readable.
The packaged `simple_return_csv` execution policy currently permits exactly this draft subject,
with explicit draft opt-in:

```text
dq.market_data.simple_return
version:      0.3.4
subject_hash: ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a
```

The catalog-aware validator checks that exact allowlist binding, required questions, the canonical
component input model, declarative constraints, lifecycle opt-in, and the profile's requirement
for non-empty timestamps. Success produces a deterministic `ValidationReceipt`; failure produces
a typed blocked or needs-information result and no approvable receipt. `ApprovalRecord` is
manual-only and binds one exact plan hash and receipt hash. `AuthorizationBinding` captures the
plan, receipt, approval, component, and dataset roots, and `verify_authorization()` reproduces the
validation before a later runner may act. Changes to any bound root require validation and manual
approval again.

`manual` is a closed authorization kind, not an identity proof or digital signature. The host is
responsible for authenticating the reviewer and controlling access to approval creation; this
foundation only makes the resulting actor assertion and exact bindings inspectable.

`ResolvedQuestion.resolved_at` is operational audit metadata and is deliberately excluded from
the semantic plan hash. Its answer, target, supplier, and verification status are semantic.
Dataset timestamps are input data, so they are included in `DatasetBinding.dataset_hash` and in
the plan hash. Changing a timestamp therefore invalidates an existing receipt and approval chain.

`managed_authorization_protocol_schema()` publishes the policy, plan, validation, approval, and
authorization schemas plus their hash domains. This surface validates and authorizes; it does not
call the calculation, authenticate a data source, issue citations, or produce an execution
attestation or `ResearchBundle`. Component result schemas may expose indexed calculation
derivations, but nullable citation join keys do not authenticate their inputs. Semantic ports make
compatible fields inspectable, including the Log Return to Historical Volatility boundary, but
this authorization surface remains deliberately one-step. Source-bound execution, managed
multi-step composition, and portable research bundles remain deferred.

Pydantic's frozen configuration prevents model-field reassignment but is shallow: nested JSON
containers supplied as component input or a dataset binding must be treated as immutable by
callers. Manifest construction recalculates and reconciles the operation request hash, while
managed authorization revalidation recalculates all semantic roots. A shallow nested JSON mutation
therefore requires revalidation and, if semantics changed, a new manual approval. Hosts should
serialize and revalidate records at trust boundaries.

## Canonical methods-first registry

`registry.py` defines the unsuffixed `MethodSpec`, `CapabilitySpec`, `BackendSpec`, `AdapterSpec`,
and `ImplementationSpec` records and their exact references. A Method owns financial meaning and a
Recipe whose steps reference Capabilities only. A Capability owns one atomic closed interface and
semantic ports. Backend, Adapter, and Implementation records carry operational and trust concerns
without redefining methodology.

Backend kinds distinguish DQ-native runtimes, Python libraries, native libraries, data providers,
analytics services, HTTP APIs, databases, and external MCP systems. Backend bindings are role-based,
so a runtime library and a data provider are never flattened into one ambiguous provider field.
Credentials and secret values do not appear in any registry record.

Method and Capability contracts have separate hash domains. Backend, Adapter, and Implementation
specifications also have separate identities. The Implementation is the sole owner of its exact
installed-distribution artifact pin; the Adapter owns only its distribution and dispatch identity.
Operational availability checks are derived from exact Backend boundaries plus any
Implementation-specific dependency probes. Registry evidence references are scoped claims and
never become provider authentication or execution attestations.

## Resolution and compiled plans

`resolution.py` defines `PlanProposal`, scoped `ResolutionConstraint` records,
`OriginReceiptSet`, `ResolutionPolicy`, `AvailabilitySnapshot`, candidate decisions, exact compiled
steps, `CompiledPlan`, and `PlanRecord`. The closed preference vocabulary covers implementation,
backend, adapter family, backend kind, transport, locality, and network dimensions with required,
preferred, forbidden, allowed-set, and automatic modes.

Preferred fallback is permitted only when the original constraint explicitly allows it. Required
choices fail rather than substitute. A user-explicit or user-profile origin needs a separate
host-recognized receipt bound to the exact constraint and trusted session; an agent proposal cannot
mint one. Financial proposal values reject executable, connection, URL, SQL, import, and
secret-bearing material.

The compiler binds only relevant policy, availability, and candidate facts, preserving deterministic
identity when unrelated registry records are added. The compiled plan records every considered
candidate, policy or availability refusal, exact selection, whether fallback was permitted and used,
and a deterministic explanation. It has no runtime fallback.

## Trusted execution records

`execution.py` defines `PlanExecutionRequest`, `AdapterExecutionRequest`, success and sanitized
failure results, canonical validation receipts, `StepRecord`, and complete `RunRecord`. Adapter
requests carry the exact compiled Capability, Implementation, Adapter, backend bindings,
availability, canonical inputs, and dependency steps. They contain no credential or arbitrary
dispatch control.

Each Step records exact inputs, input/output validation, the adapter request/result, provider
interactions, warnings, artifacts, timestamps, and failure or success. A Run binds the Method,
compiled Plan, datasets, ordered Steps, canonical Method output, Method-output validation, executor,
warnings, and complete failure or success. A false success is structurally invalid. Provider
authentication, entitlement, request ID, and response digest are runtime interaction facts; they
cannot be claimed by the compiler.

All seven bundled Methods execute through nine exact DQ-native Implementations and their trusted
adapters. No production OpenBB, LSEG, statsmodels, QuantLib, database, HTTP, or external-MCP
implementation is present. Canonical source filenames and function names are unsuffixed; protocol
versions remain record metadata and cryptographic-domain data where compatibility requires them.

The protocol source code is licensed under Apache-2.0 under the repository's `LICENSE` file. This
README is documentation and remains licensed under CC BY 4.0 as specified by `LICENSE-CONTENT`.
