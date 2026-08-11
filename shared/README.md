# Shared runtime

Small, reusable foundations used by every component:

- `types/` contains the canonical Pydantic financial, visualization, and datapoint-lineage types.
- `validation.py` evaluates the closed, declarative rules stored in each `contract.yaml`.
- `catalog.py` discovers components, loads their callables, caches stable-ID `subject_hash`
  bindings, and exposes explicit invalidation and fresh verification for trust boundaries.
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
component's Pydantic models remain canonical for inputs, outputs, units, and defaults. Their
closed semantic-port extensions make field compatibility inspectable without turning
`depends_on` into an execution graph. The direct operation adapter remains unmanaged and cannot
mint plan approval, source-verification, or `ResearchBundle` claims.

`ComponentOutput` keeps assumptions, permanent disclosures, transformations, and state-dependent
warnings in separate fields. Every comparison in a contract warning rule must depend on component
input state; constant output context belongs in `disclosures`.

Protocol 0.3.0 adds semantic ports while retaining the separate authorization-only path introduced
in 0.2.0. The packaged `simple_return_csv_v1`
profile allowlists exactly `dq.market_data.simple_return` version `0.3.2`, subject
`8c1be7c15bb097ab027d00bc6dad7f175763d9a5787855df4c726c3618644b3d`. It requires explicit draft
opt-in and semantic timestamps. Validation of one immutable, one-step plan produces a deterministic
receipt; a human may then create a manual-only approval, and all plan, policy, component, dataset,
receipt, and approval roots are revalidated before future execution.

Resolution timestamps are operational and excluded from the semantic plan hash. Dataset
timestamps and resolved answers are semantic and hash-bound. Frozen models do not recursively
freeze nested JSON, so any nested mutation requires validation again. This path performs no
calculation and verifies no source. Component derivations can bind output indices to input indices,
but their nullable citation IDs are not source evidence. Semantic ports prove field-level
compatibility for the Log Return to Historical Volatility boundary and refuse Simple Return's
different convention. They do not execute or authorize a chain. Managed multi-step composition,
source-bound execution, populated citations, and portable `ResearchBundle` generation remain
deferred.

The folder is called `shared` so its purpose is clear when browsing the repository. Packaging maps
it to the public Python package name `defined_quant`; component code therefore imports
`defined_quant.validation`, `defined_quant.catalog`, `defined_quant.charts`, and
`defined_quant.types`.

The current catalog runtime is filesystem-backed. Stable-ID hashes are cached per normalized
catalog root for ordinary component calls; the operation runner and managed plan validator call
`verify_subject()` to clear the cache, recompute from disk, and seed the verified value before use.
Path and explicit `ComponentRecord` hashes always remain fresh for authoring. Zip-imported or
single-file frozen packages are not a supported execution layout because discovery and preflight
also require readable contract files; memoization does not claim otherwise.
