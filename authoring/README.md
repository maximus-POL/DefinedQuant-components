# Registry authoring

The authoring tools validate and publish Defined Quant's methods-first registry. Authored YAML is
data only: discovery and inspection never import Adapter or Implementation code, probe credentials,
or contact a Backend.

There is no `dq` command. Run the scripts below with the project environment from the repository
root.

## Authored authorities

The registry deliberately separates concerns:

| Path | Authority |
|---|---|
| `registry/methods/<category>/<method>/method.yaml` | Financial meaning, user-facing schemas, methodology, conventions, defaults, constraints, interpretation, and backend-neutral Recipe |
| `registry/methods/<category>/<method>/README.md` | Human explanation of that Method |
| `registry/methods/<category>/<method>/examples.yaml` | Structured Method examples |
| `registry/capabilities/<domain>/<capability>/capability.yaml` | Atomic backend-neutral interface and constraints |
| `registry/capabilities/<domain>/<capability>/conformance.yaml` | Universal known answers, boundaries, tolerances, and invariants |
| `registry/backends/<backend>.yaml` | Backend identity and operational boundaries |
| `registry/adapters/<family>/<adapter>.yaml` | Trusted Adapter family, distribution, entry point, and dispatch identity |
| `registry/implementations/<backend>/<implementation>.yaml` | Exact Capability realization, Backend and Adapter bindings, sole artifact pin, restrictions, probes, and trust assertions |
| `registry/evidence/` | Separate Method-, Adapter-, and Implementation-scoped claims |

`registry/taxonomy/categories.yaml` owns discovery and website-navigation categories. A category is
not an implementation namespace and does not imply that a Method has handwritten Python code.

## Author a Method and Recipe

Start with the financial contract. A Method owns its purpose, canonical user-facing inputs and
outputs, methodology, interpretation, assumptions, limitations, conventions, authored defaults,
constraints, and Recipe.

Each Recipe step references exactly one Capability. Backends, providers, Adapters, transports, and
Implementations never appear in a Method or Recipe. When a Method field crosses a Capability
boundary, author only an `x-defined-quant-capability-field` directive naming the exact Capability,
direction, and field. The registry loader expands the Capability-owned schema and records an
auditable binding; a Method may not override it.

Method-only fields remain authored in `method.yaml`. A convention that can change the answer must
be a required input or a visible authored default. Constraints use the closed declarative
vocabulary and may never execute code.

## Author a Capability and conformance

A Capability is one reusable atomic operation. Its schemas define exact typed inputs, outputs, and
semantic ports. Put backend-neutral domain constraints in `capability.yaml` and put known answers,
boundary cases, tolerances, and invariants that every Implementation must pass in
`conformance.yaml`.

Do not place Method interpretation, Backend behavior, provider responses, or implementation-only
evidence in Capability conformance.

## Register a Backend, Adapter, and Implementation

Backend records own locality, transport, network, credential, licensing, entitlement, and
data-egress boundaries. They contain no secrets.

Adapter records identify the trusted distribution and dispatch surface. Adapter code lives in an
independently installable distribution where practical and owns only canonical mapping, one exact
Backend invocation, safe failure translation, and cleanup.

Implementation records bind one Capability to exact Backend roles and one Adapter. The
Implementation alone owns the exact distribution version and artifact hash. Its dependency probes
may describe realization-specific installation facts but must not duplicate Backend operational
requirements.

Do not add a production external-Backend record before the real Adapter and scoped evidence exist.
An installed package is an availability fact, not policy admission or trust.

## Validate and build the registry

Run the focused methods-first suite:

```bash
uv run pytest tests/registry tests/methods tests/capabilities tests/implementations tests/adapters tests/product
```

Compile authored YAML into the deterministic metadata-only bundle used by the core wheel:

```bash
uv run python authoring/build_registry_bundle.py --output dist/registry.json
```

The build validates closed protocol records, exact references, canonical ordering, schema
authority, Recipe dataflow, evidence subjects, Backend/Implementation consistency, and prohibited
secret or executable control fields. Runtime discovery reads the compiled inert JSON and does not
need PyYAML.

## Export website Component pages

The website term “Component page” is a presentation projection, not an executable component. Export
the deterministic joined record with:

```bash
uv run python authoring/export_component_pages.py
```

The exporter joins Methods, Capabilities, registered Implementations, backend kinds, separated
evidence, and trust boundaries without importing Adapter code. It reports registered support, not
installation-specific availability. The private website consumes only the generated static JSON.

## Legacy compatibility maintenance

The old five-file authoring and catalog tools are maintenance-only and are removed on
**2026-12-31 or the first 0.2.0 release, whichever comes first**. Do not add a Method or extend the
component hierarchy through this path. The seven folders under
`categories/<category>/<component>/` are preserved behavior fixtures, not financial sources of
truth.

When repairing that temporary compatibility surface, the bounded old commands are:

```bash
uv run python authoring/check_component.py
uv run python authoring/check_component.py categories/<category>/<component>
uv run python authoring/search_catalog.py "compatibility query"
uv run python authoring/export_catalog.py
```

Legacy `component.py`, `contract.yaml`, `evidence.yaml`, and `subject_hash` govern only the old
direct execution path until deletion; they never define the canonical Method or Capability.
