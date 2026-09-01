# Governed plan compiler V1

**Status:** Phase-1 architecture decision

This RFC defines the first governed-planning layer for Defined Quant. It is deliberately smaller
than the future governed-execution architecture: it registers backend-neutral financial methods,
resolves approved implementations deterministically, and produces an immutable compiled plan. It
does not execute that plan.

## 1. Product and trust boundary

Defined Quant remains a local MCP server with no generative model in its trusted core. An agent may
propose a financial method and supply strictly structured financial inputs and explicitly permitted
choices. Defined Quant alone validates the proposal, applies only authored defaults, resolves
implementations under configured policy and explicit availability, and decides whether compilation
succeeded.

The governing rule is:

> The LLM may choose or propose the calculation. It is never the authority that says the
> calculation or its validation succeeded.

Phase 1 may make only these scoped positive claims:

- `PLAN VALIDATION PASSED`
- `ELIGIBLE UNDER POLICY`

Those claims mean that the proposed inputs and conventions passed the closed method contract and
that each bound implementation was eligible under the exact supplied resolution policy and
availability facts. They do not claim financial correctness, data authenticity, calculation
success, output reconciliation, independent review, execution attestation, or replay verification.
No broad `verified` boolean or composite trust badge is introduced.

## 2. Terminology

`MethodSpecV1`
: A professional financial method contract. It owns typed inputs and outputs, units, methodology,
  constraints, conventions, interpretation semantics, authored defaults, and an embedded
  backend-neutral recipe DAG.

Recipe
: The ordered dataflow needed to realize a method. In V1 it is embedded in `MethodSpecV1`; there is
  no independent recipe registry or recipe hash.

`CapabilitySpecV1`
: A backend-neutral typed operation required by a recipe node, such as `statistics.ols` or
  `market_data.equity_history`.

`ImplementationSpecV1`
: One registered concrete implementation of one capability. It binds trusted adapter identity,
  transport and implementation metadata, semantic ports, and separate scoped trust dimensions.

Adapter
: Trusted Defined Quant code that knows how to invoke an implementation. An adapter is registry
  data and trusted runtime code, never caller-supplied code.

Provider
: An external data provider behind a data implementation.

Transport
: Python, HTTP, SQL, MCP, or another invocation mechanism. Transport is implementation metadata;
  it is not a financial capability.

Component
: The existing public compatibility term. Current component tools, operation records, and
  `subject_hash` semantics remain unchanged.

A recipe node names a capability only. It may not name an implementation, adapter, provider,
Python import, URL, SQL statement, credential, or arbitrary MCP tool.

## 3. Phase-1 records

The public records live in `defined_quant_protocol` under `protocol/governance.py`. Every record is
closed (`extra="forbid"`), recursively immutable, bounded, canonically serializable, and versioned
with `schema_version: 1`. Copy construction revalidates instead of accepting unchecked updates.

### 3.1 Registry records

`MethodSpecV1` binds the method identity and exact financial contract. Its recipe is a DAG of
stable step IDs. Each step references one exact capability and maps only declared method inputs or
earlier step outputs into every declared capability input. In V1, terminal result fields bind to
method outputs by exact field name; the terminal-step union must expose every method output exactly
once. Step IDs, bindings, and output targets are unique, every method input is consumed, and graph
ordering is validated. Bound source and target JSON Schemas must have the same closed V1 validation
projection, including exact semantic-port metadata.

`CapabilitySpecV1` defines the complete typed input and output contract and named semantic ports
for one abstract operation. Capability identity is stronger than a search tag: an implementation
must match both the exact capability and its semantic ports.

`ImplementationSpecV1` implements one exact capability. It carries a stable implementation ID,
trusted adapter identity, transport metadata, and separate scoped trust dimensions. Unit, golden,
property, and differential testing, domain review, provider authentication, and other dimensions
remain distinct facts; their absence is not collapsed into a composite badge. Provider,
connection, package, or other backend metadata may exist only in this trusted record. Secrets and
credential values never do.

`ResolutionPolicyV1` is a service-owned allowlist and priority policy. It determines which
implementations may be considered for each method step, the required scoped trust dimensions, and the
integer priority used for selection. The policy never delegates implementation selection to the
agent.

