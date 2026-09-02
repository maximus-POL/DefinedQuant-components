# Contributing to Defined Quant

Defined Quant is authored methods first. A contribution starts with the professional financial
Method and the atomic backend-neutral Capabilities in its Recipe; executable code enters only as a
separately registered Implementation invoked through a trusted Adapter.

Do not add another five-file component under `categories/`. Those folders preserve the seven old
DQ-native calculations only as migration evidence and a temporary compatibility surface.

## Canonical sources

| Concern | Authored source |
|---|---|
| Financial meaning, user-facing contract, conventions, defaults, interpretation, and Recipe | `registry/methods/<category>/<method>/method.yaml` |
| Human Method explanation and worked examples | `registry/methods/<category>/<method>/README.md` and `examples.yaml` |
| Atomic typed interface and backend-neutral constraints | `registry/capabilities/<domain>/<capability>/capability.yaml` |
| Universal known answers, boundaries, tolerances, and invariants | `registry/capabilities/<domain>/<capability>/conformance.yaml` |
| Backend identity and operational boundaries | `registry/backends/<backend>.yaml` |
| Trusted mapping and invocation identity | `registry/adapters/<family>/<adapter>.yaml` and the adapter distribution |
| Exact Capability realization and artifact pin | `registry/implementations/<backend>/<implementation>.yaml` |
| Method-, Adapter-, and Implementation-specific claims | Separate records under `registry/evidence/` |

Category is taxonomy metadata for discovery and website navigation. It neither chooses an
Implementation nor determines where executable code lives.

## Propose and author a Method

Open a proposal before implementing a new Method or materially changing an existing one. Agree on
the financial question, conventions, unsupported scope, canonical inputs and outputs, Recipe, and
the smallest reusable Capabilities before adding executable code.

When authoring registry records:

1. Put financial purpose, methodology, assumptions, limitations, interpretation, explicit
   conventions, and authored defaults in `method.yaml`.
2. Make every Recipe step reference a Capability only. Never place a Backend, Adapter, provider,
   transport, or Implementation in a Method or Recipe.
3. Put each atomic input/output contract and backend-neutral constraint in `capability.yaml`.
4. Derive any Method field that crosses a Capability boundary with the exact
   `x-defined-quant-capability-field` directive. Do not hand-copy that schema into the Method.
5. Make every answer-changing convention required, visibly defaulted, or refused.
6. Keep constraints in the closed declarative vocabulary. Blocking means the result would be
   meaningless; warning means the result remains valid but uncertain.
7. Use bounded aliases and stable lower-snake-case intents and concepts for discovery.

## Implementations and Adapters

An Implementation realizes exactly one registered Capability. Its record binds the exact Backend,
Adapter, artifact, restrictions, dependency probes, trust assertions, and implementation evidence.
The Backend remains authoritative for network, credential, licence, entitlement, locality,
transport, and data-egress boundaries.

Adapter code may map canonical inputs, invoke one exact Backend, translate canonical outputs and
safe failures, and clean up resources. It may not redefine methodology, invent defaults, select a
different Implementation, or apply fallback.

Do not register OpenBB, LSEG, statsmodels, QuantLib, or another external Backend as supported until
its independently installable Adapter and evidence exist. Installation alone does not grant trust.

## Evidence and tests

Keep trust claims separate:

- Capability conformance contains implementation-independent known answers and boundaries.
- Method evidence supports Method-specific meaning or interpretation.
- Implementation evidence binds one exact Implementation and artifact.
- Adapter evidence covers input/output mapping, failure translation, and cleanup.
- Provider authentication, dataset provenance, execution integrity, domain review, and independent
  reproduction remain distinct runtime or review claims.

Use synthetic and seeded data. Do not commit licensed vendor data, scraped market data, provider
responses that cannot be redistributed, credentials, secrets, generated catalogs, or rendered
artifacts.

Run the methods-first checks relevant to the change:

```bash
uv run pytest tests/registry tests/methods tests/capabilities tests/implementations tests/adapters tests/product
uv run python authoring/build_registry_bundle.py --output dist/registry.json
uv run python authoring/export_component_pages.py
```

## Legacy compatibility maintenance

The five-file component authoring path is maintenance-only and is removed on **2026-12-31 or the
first 0.2.0 release, whichever comes first**. Do not use it for a new Method, Capability, Backend,
Adapter, or Implementation.

When a change must preserve an existing compatibility component, these are the bounded old checks:

```bash
uv run pytest categories/<category>/<component>
uv run python authoring/check_component.py categories/<category>/<component>
uv run python authoring/search_catalog.py "compatibility query"
uv run python authoring/export_catalog.py
```

Legacy `subject_hash` and evidence bindings apply only to that old bundled behavior. They never
become Method, Capability, Adapter, Implementation, Plan, Step, or Run identities.

## Pull requests

Keep each change focused and explain which source owns every new or changed claim. Do not advertise
an external Implementation without its Adapter and evidence, combine trust dimensions into a badge,
or add a second runtime. Shared protocol, registry loading, planning, execution, trust, packaging,
and CI policy remain maintainer-controlled during the preview.

By contributing, you agree that code is licensed under Apache-2.0 and authored explanations under
CC BY 4.0. Do not contribute material you cannot license.
