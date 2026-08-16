# AGENTS.md

Persistent instructions for coding agents working on the public Defined Quant components project.
Runtime instructions for using a calculation live under `guidance` in its `contract.yaml`; do not
conflate them with these repository instructions.

## Orientation

| Path | Purpose |
|---|---|
| `categories/<category>/README.md` | Human scope and conventions for one financial topic |
| `categories/<category>/<component>/` | One component, exactly two directory levels below `categories/` |
| `shared/types/` | Canonical financial types; protected |
| `shared/validation.py` | Closed declarative constraint evaluator; protected |
| `shared/catalog.py` | Discovery, loading, and `subject_hash`; protected |
| `shared/discovery.py` | Import-free deterministic search, ranking, explanations, and facets; protected |
| `shared/charts.py` | Trusted renderer for typed visualization specifications; protected |
| `shared/operation_runtime.py` | Canonical validation, execution, and publication; protected |
| `shared/service.py` | Transport-neutral host API; protected |
| `shared/data_records.py` | Immutable V1 dataset and operation record models; protected |
| `shared/dataset_registry.py` | Strict normalization and configured-root ingestion; protected |
| `shared/session_cas.py` | Owner-private session-scoped content store; protected |
| `shared/operation_records.py` | Manifest-to-operation-record reconciliation; protected |
| `shared/record_views.py` | Bounded dataset and operation projections; protected |
| `shared/host_failures.py` | Closed host failures, outcomes, and trust labels; protected |
| `shared/_immutable_json.py` | Recursively immutable JSON containers; protected |
| `shared/local_host_platform.py` | Transport-neutral local host capability facade and provider selection; protected |
| `shared/_posix_local_host.py` | POSIX secure-filesystem provider behind the host facade; protected |
| `shared/_windows_local_host.py` | Native Windows secure-filesystem, private-state, locking, publication, and cleanup provider; protected |
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
| `shared/plan_validation.py` | Managed plan validation and authorization; protected |
| `shared/agent.py` | Compatibility imports only; protected |
| `shared/__init__.py` | Public exports and source-layout bridge; protected |
| `authoring/component-template/` | The one canonical five-file component template |
| `authoring/schemas/` | JSON Schemas for `contract.yaml` and `evidence.yaml`; protected |
| `authoring/*.py` | Explicit creation, checking, and catalog-export tools; protected |
| `.agents/skills/use-defined-quant/` | Optional catalog-wide Codex adapter; never component-specific |
| `protocol/` | Canonical typed operation envelopes, installed as `defined_quant_protocol` |
| `mcp_server/` | Separate optional `defined-quant-mcp` distribution; all MCP SDK code stays here |
| `mcp_server/src/defined_quant_mcp/server.py` | Low-level SDK adapter, bounded STDIO, safe envelopes, and redacted audit output |
| `mcp_server/uv.lock` | Platform-complete locked MCP dependency graph; protected |
| `ARCHITECTURE.md` | Structure, package projection, trust binding, and publication boundary |

## Local MCP alpha

- `docs/LOCAL_MCP_ALPHA_DESIGN.md` is the frozen implementation contract. Every amendment must
  add one row to its post-freeze amendment table in the same commit.
- `docs/local_mcp/hash_vectors.v1.json`, `host_failures.v1.json`, and
  `evaluation_cases.v1.json` are normative test fixtures, not samples or generated schemas.

| Surface | Required platforms |
|---|---|
| `defined-quant` core | Windows, macOS, and Linux |
| Local MCP alpha | Windows, macOS, and Linux |
| CI | Windows, macOS, and Linux at Python 3.11 and 3.13 |

The Phase-4 provider, worker, and transport implementations are in-tree. Release readiness remains
blocked until the configured-root, session-store, worker, official-client, and installed-wheel
stories have native green CI in all six cells. Windows is not an allowed-failure lane and the
complete public-service product story must not be skipped there.

