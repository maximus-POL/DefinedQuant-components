# Defined Quant protocols

`defined_quant_protocol` 0.2.0 provides closed, typed interchange formats for generic component
operations and atomic managed authorization. It is versioned independently from the component
catalog and ships in the same Python distribution for now. The package depends only on Pydantic
and the Python standard library; it does not import component implementations, discovery,
renderers, or any host application.

## Unmanaged component operations

An `OperationRequest` binds the exact component ID, version, and `subject_hash` plus candidate
input and caller-asserted provenance. The selected component's Pydantic input model remains the
canonical authority that validates and normalizes that candidate input before execution. The
request intentionally contains no catalog root, output directory, timestamp, or other host-runtime
setting. Those settings are trusted runtime configuration and are not semantic request data. The
current runner requires a new output directory and does not support mutable overwrite behavior.
Publication uses the host's atomic no-replace rename primitive on Linux, macOS, and Windows; a
host without that primitive receives a typed failure instead of a weaker overwrite fallback.

The direct `OperationRequest` path remains explicitly unmanaged in protocol 0.2.0. Its provenance
status is either `unverified` or `caller_confirmed`; it cannot claim that values are source-bound.
An operation request does not become managed merely because the package now also defines managed
authorization records.

New operation manifests default to protocol 0.2.0. The unchanged unmanaged manifest shape still
parses protocol 0.1.0 records, so adding the separate authorization surface does not invalidate
existing operation bundles.

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

## Atomic managed authorization

Protocol 0.2.0 adds an authorization-only foundation for one immutable, one-step `AnalysisPlan`.
The packaged `simple_return_csv_v1` execution policy currently permits exactly this draft subject,
with explicit draft opt-in:

```text
dq.market_data.simple_return
version:      0.2.1
subject_hash: be4ec41acc48df60a5c986efdf272894878ed989b681646de11d7a6a62c6100c
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
attestation or `ResearchBundle`. Source-bound execution and portable research bundles remain
deferred. Semantic port metadata and multi-step composition, including Log Return to Historical
Volatility, are also deferred; adding them changes the authorized subject and requires a new
component version and re-freeze.

Pydantic's frozen configuration prevents model-field reassignment but is shallow: nested JSON
containers supplied as component input or a dataset binding must be treated as immutable by
callers. Manifest construction recalculates and reconciles the operation request hash, while
managed authorization revalidation recalculates all semantic roots. A shallow nested JSON mutation
therefore requires revalidation and, if semantics changed, a new manual approval. Hosts should
serialize and revalidate records at trust boundaries.

The protocol source code is licensed under Apache-2.0 under the repository's `LICENSE` file. This
README is documentation and remains licensed under CC BY 4.0 as specified by `LICENSE-CONTENT`.