`AvailabilitySnapshotV1` is a service-supplied, explicit, non-secret observation of whether exact
registered implementations are currently available, with a closed reason when unavailable. It is
an input to compilation, not an invitation for the compiler to inspect the environment.

### 3.2 Proposal and outcomes

`PlanProposalV1` contains only a method ID and version, structured financial inputs, explicit
conventions, and optional untrusted agent rationale. It has no field for an implementation ID,
provider name, adapter, transport, code, import path, URL, SQL, credential, connection, or MCP tool.
Unknown fields are refused.

`compile_plan` returns exactly one of three discriminated outcomes:

- `CompiledPlanV1` with status `compiled` when validation and deterministic implementation
  resolution both succeed;
- `NeedsInformationV1` with status `needs_information` when an answer-changing required
  convention has neither a supplied value nor an authored default; or
- `PlanRefusalV1` with status `refused` when the proposal, registry slice, policy, semantic ports,
  trust requirements, availability, or resolution contract cannot produce an eligible exact plan.

`NeedsInformationV1` reports machine-readable field paths, closed reason codes, bounded
descriptions, and permitted values when the method declares them. `PlanRefusalV1` uses closed
reason codes and safe bounded details; native exception text is not part of the outcome.

Every default is authored in `MethodSpecV1`. A default that is applied is materialized in the
resolved input and in an explicit applied-default record. Missing answer-changing conventions are
never guessed. Changing a supplied financial input, convention, or applied default changes the
compiled plan identity. Every optional method input has one authored default; JSON Schema default
metadata, when present, must match it exactly. A non-user-required convention must be resolved by
such a default.

## 4. Architecture and ownership

```text
agent
  |
  | PlanProposalV1
  v
local DQ MCP compile_plan
  |
  v
DefinedQuantService
  |-- selected Method/Capability/Implementation registry records
  |-- configured ResolutionPolicyV1
  `-- explicit AvailabilitySnapshotV1
          |
          v
    pure compile_plan
          |
          +-- CompiledPlanV1
          +-- NeedsInformationV1
          `-- PlanRefusalV1
```

`DefinedQuantService` remains the only transport-neutral dispatch boundary. The MCP package owns
only SDK request conversion, response conversion, bounds, and transport behavior. It does not
implement validation or resolution.

`shared/method_registry.py` owns immutable registry lookup and the legacy compatibility
projection. `shared/plan_compiler.py` owns the pure compiler. The compiler receives every input as
an explicit argument and performs no network access, filesystem access, environment inspection,
credential lookup, current-time lookup, component execution, or other ambient I/O.

The service may collect the selected registry slice, configured policy, and explicit non-secret
availability snapshot before calling the compiler. This collection is outside the pure function.
An injected registry without an explicit policy fails closed; only the bounded legacy
`dq_native` compatibility projection constructs its explicit compatibility policy.
Where compatibility projection needs a selected component's canonical Pydantic schemas, the
existing bounded-worker import boundary remains authoritative; the long-lived MCP controller does
not import component code.

The local MCP surface adds one read-only, idempotent, closed-world tool named `compile_plan`. V1
does not expose separate `validate_plan` or `resolve_implementations` tools. The existing seven
tools remain present with unchanged schemas and behavior.

## 5. Deterministic compilation

For each proposal, the compiler performs the following deterministic sequence:

1. Match the exact method ID and version and validate the closed proposal.
2. Resolve financial inputs and conventions against the closed supported JSON Schema subset,
   rejecting supplied unknown or invalid fields before requesting clarification. Date-time values
   are validated as aware RFC 3339 values and normalized to instants for constraint evaluation.
3. Materialize every authored default, enforce declared convention values, and return
   `NeedsInformationV1` for each missing non-defaulted required convention.
4. Evaluate constraints through the canonical closed constraint evaluator; validate complete DAG
   bindings, exact field-schema compatibility, and matching semantic-port projections.
5. For every step, collect implementations that match the exact capability and semantic ports.
6. Filter candidates forbidden by `ResolutionPolicyV1` and enforce every required scoped trust
   dimension.
7. Filter the remaining exact candidates through the supplied `AvailabilitySnapshotV1`.
8. Sort eligible candidates by policy-authored integer priority, then by stable implementation ID
   using deterministic byte ordering.