Do not create another MCP runtime. `DefinedQuantService` remains the only dispatch boundary;
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
uv run pytest categories/market_data/simple_return
uv run python authoring/check_component.py
uv run python authoring/check_component.py --bless categories/market_data/simple_return
uv run python authoring/search_catalog.py "calculate returns from prices"
uv run python authoring/create_component.py \
  --category <category> --group <group> --slug <slug> --profile <profile>
uv run python authoring/create_category.py --id <id> --title "<title>"
uv run python authoring/export_catalog.py
uv run ruff check protocol shared categories authoring \
  .agents/skills/use-defined-quant/scripts .agents/skills/use-defined-quant/tests
uv run --project mcp_server ruff check mcp_server/src mcp_server/tests
uv run --no-editable mypy shared categories authoring/*.py
uv run --no-editable mypy -p defined_quant_protocol
uv build
git diff --check
```

## Invariants

1. Pydantic models in `component.py` are the single source of truth for inputs, outputs, units,
   and defaults. `contract.yaml` must not contain input or output type declarations. `README.md`
   may explain a convention but may never define one. Every `Output` extends the shared
   `defined_quant.types.ComponentOutput` provenance and visualization envelope.
2. Components are exactly `categories/<category>/<component>/`. Do not add a third component
   nesting level.
3. Every component has five visible files: `README.md`, `component.py`, `contract.yaml`,
   `evidence.yaml`, and `test_component.py`. Do not add empty optional files.
4. Trust uses separate bindings. `subject_hash` identifies behavior and enforceable contract;
   evidence content is bound separately; exact-commit CI proves what ran. Domain review remains a
   distinct assertion.
5. Status and evidence displays are derived. The only authored maturity field is `lifecycle`.
   Never add an authored composite status or badge.
6. Constraints are data, evaluated by `shared/validation.py` with a closed vocabulary. Never
   `eval`, `exec`, or import a component merely to evaluate its rules.
7. A state that makes output meaningless is blocking; a merely uncertain state is a warning.
8. No implicit default may change the answer: annualization factors, day counts, compounding,
   return kinds, and similar conventions must be supplied, visibly defaulted, or refused.
9. The `defined-quant` core wheel must include component contracts, evidence, and README files.
   Phase 4's optional `defined-quant-mcp` package is a separate distribution; this component-data
   requirement remains a core-wheel responsibility.
10. `pydantic` remains the only runtime dependency of the `defined-quant` core distribution. The
    MCP SDK and its dependencies belong only to the separate optional `defined-quant-mcp`
    distribution.
11. Test data is synthetic and seeded. Never commit vendor or scraped market data.
12. Preserve the honest preview copy: explicit evidence types, `Domain review: none`, the
    non-claims block, and the “Experimental Technical Preview” label.
13. The private website is not part of this project. Export static catalog data; do not add site
    routes, React code, or deployment state here.
14. Agent-host integration is catalog-wide. It discovers canonical component contracts and must
    not duplicate a component's financial logic, defaults, examples, or guidance.
15. Every contract has bounded `discovery` aliases, intents, input concepts, and output concepts.
    Intents and concepts are stable lower-snake-case identifiers. Search and filtering must read
    contracts only and must never import component code.
16. Every component declares at least one closed input and output semantic port through
    `defined_quant_protocol.semantic_port_metadata`. Never invent sibling `json_schema_extra`
    dialects, and never treat `depends_on` as proof that two component fields are compatible.

## Adding a component

1. Create the category with `authoring/create_category.py` if it does not exist.
2. Use `authoring/create_component.py`; do not hand-create the folder.
3. Complete all five files. The important work is explicit guidance, unsupported scope, and the
   blocking-versus-warning split.
4. Run the component tests and checker.
5. Bless the evidence binding only after tests pass, then open a pull request. Never push a
   component directly to `main`.

When a financial convention is unclear, stop and ask. A plausible guess is worse than an explicit
blocker in this project.
