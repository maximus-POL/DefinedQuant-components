# Defined Quant protocols

`defined_quant_protocol` 0.4.0 provides closed, typed interchange formats for generic component
operations, semantic component ports, and atomic managed authorization. It is versioned
independently from the component catalog and ships in the same Python distribution for now. The
package depends only on Pydantic and the Python standard library; it does not import component
implementations, discovery, renderers, or any host application.

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
values therefore match; signed zero becomes positive zero. Non-finite values, integers outside
`-(2^53-1)` through `2^53-1`, and integer-valued floats outside that range are refused.

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
The packaged `simple_return_csv_v1` execution policy currently permits exactly this draft subject,
with explicit draft opt-in:

```text
dq.market_data.simple_return
version:      0.3.3
subject_hash: 63a2e74034b45f567fda32b263dc61441f16c9cfd5107f88b41505d16de39cab
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

The protocol source code is licensed under Apache-2.0 under the repository's `LICENSE` file. This
README is documentation and remains licensed under CC BY 4.0 as specified by `LICENSE-CONTENT`.