9. Select the first candidate or return `PlanRefusalV1` if none remains.
10. Record the candidate evaluation and immutable selected binding in the compiled plan.

Registry iteration or insertion order is never consulted. A compiled binding cannot express a
fallback. Future execution must use that exact implementation or fail; selecting another
implementation requires recompilation and a new `plan_hash`.

## 6. Content identities

Phase 1 reuses `dq-tagged-json-v1`, `canonical_json_bytes`, and the existing domain-separated
`canonical_hash`. It adds exactly five content identities and domains:

| Identity | Domain |
|---|---|
| `spec_hash` | `governance.method_spec.v1` |
| `capability_hash` | `governance.capability_spec.v1` |
| `implementation_hash` | `governance.implementation_spec.v1` |
| `policy_hash` | `governance.resolution_policy.v1` |
| `plan_hash` | `governance.compiled_plan.v1` |

The existing `subject_hash`, `OperationRequest.operation_hash`, `AnalysisPlan.plan_hash`,
`ExecutionPolicy.policy_hash`, and their canonical bytes and domains are not reinterpreted or
changed. The same public property name on a new record does not merge its domain with a legacy
record.

The `CompiledPlanV1` semantic projection binds:

- exact `spec_hash`;
- exact capability hashes for its ordered steps;
- the sorted implementation hashes of only the candidates relevant to each step;
- each exact selected implementation hash;
- exact `policy_hash`;
- the relevant availability entries used for those candidates;
- deterministic per-step resolution receipts;
- the resolution algorithm version; and
- resolved financial inputs, conventions, and materialized defaults.

The complete global registry is never hashed. Adding an implementation unrelated to the selected
method and policy cannot change an existing plan. Changing a relevant candidate, policy,
availability fact, selected implementation, financial input, convention, or applied default must
change the plan.

Agent rationale and other narrative annotations are preserved for inspection but excluded from
the computational `plan_hash`. The availability snapshot has no separate Phase-1 hash identity;
the plan binds the exact candidate-relevant availability facts it consumed. Phase 1 does not add
`evidence_hash`, `snapshot_hash`, `step_result_hash`, `run_hash`, environment digests, proposal
hashes, receipt hashes, or replay identities.

## 7. Legacy compatibility projection

Current components remain the public execution and discovery compatibility surface. Phase 1 may
project an existing component into:

- one `MethodSpecV1` using its current canonical Pydantic input/output schemas, units, guidance,
  formula, assumptions, limitations, constraints, conventions, interpretation, and a one-node
  recipe;
- one matching `CapabilitySpecV1`; and
- one `ImplementationSpecV1` using the trusted `dq_native` adapter and an exact legacy
  `ComponentRef` containing the unchanged `subject_hash`.

This projection is a migration adapter, not the final capability taxonomy. It adds no financial
calculation and does not move implementation or provider identity into the recipe. No current
component file, public tool name, operation record, operation execution path, or subject-hash
projection changes because of it.

## 8. Explicit non-goals

Phase 1 does not add or perform:

- plan execution or `execute_plan`;
- OpenBB, statsmodels, SciPy, QuantLib, or any other calculation/data dependency;
- provider fetches, network calls, SQL, external MCP calls, or arbitrary user Python;
- credential brokering or secret storage;
- persistent or raw provider-response snapshots;
- run records, step-result records, environment capture, replay, or reproduction bundles;
- an inspection UI, HTTP MCP transport, embedding search, or an LLM router; or
- broad renaming or behavior changes to existing component tools.

The core distribution remains Pydantic-only. Execution backends, provider adapters, durable run
records, snapshots, inspection views, and replay require later separately reviewed phases.

## 9. Compatibility and verification requirements

Fixed vectors must cover all five new hash domains and canonical compiled-plan bytes on Windows,
macOS, and Linux. Tests must prove stable output for identical explicit inputs; insertion-order
independence; immunity to unrelated registry additions; sensitivity to selected implementation,
policy, relevant candidate, financial input, convention, and default changes; narrative exclusion;
closed-field refusal; no agent implementation override; and the absence of silent fallback.

Existing `subject_hash` values, protocol vectors, seven MCP tool schemas, operation behavior, and
the full current test suite remain compatibility gates. This RFC does not itself authorize or
attest any calculation.
