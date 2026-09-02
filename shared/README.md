# Shared runtime

Transport-neutral foundations for the canonical methods-first runtime and the time-bounded legacy
component path:

- `registry/` loads, validates, searches, and joins inert authored or compiled registry metadata.
  Discovery never imports adapter or implementation code and never probes live availability.
- `schema_validation.py` evaluates the closed deterministic JSON Schema subset used at Method and
  Capability boundaries.
- `planning.py` validates Method proposals and explicit resolution preferences, applies trusted
  origin receipts, policy, restrictions, trust requirements, and explicit availability, and binds
  one exact Implementation per recipe step without ambient I/O.
- `execution.py` executes only retained compiled bindings through an `ExactAdapterCatalog`,
  validates canonical step and Method outputs, and publishes immutable Step and Run records. It has
  no runtime resolution or fallback.
- `method_records.py` is the session-scoped publish-once store for compiled Plan, Step, and complete
  Run records; it verifies hashes and cross-record bindings on retrieval.
- `method_service.py` composes one explicit Registry, policy, availability snapshot, trusted adapter
  catalog, runtime identity, and record store. It exposes Method search, inspection, compilation,
  execution, and record retrieval without a transport SDK.
- `adapter_discovery.py` reads installed distribution and entry-point metadata without loading an
  entry point.
- `adapter_artifacts.py` recomputes canonical installed-distribution manifests from package
  metadata, declared entry point, and resource bytes and emits an attestation only after an exact
  artifact match.
- `adapter_catalog.py` builds explicit availability facts and gates exact adapter invocation on the
  compiled Implementation, registry Adapter, policy admission, current availability, and artifact
  attestation. Installation by itself never grants trust.

- `types/` contains the canonical Pydantic financial, visualization, and datapoint-lineage types.
- `validation.py` evaluates the closed, declarative rules stored in each `contract.yaml`.
- `catalog.py` discovers components, loads their callables, caches stable-ID `subject_hash`
  bindings, and exposes explicit invalidation and fresh verification for trust boundaries.
- `discovery.py` searches contract metadata without importing calculations. It provides
  deterministic ranking, positive-versus-boundary match explanations, exact facets, and bounded
  results for both developer tools and agent adapters.
- `charts.py` renders trusted `VisualizationSpec` values as deterministic SVG.
- `operation_runtime.py` owns the one catalog-wide validation, invocation, byte construction,
  bundle reconciliation, and atomic unmanaged-publication path used by CLI and evidence.
  `execute_resolved_operation()` is its supported pre-resolved seam for consumers such as the
  legacy CLI that have already called `prepare_output_directory()` and `resolve_component()`;
  unresolved host requests should use `DefinedQuantService.execute_operation()` instead.
- `data_records.py`, `dataset_registry.py`, `operation_records.py`, `record_views.py`, and
  `session_cas.py` implement the closed V1 content roots, structural normalization, manifest
  reconciliation, bounded paging, and owner-private session storage without a transport SDK.
- `host_failures.py` is the transport-neutral closed failure and trust vocabulary.
- `service.py` composes canonical execution, one immutable process-lifetime discovery snapshot,
  and a lazily opened ephemeral data/record session without importing a transport SDK.
- `method_registry.py` and `plan_compiler.py` are the deprecated component-projection compiler
  retained for compatibility while old clients migrate to `registry/` and `planning.py`.
- `local_host_platform.py`, `worker_process.py`, and `worker_runtime.py` keep native filesystem and
  bounded-process mechanisms behind transport-neutral interfaces.
- `agent.py` is a compatibility-only re-export of the canonical `defined_quant_protocol` models.
- `managed_profiles/` contains closed, packaged execution allowlists rather than component-ID
  branches in production code.
- `plan_validation.py` validates atomic plans against those policies, component contracts, input
  models, and declarative constraints without invoking a calculation.

`DefinedQuantService` is the transport dispatch boundary and delegates the canonical surface to a
`MethodsRuntime`: `search_methods`, `inspect_method`, `compile_plan`, `execute_plan`, `get_plan`,
and `get_run`, alongside dataset and artifact retrieval. Explicitly named component methods remain
migration aliases only. The methods-first path does not project an old component into a Method;
it reads independently authored Method, Capability, Backend, Adapter, and Implementation records.
`MethodsRuntime` retains and returns the complete immutable Run record inside core. The public
service returns a compact execution receipt and exposes that record through deterministic bounded
`get_run` summaries and cursor-paged views, including individually selected output fields.

All seven bundled Methods are complete canonical slices. Their nine atomic DQ-native calculations
live in a separate adapter distribution and are invoked through the exact compiled-plan path. The
component folders and unmanaged workers remain compatibility-only until their dated removal
milestone. Provider-backed execution and credential brokering remain outside the current runtime.

`ComponentOutput` keeps assumptions, permanent disclosures, transformations, and state-dependent
warnings in separate fields. Every comparison in a contract warning rule must depend on component
input state; constant output context belongs in `disclosures`.

Protocol 0.4.0 extends the closed semantic-port vocabulary introduced in 0.3.0 while retaining the
separate authorization-only path introduced in 0.2.0. The packaged `simple_return_csv`
profile allowlists exactly `dq.market_data.simple_return` version `0.3.4`, subject
`ca4790d64d5405b7444eaebeee96b2f3257d7260efeb38262194c11617b9b87a`. It requires explicit draft
opt-in and semantic timestamps. Validation of one immutable, one-step plan produces a deterministic
receipt; a human may then create a manual-only approval, and all plan, policy, component, dataset,
receipt, and approval roots are revalidated before future execution.

Resolution timestamps are operational and excluded from the semantic plan hash. Dataset
timestamps and resolved answers are semantic and hash-bound. Protocol authorization models do not
recursively freeze every nested JSON value, so any nested mutation there requires validation
again. Phase-3 content-addressed records and host envelopes recursively freeze their hash-bound
JSON. This path performs no calculation and verifies no source. Component derivations can bind
output indices to input indices,
but their nullable citation IDs are not source evidence. Semantic ports prove field-level
compatibility for the Log Return to Historical Volatility boundary and refuse Simple Return's
different convention. That legacy compatibility check does not execute or authorize a chain. The
canonical executor executes registered single- and multi-step recipes through exact compiled
bindings.
Source-bound execution, populated citations, production non-native/provider adapters, credential
brokering, provider I/O, and portable independent reproduction remain deferred.

The folder is called `shared` so its purpose is clear when browsing the repository. Packaging maps
it to the public Python package name `defined_quant`; component code therefore imports
`defined_quant.validation`, `defined_quant.catalog`, `defined_quant.charts`, and
`defined_quant.types`.

The legacy component catalog runtime is filesystem-backed. Stable-ID hashes are cached per normalized
catalog root for ordinary component calls; the operation runner and managed plan validator call
`verify_subject()` to clear the cache, recompute from disk, and seed the verified value before use.
Path and explicit `ComponentRecord` hashes always remain fresh for authoring. Zip-imported or
single-file frozen packages are not a supported execution layout because discovery and preflight
also require readable contract files; memoization does not claim otherwise.
