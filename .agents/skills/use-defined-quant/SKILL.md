---
name: use-defined-quant
description: Autonomously discover, compare, compose, inspect, execute, and render deterministic Defined Quant financial components through their canonical contracts. Use when Codex needs to turn a user's financial intent into candidate calculations, search or filter a large component catalog, choose one or more compatible methods, reject unsupported or boundary-only matches, resolve required conventions, run selected components, preserve provenance and warnings, or display component-declared visualizations.
---

# Use Defined Quant

Treat the catalog as the source of financial behavior. Never reproduce a component's formula,
invent a substitute calculation, alter a result, suppress a warning, or redraw a declared
visualization from a separate transformation.

Run the bundled commands from the public `components/` project root.

## Discover candidates autonomously

1. Translate the request into a short query describing the financial operation and desired
   result. Preserve method names, financial nouns, units, frequency, and requested output
   semantics. Do not add a convention the user did not state.
2. Retrieve a small candidate set:

```bash
uv run python .agents/skills/use-defined-quant/scripts/catalog.py search \
  "requested operation and result" --limit 5
```

Apply `--category`, `--group`, `--profile`, `--tag`, `--intent`, `--input-concept`,
`--output-concept`, or `--lifecycle` only when the request establishes that filter. Repeat a
filter to accept any of several values within that field. Filters across different fields narrow
the result together.

Keep the initial limit small. When `total_matches` exceeds `returned_count`, refine with reported
facets before increasing the limit; do not load the entire catalog into context.

3. Read every returned match explanation:
   - Positive matches explain why the component may satisfy the request.
   - Boundary matches come from prohibitions, limitations, or unsupported scope. They are
     exclusion evidence, never positive evidence.
   - Unmatched query terms identify intent that discovery could not substantiate.
4. Reject a boundary-only candidate. Do not select a high-ranked candidate when a boundary match
   conflicts with the request.
5. Inspect the top plausible candidates, not merely the first result:

```bash
uv run python .agents/skills/use-defined-quant/scripts/catalog.py show dq.category.component
```

Compare purpose, input and output concepts, guidance, assumptions, limitations, required
questions, canonical schemas, lifecycle, and evidence. Select a component only when the request
is positively supported and outside `do_not_use_when` and `unsupported_scope`.

If no candidate fits, broaden safely: remove one nonessential filter, replace a narrow phrase with
an established financial synonym, or use a broader concept identifier that is actually present
in the reported facets. Inspect the new explanations. Stop and say that no supported component
was found when broadening changes the requested method or still produces only boundary matches.

Use `catalog.py list --limit N` with the same filters for structured browsing. Discovery does not
import component Python. `show` imports only the selected component to expose its canonical
Pydantic schemas and subject hash.

## Plan compositions

Use multiple components only when the request has multiple supported steps or no one component
can satisfy it. Discovery concepts may identify a candidate chain, but they do not establish
compatibility:

1. Match an upstream component's output concept to a downstream component's input concept.
2. Inspect both canonical Pydantic models and verify units, cardinality, shape, ordering,
   frequency, conventions, and provenance compatibility. Do not infer compatibility from
   `contract.yaml` dependency metadata.
3. Resolve every required question for every component before execution.
4. Execute in dependency order and preserve each intermediate result and subject hash.

Never infer compatibility from similar field names. Do not insert an unregistered conversion or
transformation between components. If the canonical models do not establish compatibility, ask
for direction or report that the composition is unsupported.

The current operation protocol executes one component per unmanaged request. Separate successful
operations do not create an approved multi-step plan or a verified composition.

## Resolve ambiguity

Ask the selected contract's required questions when the corresponding value is absent. Never
infer an answer-changing convention, unit, ordering policy, annualization factor, day count,
compounding rule, price adjustment policy, or similar input from unstated context. Advisory
questions may improve interpretation but do not become blockers unless the contract says so.

## Execute and render

1. Use `catalog.py show` to capture the selected component's exact ID, version, `subject_hash`,
   input schema, and output schema.
