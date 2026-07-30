---
name: use-defined-quant
description: Autonomously discover, compare, compose, inspect, execute, and render deterministic Defined Quant financial components through their canonical contracts. Use when Codex needs to turn a user's financial intent into candidate calculations, search or filter a large component catalog, choose one or more compatible methods, reject unsupported or boundary-only matches, resolve required conventions, run selected components, preserve provenance and warnings, or display component-declared visualizations.
---

# Use Defined Quant

Treat the catalog as the source of financial behavior and presentation. Never reproduce a
component's formula, invent a substitute calculation, alter a result, suppress a warning, redraw a
declared visualization, or create an alternative dashboard from a separate transformation.

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
2. Run the generic adapter with the stable component ID:

```bash
uv run python .agents/skills/use-defined-quant/scripts/run_component.py \
  --component dq.category.component \
  --input /absolute/path/to/input.json \
  --output-dir /absolute/path/to/new-output-directory
```

Pass `-` to `--input` to read the object from standard input. Use `--overwrite` only when the
caller explicitly intends to replace files at the selected output path.

The default `--view-use-case chat` selects a component-declared responsive HTML view when the
component and host support it. Use repeated `--supported-media-type` arguments when host
capabilities are known; deterministic selection falls back to the component-declared portable
view. For an inline Codex chat view, choose the current thread-scoped visualization directory as
`--output-dir` so the selected HTML file can be embedded without copying or rewriting it.

3. Treat a non-zero exit as a refusal or failure. Report the structured error; do not calculate a
fallback answer.
4. Read `result.json` and `manifest.json`. Present the result with its component ID, version,
subject hash, unit, assumptions, transformations, warnings, and any component-specific
interpretation fields.
5. When the manifest contains an artifact with `role: "primary"`, display that artifact first and
   use it as the prescribed component view. Do not restyle, redraw, reorder, summarize into a new
   dashboard, or replace it with an AI-authored presentation. Display supporting artifacts only
   when the caller requests individual charts or no prescribed dashboard exists. For a selected
   `text/html` artifact in the current Codex thread visualization directory, emit
   `::codex-inline-vis{file="<artifact filename>"}` on its own line. For an SVG artifact, embed its
   absolute `path` from the manifest with Markdown image syntax. Preserve its title and alt text
   when describing it.

The adapter validates the canonical `Inputs`, invokes the catalog-declared callable, validates the
canonical `Output`, verifies identity and subject-hash provenance, and renders every declared
visualization with the shared trusted SVG renderer. When a component declares a deterministic
dashboard, the adapter also renders the prescribed chart order, table columns, formatting, notes,
and layout in every declared format. A closed `ViewBundleSpec` selects responsive HTML by default
for supported chat hosts and retains SVG as the portable fallback. Components without
visualizations still produce normalized input, structured result, and manifest files.

## Data and interpretation boundaries

- Keep data acquisition separate from calculation. A host tool may supply observations, but
  never claim that Defined Quant fetched or validated them unless the selected component says so.
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
