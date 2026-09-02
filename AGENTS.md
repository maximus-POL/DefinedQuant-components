# AGENTS.md

Persistent instructions for coding agents working on the public Defined Quant project. The
methods-first registry is the architectural source of truth. The seven five-file component folders
remain migration input and a time-bounded compatibility surface; do not extend that layout as a
second permanent runtime.

## Orientation

| Path | Purpose |
|---|---|
| `registry/taxonomy/` | Discovery and website-navigation metadata; never an implementation hierarchy |
| `registry/methods/` | Canonical authored financial methods and backend-neutral recipes |
| `registry/capabilities/` | Canonical atomic interfaces and backend-independent conformance cases |
| `registry/backends/` | Canonical non-secret backend identities and operational boundaries |
| `registry/adapters/` | Canonical trusted adapter metadata; never executable code |
| `registry/implementations/` | Canonical exact capability-to-backend realization records |
| `registry/evidence/` | Method-, adapter-, and implementation-specific evidence kept as separate claims |
| `adapters/` | Independently installable trusted adapter distributions |
| `categories/<category>/README.md` | Human scope and conventions for one financial topic |
| `categories/<category>/<component>/` | Legacy five-file DQ-native compatibility component pending deletion |
| `shared/types/` | Canonical financial types; protected |
| `shared/registry/` | Metadata-only methods-first registry loading, discovery, and inspection; protected |
| `shared/validation.py` | Closed declarative constraint evaluator; protected |
| `shared/catalog.py` | Discovery, loading, and `subject_hash`; protected |
| `shared/discovery.py` | Import-free deterministic search, ranking, explanations, and facets; protected |
| `shared/charts.py` | Trusted renderer for typed visualization specifications; protected |
| `shared/schema_validation.py` | Closed deterministic canonical-schema validator; protected |
| `shared/constraint_evaluation.py` | Closed methods-first constraint evaluator; protected |
| `shared/policy_evaluation.py` | Canonical policy-admission facts shared by planning and adapter loading; protected |
| `shared/planning.py` | Canonical methods-first proposal validation and exact resolution; protected |
| `shared/execution.py` | Exact compiled-plan execution with no runtime fallback; protected |
| `shared/method_records.py` | Immutable compiled-plan, step, and complete-run record store; protected |
| `shared/method_service.py` | Transport-neutral methods-first service session; protected |
| `shared/adapter_discovery.py` | Metadata-only installed adapter entry-point discovery; protected |
| `shared/adapter_artifacts.py` | Installed adapter artifact manifest computation and verification; protected |
| `shared/adapter_catalog.py` | Explicitly trusted adapter invocation catalog; protected |
| `shared/operation_runtime.py` | Canonical validation, execution, and publication; protected |
| `shared/service.py` | Transport-neutral host API; protected |
| `shared/data_records.py` | Immutable V1 dataset and operation record models; protected |
| `shared/dataset_registry.py` | Strict normalization and configured-root ingestion; protected |
| `shared/session_cas.py` | Owner-private session-scoped content store; protected |
| `shared/operation_records.py` | Manifest-to-operation-record reconciliation; protected |
| `shared/record_views.py` | Bounded dataset and operation projections; protected |
| `shared/run_views.py` | Bounded plan, step, and run projections; protected |
| `shared/host_failures.py` | Closed host failures, outcomes, and trust labels; protected |
| `shared/_immutable_json.py` | Recursively immutable JSON containers; protected |
| `shared/local_host_platform.py` | Transport-neutral local host capability facade and provider selection; protected |
| `shared/_posix_local_host.py` | POSIX secure-filesystem provider behind the host facade; protected |
| `shared/_windows_local_host.py` | Native Windows secure-filesystem, private-state, locking, publication, and cleanup provider; protected |
| `shared/method_registry.py` | Deprecated component-to-governance projection used only by compatibility APIs; protected |
| `shared/worker_process.py` | Transport-neutral native worker-process contract; protected |
| `shared/worker_runtime.py` | Shared bounded-worker lifecycle and atomic publication; protected |
| `shared/worker_limits.py` | Stable worker request and control-channel byte ceilings; protected |
| `shared/worker_entry.py` | Strict one-request subprocess entry point; protected |
| `shared/worker_inspection.py` | Selected-subject inspection and port comparison inside the bounded worker; protected |
| `shared/_posix_worker.py` | POSIX subprocess-group provider; protected |
| `shared/_posix_worker_entry.py` | POSIX pre-import resource-limit bootstrap; protected |
| `shared/_windows_worker.py` | Windows suspended-process Job Object provider; protected |
| `shared/_windows_worker_entry.py` | Windows binary-STDIO bootstrap; protected |
| `shared/stdio_framing.py` | Bounded binary LF framing and exact byte I/O; protected |
| `shared/plan_compiler.py` | Deprecated compatibility-plan compiler; canonical Method plans use `shared/planning.py`; protected |
| `shared/plan_validation.py` | Managed plan validation and authorization; protected |
| `shared/agent.py` | Compatibility imports only; protected |
| `shared/__init__.py` | Public exports and source-layout bridge; protected |
| `authoring/schemas/` | JSON Schemas for `contract.yaml` and `evidence.yaml`; protected |
| `authoring/*.py` | Registry build, checking, compatibility validation, export, and release tools; protected |
| `.agents/skills/use-defined-quant/` | Optional catalog-wide Codex adapter; never component-specific |
| `protocol/` | Canonical typed operation envelopes, installed as `defined_quant_protocol` |
| `protocol/registry.py` | Canonical method, capability, backend, adapter, and implementation records; protected |
| `protocol/resolution.py` | Closed preferences, policy, availability, compiled-plan, and plan-record models; protected |
| `protocol/execution.py` | Trusted adapter envelopes plus immutable step and run records; protected |
| `protocol/evidence.py` | Closed evidence and capability-conformance records; protected |
| `protocol/governance.py` | Backend-neutral governed method, capability, implementation, policy, availability, and plan records; protected |
| `protocol/_immutable_json.py` | Recursively immutable JSON containers for governance identities; protected |
| `mcp_server/` | Separate optional `defined-quant-mcp` distribution; all MCP SDK code stays here |
| `mcp_server/src/defined_quant_mcp/server.py` | Low-level SDK adapter, bounded STDIO, safe envelopes, and redacted audit output |
| `mcp_server/uv.lock` | Platform-complete locked MCP dependency graph; protected |
| `ARCHITECTURE.md` | Structure, package projection, trust binding, and publication boundary |