2. Prepare an operation request that satisfies `defined_quant_protocol.OperationRequest`. The
   request is transport only; the selected component's Pydantic `Inputs` model remains the source
   of truth. A typical shape is:

```json
{
  "schema_version": 1,
  "component": {
    "id": "dq.category.component",
    "version": "0.1.0",
    "subject_hash": "<64 lowercase hexadecimal characters>"
  },
  "input": {},
  "provenance": {
    "source_kind": "user_attachment",
    "interpretation_method": "caller_structured",
    "verification_status": "unverified",
    "label": "User-supplied input",
    "references": [],
    "assumptions": []
  },
  "artifacts": {
    "kind": "svg",
    "selection": "all"
  }
}
```

Use the exact values returned by inspection; never copy the placeholder identity. Set
`verification_status` to `caller_confirmed` only when the caller explicitly confirms the mapping.
It remains a caller assertion, not source verification. When AI interprets source values, use
`ai_interpreted`, keep status `unverified`, and record every inferred convention in `assumptions`.

3. Run the generic adapter. Output location and catalog location are runtime settings outside the
   semantic request:

```bash
uv run python .agents/skills/use-defined-quant/scripts/run_component.py \
  --request ./operation-request.json \
  --output-dir ./operation-output
```

Pass `-` to `--request` to read the object from standard input. Use `--catalog-root` only when the
host must select a non-default catalog. The selected output directory must not already exist. The
runner stages every member before publishing the complete directory and deliberately refuses
mutable overwrite behavior. If the host cannot provide atomic no-replace directory publication,
the runner returns a typed failure instead of falling back to replacement. None of the host
settings belongs inside the request or its operation hash.

4. For a runner-handled non-zero exit, read the typed `OperationFailure` from standard error,
   report its stable code and message, and do not calculate a fallback answer. A process that
   cannot start, is killed, or fails below the Python runner may have no protocol envelope and must
   be reported as an operational failure. On success, standard output contains an
   `OperationSuccess` whose manifest is also written to `manifest.json`.
5. Read `result.json` and `manifest.json`. Present the result with its component ID, version,
   subject hash, unit, assumptions, disclosures, transformations, state-dependent warnings,
   datapoint derivations, and any component-specific interpretation fields. Verify content hashes
   before trusting materialized members.
6. Manifest member paths are normalized relative POSIX paths such as `result.json` or
   `01-chart.svg`; they never contain the host output root. Resolve a member against the selected
   output directory only for local access. Do not write that resolved host path back into the
   manifest, request, or operation hash. Preserve artifact title and alt text when displaying it.

The adapter validates the canonical `Inputs`, invokes the catalog-declared callable, validates the
canonical `Output`, refuses an exact component identity mismatch, verifies result identity and
subject-hash provenance, and renders requested component-declared visualizations with the shared
trusted SVG renderer. Components without visualizations still produce normalized input,
structured result, and manifest members. Production execution is catalog-wide and must not branch
on a particular component ID.

## Data and interpretation boundaries

- Keep data acquisition separate from calculation. A host tool may supply observations, but
  never claim that Defined Quant fetched or validated them unless the selected component says so.
- Preserve caller order and source semantics unless the component explicitly declares a
  transformation.
- Describe synthetic inputs as synthetic and sourced inputs with their actual provenance.
- Treat provenance references as caller context, not cell-level citations or authenticated source
  bindings. A component derivation may identify exact input indices, but a null or caller-populated
  citation ID does not prove that a CSV cell, filing passage, or provider response supplied a value.
- Do not claim an approved `AnalysisPlan`, validation receipt, authorization, deterministic
  evaluation, or portable `ResearchBundle`; those records are not part of this protocol version.
- Do not turn a deterministic calculation into financial advice or imply evidence, review, or
  scope beyond the component's contract.

## Repository location

`.agents` contains repository-level Codex integration metadata, not financial calculations.
Financial components remain under `categories/<category>/<component>/`. The generic scripts
delegate retrieval and ranking to the canonical `defined_quant.discovery` API. Adding a conforming
component therefore does not require another Codex skill or a bespoke adapter change.
