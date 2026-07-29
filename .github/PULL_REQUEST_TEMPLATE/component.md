# Component pull request

Closes #<!-- accepted proposal issue -->

## What this adds or changes

<!-- One focused paragraph. -->

## Component

- **ID:** `dq.<category>.<slug>`
- **Version:** `0.1.0`
- **Profile:** `convention | statistic | pricing_model | time_series | diagnostic`
- **Lifecycle:** `draft`

## Five-file folder

- [ ] `README.md` explains the formula, worked example, and limitations
- [ ] `component.py` defines Pydantic `Inputs` / `Output` and the callable
- [ ] `contract.yaml` contains identity, guidance, constraints, and display hints
- [ ] `evidence.yaml` contains named evidence and agent cases
- [ ] `test_component.py` defines every evidence-referenced test
- [ ] No empty optional file or generated artifact is committed

## Canonical sources

- [ ] `contract.yaml` contains no input/output type declarations
- [ ] `README.md` explains conventions but defines none
- [ ] Every guidance field reference exists in `Inputs.model_fields`
- [ ] Units, conventions, and answer-changing defaults are explicit in Pydantic models

## Scope and evidence

- [ ] Every material ambiguity is a required question or visible declared default
- [ ] Unsupported scope is explicit
- [ ] Blocking means meaningless output; warning means valid but uncertain output
- [ ] Every evidence ID names a test, provenance, and tolerance policy
- [ ] `validated_subject_hash` was written by `check_component.py --bless`, not by hand
- [ ] No independent human or domain review is self-awarded

## Boundaries

- [ ] No new runtime dependency
- [ ] Test data is synthetic and seeded
- [ ] No vendor data, scraped data, binaries, generated catalogs, rendered outputs, or secrets
- [ ] No protected path was changed without maintainer agreement (`shared/`, `authoring/`,
      `.github/`, category READMEs, repository policy, or review records)
- [ ] No private website code is included

## Local checks

```bash
uv run pytest categories/<category>/<component>
uv run python authoring/check_component.py
uv run ruff check shared categories authoring
uv run --no-editable mypy shared categories authoring/*.py
```

- [ ] All checks pass locally

## Author declaration

- [ ] I can license the code under Apache-2.0 and the prose under CC BY 4.0
- [ ] I disclosed implementation-independent comparisons accurately
- [ ] Relevant conflicts of interest are stated below

<!-- Conflicts, if any: -->