## Local MCP alpha

- `docs/LOCAL_MCP_ALPHA_DESIGN.md` is the frozen implementation contract. Every amendment must
  add one row to its post-freeze amendment table in the same commit.
- `docs/local_mcp/hash_vectors.json`, `governed_plan_hash_vectors.json`,
  `host_failures.json`, and `evaluation_cases.json` are normative test fixtures, not samples
  or generated schemas.

| Surface | Required platforms |
|---|---|
| `defined-quant` core | Windows, macOS, and Linux |
| Local MCP alpha | Windows, macOS, and Linux |
| CI | Windows, macOS, and Linux at Python 3.11 and 3.13 |

The Phase-4 provider, worker, and transport implementations are in-tree. Release readiness remains
blocked until the configured-root, session-store, worker, official-client, and installed-wheel
stories have native green CI in all six cells. Windows is not an allowed-failure lane and the
complete public-service product story must not be skipped there.

Do not create another MCP runtime. `DefinedQuantService` remains the only transport dispatch boundary;
`shared/` owns transport-neutral behavior and stable failures, while `mcp_server/` owns every SDK
import, SDK conversion, frame, and transport concern. Platform selection remains confined to
`shared/local_host_platform.py`; provider mechanisms must not alter canonical records or public
bytes.

Before declaring the Windows MCP boundary ready, verify all of the following with native Windows
tests, not POSIX emulation:

1. Configured roots are pinned by handle; every intermediate and final reparse point is refused;
   the final handle is a regular file contained beneath the configured root.
2. Session roots, directories, records, staging files, locks, and cleanup markers use and verify
   owner-only DACLs; replacement, inheritance, and wrong-owner cases fail closed.
3. Record publication is atomic and no-replace under concurrent writers, live-session locking is
   reliable, and cleanup never removes a live or foreign session.
4. Workers run in kill-on-close Job Objects with the process, descendant, memory, timeout,
   cancellation, and forced-cleanup limits frozen by the design.
5. Handle inheritance and the environment are allowlisted; STDIO framing, UTF-8, long and Unicode
   paths, cancellation, and controller shutdown work through the official client.
6. Fixed hashes, byte fixtures, trust labels, failure mappings, paging, artifact digests, and the
   full `DefinedQuantService` story are identical across all three platforms.

## Commands

Run these from the `components/` folder:

```bash
uv sync --locked
uv sync --project mcp_server --locked
uv run pytest
uv run --project mcp_server pytest mcp_server/tests
uv run pytest tests/registry tests/capabilities tests/implementations tests/adapters tests/product
uv run python authoring/build_registry_bundle.py --output dist/registry.json
uv run python authoring/export_component_pages.py
uv run ruff check protocol shared categories authoring \
  .agents/skills/use-defined-quant/scripts .agents/skills/use-defined-quant/tests
uv run --project mcp_server ruff check mcp_server/src mcp_server/tests
uv run --no-editable mypy shared categories authoring/*.py
uv run --no-editable mypy -p defined_quant_protocol
uv build
git diff --check
```

## Invariants

