---
name: use-defined-quant
description: Autonomously turn natural-language financial requests, pasted data, and readable attachments into validated Defined Quant component operations and deterministic views. Use when Codex needs to interpret caller-supplied financial data, discover or compare catalog components, resolve required conventions, run selected calculations, preserve provenance and warnings, or display component-declared visualizations without requiring the user to know component IDs, schemas, adapters, or rendering commands.
---

# Use Defined Quant

Treat the catalog as the source of financial behavior and presentation. Never reproduce a
component's formula, invent a substitute calculation, alter a result, suppress a warning, redraw a
declared visualization, or create an alternative dashboard from a separate transformation.

Run the bundled commands from the public `components/` project root.
The bundled scripts activate that checkout as the runtime package before importing components, so
they do not silently execute a stale installed `defined_quant` build.

## Keep the user interface financial

Accept ordinary requests such as “compare the earnings in this file and show me.” Do not require
the user to name a component, mention normalization, construct JSON, choose a renderer, or know the
operation protocol.

When the user authorizes AI interpretation, inspect readable prompt data or attachments and map
them into the selected component's canonical input. Make every inferred convention explicit in the
operation input and provenance assumptions. Mark the interpretation `unverified`; do not describe
it as validated source data. Ask only when an unresolved convention materially changes the answer
and cannot reasonably be inferred under the user's authorization.

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

Use multiple components when the request has multiple supported steps or no one component can
satisfy it, and only when their declared concepts form an explicit chain:

1. Match an upstream component's output concept to a downstream component's input concept.
2. Inspect both contracts and verify units, shape, ordering, frequency, conventions, and
   provenance compatibility.
3. Resolve every required question for every component before execution.
4. Execute in dependency order and preserve each intermediate result and subject hash.

Never infer compatibility from similar field names. Do not insert an unregistered conversion or
transformation between components. If the concepts or contracts do not establish compatibility,
ask for direction or report that the composition is unsupported.

## Resolve ambiguity

Ask the selected contract's required questions when the corresponding value is absent. Never
infer an answer-changing convention, unit, ordering policy, annualization factor, day count,
compounding rule, price adjustment policy, or similar input from unstated context. Advisory
questions may improve interpretation but do not become blockers unless the contract says so.

## Execute and render

1. Prepare an input object that satisfies the inspected schema. The serialized input is transport
   only; the selected component's `Inputs` model remains the source of truth.
2. Create one `OperationRequest` object. Record how the values were obtained:
   - prompt or pasted values: `source_kind: user_prompt`;
   - attached files: `source_kind: user_attachment`;
   - provider tools: `source_kind: external_provider`;
   - component fixtures: `source_kind: synthetic`.

   Use `interpretation_method: ai_interpreted` for model mapping,
   `caller_structured` for already-canonical input, and `adapter_normalized` only for a deterministic
   source adapter. Use `verification_status: unverified` unless caller confirmation or hash-bound
   source verification actually occurred.
3. Run the generic adapter with the request envelope:

```bash
uv run python .agents/skills/use-defined-quant/scripts/run_component.py \
  --request /absolute/path/to/operation-request.json
```

The request contains `component_id`, `input`, `provenance`, host `view` capabilities, and an
absolute `output_dir`. Pass `-` to `--request` to read it from standard input. Set `overwrite: true`
only when the caller explicitly intends to replace files at the selected output path.

The default `view.use_case: chat` selects a component-declared responsive HTML view when the
component and host support it. Declare `view.supported_media_types` when host capabilities are
known; deterministic selection falls back to the component-declared portable view. For an inline
Codex chat view, choose the current thread-scoped visualization directory as `output_dir` so the
selected HTML file can be embedded without copying or rewriting it.

4. Treat a non-zero exit as a refusal or failure. Parse the typed `OperationFailure`; do not
   calculate a
   fallback answer.
5. Parse the typed `OperationSuccess`, then read the hash-bound `result.json` and `manifest.json`.
   Present the result with its component ID, version, subject hash, operation hash, source
   provenance, unit, assumptions, transformations, warnings, and component-specific interpretation
   fields.
6. When the manifest contains an artifact with `role: "primary"`, display that artifact first and
   use it as the prescribed component view. Do not restyle, redraw, reorder, summarize into a new
   dashboard, or replace it with an AI-authored presentation. Display supporting artifacts only
   when the caller requests individual charts or no prescribed dashboard exists. For a selected
   `text/html` artifact in the current Codex thread visualization directory, emit
   `::codex-inline-vis{file="<artifact filename>"}` on its own line. For an SVG artifact, embed its
   absolute `path` from the manifest with Markdown image syntax. Preserve its title and alt text
   when describing it.

The adapter validates the closed operation request and canonical `Inputs`, invokes the
catalog-declared callable, validates the canonical `Output`, verifies identity and subject-hash
provenance, binds the semantic operation independently of local paths, and renders every declared
visualization with the shared trusted SVG renderer. When a component declares a deterministic
dashboard, the adapter also renders the prescribed chart order, table columns, formatting, notes,
and layout in every declared format. A closed `ViewBundleSpec` selects responsive HTML by default
for supported chat hosts and retains SVG as the portable fallback. Components without
visualizations still produce normalized input, structured result, and manifest files.

## Data and interpretation boundaries

- Keep data acquisition separate from calculation. A host tool may supply observations, but
  never claim that Defined Quant fetched or validated them unless the selected component says so.
- Treat AI interpretation as an explicit, replaceable ingestion layer. Preserve its assumptions in
  `OperationProvenance`; a future trusted adapter can replace that layer without changing the
  component or renderer.
- Preserve caller order and source semantics unless the component explicitly declares a
  transformation.
- Describe synthetic inputs as synthetic and sourced inputs with their actual provenance.
- Do not turn a deterministic calculation into financial advice or imply evidence, review, or
  scope beyond the component's contract.

## Repository location

`.agents` contains repository-level Codex integration metadata, not financial calculations.
Financial components remain under `categories/<category>/<component>/`. The generic scripts
delegate retrieval and ranking to the canonical `defined_quant.discovery` API. Adding a conforming
component therefore does not require another Codex skill or a bespoke adapter change.
