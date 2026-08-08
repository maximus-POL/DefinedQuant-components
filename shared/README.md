# Shared runtime

Small, reusable foundations used by every component:

- `types/` contains the canonical Pydantic financial and visualization types.
- `validation.py` evaluates the closed, declarative rules stored in each `contract.yaml`.
- `catalog.py` discovers components, loads their callables, and produces deterministic
  `subject_hash` bindings.
- `discovery.py` searches contract metadata without importing calculations. It provides
  deterministic ranking, positive-versus-boundary match explanations, exact facets, and bounded
  results for both developer tools and agent adapters.
- `charts.py` renders trusted `VisualizationSpec` values as deterministic SVG.
- `agent.py` is a compatibility-only re-export of the canonical `defined_quant_protocol` models.

The generic host adapter composes these catalog, validation, and rendering foundations with the
separate `defined_quant_protocol` package. Protocol records do not become component truth: each
component's Pydantic models remain canonical for inputs, outputs, units, defaults, and structural
compatibility. The current operation adapter is unmanaged and cannot mint plan approval,
source-verification, or `ResearchBundle` claims.

The folder is called `shared` so its purpose is clear when browsing the repository. Packaging maps
it to the public Python package name `defined_quant`; component code therefore imports
`defined_quant.validation`, `defined_quant.catalog`, `defined_quant.charts`, and
`defined_quant.types`.