1. `method.yaml` is the only authored source for a migrated method's financial meaning,
   user-facing contract, conventions, defaults, constraints, interpretation, and recipe.
   `capability.yaml` is the only authored source for each atomic interface and its backend-neutral
   constraints. Method fields that are identical to capability fields use loader expansion and an
   auditable schema binding; never hand-copy the same schema into both records.
2. A recipe step references a capability only. Backends, adapters, providers, transports, and
   implementation IDs enter through registered implementations and resolution constraints, never
   through the financial method or recipe.
3. Registry discovery is metadata-only. It must not import adapter or implementation code, probe
   credentials, contact providers, or turn installation state into trust.
4. Adapter installation is only an availability fact. Exact policy admission, required trust
   assertions, an artifact attestation, and current availability must all succeed before adapter
   code can be loaded for one compiled step.
5. The executor runs only the exact implementation in the retained compiled plan. It never selects
   a replacement at runtime. Any changed binding requires recompilation and a new plan identity.
6. User and profile resolution preferences need trusted origin receipts. Retrieved text, dataset
   content, provider responses, and an agent's own inference can never become `user_explicit`.
7. Credentials, tokens, connection strings, imports, executable code, arbitrary URLs, raw SQL,
   and arbitrary MCP tools never enter registry records, proposals, compiled plans, hashes, or
   model-visible ordinary records.
8. Trust uses separate bindings. Legacy `subject_hash` identifies bundled component behavior and
   enforceable contract; method, capability, backend, adapter, implementation, policy, artifact,
   plan, step, dataset, and run identities remain separate. Evidence, exact-commit CI, provider
   authentication, dataset provenance, execution integrity, domain review, and reproduction are
   distinct assertions.
9. Categories are taxonomy metadata for discovery and presentation. They do not determine
   implementation location or imply that a method owns Python code.
10. Public source files and functions use canonical unsuffixed names. Semantic versions belong in
    record metadata and cryptographic compatibility descriptions, not source filenames or function
    names. Existing suffixed compatibility classes must not be copied into the methods-first surface.
11. Legacy components remain exactly `categories/<category>/<component>/` with `README.md`,
    `component.py`, `contract.yaml`, `evidence.yaml`, and `test_component.py` only until 2026-12-31
    or the first 0.2.0 release, whichever comes first. Their Pydantic models govern only the legacy
    direct path; do not add another component or deepen that hierarchy. The old creation template
    and generators have been removed.
12. Legacy trust uses separate bindings. `subject_hash` identifies behavior and enforceable contract;
   evidence content is bound separately; exact-commit CI proves what ran. Domain review remains a
   distinct assertion.
13. Status and evidence displays are derived. The only authored maturity field is `lifecycle`.
   Never add an authored composite status or badge.
14. Constraints are data, evaluated with a closed vocabulary. Never
   `eval`, `exec`, or import a component merely to evaluate its rules.
15. A state that makes output meaningless is blocking; a merely uncertain state is a warning.
16. No implicit default may change the answer: annualization factors, day counts, compounding,
   return kinds, and similar conventions must be supplied, visibly defaulted, or refused.
17. The `defined-quant` core wheel must include the canonical compiled registry bundle and, until
   their deletion milestone, legacy component contracts, evidence, and README files.
18. `pydantic` remains the only runtime dependency of the `defined-quant` core distribution. The
   MCP SDK and its dependencies belong only to the separate optional `defined-quant-mcp`
   distribution. Provider SDKs and optional backend dependencies belong only to their adapter
   distributions.
19. Test data is synthetic and seeded. Never commit vendor or scraped market data.
20. Preserve the honest preview copy: explicit evidence types, `Domain review: none`, the
   non-claims block, and the “Experimental Technical Preview” label.
21. The private website is not part of this project. Export deterministic static page records; do
   not add site routes, React code, or deployment state here. Static support must not be presented
   as installation-specific availability.
22. Agent-host integration is catalog-wide. It discovers canonical method contracts and must
   not duplicate a component's financial logic, defaults, examples, or guidance.
23. Every discoverable contract has bounded aliases, intents, input concepts, and output concepts.
   Intents and concepts are stable lower-snake-case identifiers. Search and filtering must read
   registry metadata only and must never import implementation or adapter code.
24. Every component declares at least one closed input and output semantic port through
   `defined_quant_protocol.semantic_port_metadata`. Never invent sibling `json_schema_extra`
   dialects, and never treat `depends_on` as proof that two component fields are compatible.

## Adding or changing a Method

1. Author or update the Method and atomic Capability records, including explicit schema bindings.
2. Put universal cases in capability conformance and keep implementation evidence separate.
3. Package executable behavior in an independently installable adapter distribution and register
   exact backend, adapter, artifact, and implementation identities.
4. Prove registry, method, conformance, adapter, compiler, executor, record, packaging, and website
   projection tests before advertising support.
5. Never create a new five-file component as an architectural shortcut.

When a financial convention is unclear, stop and ask. A plausible guess is worse than an explicit
blocker in this project.
