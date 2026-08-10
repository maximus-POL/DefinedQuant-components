# Shared runtime

Small, reusable foundations used by every component:

- `types/` contains the canonical Pydantic financial, visualization, and datapoint-lineage types.
- `validation.py` evaluates the closed, declarative rules stored in each `contract.yaml`.
- `catalog.py` discovers components, loads their callables, and produces deterministic
  `subject_hash` bindings.
- `discovery.py` searches contract metadata without importing calculations. It provides
  deterministic ranking, positive-versus-boundary match explanations, exact facets, and bounded
  results for both developer tools and agent adapters.
- `charts.py` renders trusted `VisualizationSpec` values as deterministic SVG.
- `agent.py` is a compatibility-only re-export of the canonical `defined_quant_protocol` models.
- `managed_profiles/` contains closed, packaged execution allowlists rather than component-ID
  branches in production code.
- `plan_validation.py` validates atomic plans against those policies, component contracts, input
  models, and declarative constraints without invoking a calculation.

The generic host adapter composes these catalog, validation, and rendering foundations with the
separate `defined_quant_protocol` package. Protocol records do not become component truth: each
component's Pydantic models remain canonical for inputs, outputs, units, defaults, and structural
compatibility. The direct operation adapter remains unmanaged and cannot mint plan approval,
source-verification, or `ResearchBundle` claims.

Protocol 0.2.0 adds a separate authorization-only path. The packaged `simple_return_csv_v1`
profile allowlists exactly `dq.market_data.simple_return` version `0.3.0`, subject
`7259991b8fa3617d27e13109208258f536cd706c1d65e9008964ed072d8dfaee`. It requires explicit draft
opt-in and semantic timestamps. Validation of one immutable, one-step plan produces a deterministic
receipt; a human may then create a manual-only approval, and all plan, policy, component, dataset,
receipt, and approval roots are revalidated before future execution.

Resolution timestamps are operational and excluded from the semantic plan hash. Dataset
timestamps and resolved answers are semantic and hash-bound. Frozen models do not recursively
freeze nested JSON, so any nested mutation requires validation again. This path performs no
calculation and verifies no source. Component derivations can bind output indices to input indices,
but their nullable citation IDs are not source evidence. Semantic port metadata, multi-step
composition, source-bound execution, populated citations, and portable `ResearchBundle` generation
remain deferred.

The folder is called `shared` so its purpose is clear when browsing the repository. Packaging maps
it to the public Python package name `defined_quant`; component code therefore imports
`defined_quant.validation`, `defined_quant.catalog`, `defined_quant.charts`, and
`defined_quant.types`.
