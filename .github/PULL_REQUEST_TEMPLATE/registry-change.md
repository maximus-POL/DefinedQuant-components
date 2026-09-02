# Methods-first registry change

Closes #<!-- accepted proposal or report issue -->

## Outcome

<!-- Explain the governed financial or operational outcome in one focused paragraph. -->

## Exact subjects changed

<!-- Keep each identity separate. Use "new" or "removed" where an identity does not exist. -->

| Subject kind | ID | Semantic version | Identity before | Identity after |
|---|---|---|---|---|
| Method / Capability / Backend / Adapter / Implementation / Capability conformance / Evidence / Policy |  |  |  |  |

## Change scope

- [ ] Method financial meaning, user-facing contract, or capability-only recipe
- [ ] Capability atomic schema, semantic ports, constraints, or conformance
- [ ] Backend identity or non-secret operational boundaries
- [ ] Adapter metadata, mapping code, packaging, or review evidence
- [ ] Implementation registration, artifact pin, restrictions, or implementation evidence
- [ ] Policy, availability, resolution, compiled-plan, execution, or record behavior
- [ ] Static website projection or migration compatibility

## Sources of truth

- [ ] `method.yaml` is the only authored source for method meaning, conventions, defaults,
      constraints, interpretation, and its backend-neutral recipe
- [ ] Every recipe step references a Capability only; no Backend, Adapter, provider, transport,
      or Implementation is embedded in the Method
- [ ] `capability.yaml` is the only authored source for its atomic interface, semantic ports, and
      backend-neutral constraints
- [ ] Shared Method and Capability fields use an explicit loader schema binding; no schema was
      silently hand-copied into two authorities
- [ ] Category is taxonomy metadata only and does not choose an implementation location
- [ ] New public files, functions, and record classes use canonical unsuffixed names; semantic
      versions appear in record metadata

## Realization boundaries

- [ ] Each Backend record describes its correct kind and non-secret locality, transport, network,
      credential, licence, entitlement, and data-egress boundaries
- [ ] Adapter code only maps canonical inputs, invokes its exact backend, maps canonical outputs
      and failures, and cleans up resources
- [ ] No Adapter redefines methodology, invents defaults, resolves another Implementation, or
      performs silent fallback
- [ ] Each Implementation binds one exact Capability, Adapter, backend-role set, and
      implementation-owned artifact pin
- [ ] Installation is treated only as availability; policy admission, trust, artifact attestation,
      and current availability are independently required

## Conformance and evidence

- [ ] Capability conformance contains backend-independent known answers, boundaries, tolerances,
      and executable invariants
- [ ] Every `conformance_tested` Implementation runs the exact shared Capability suite through its
      trusted Adapter path
- [ ] Method, Capability, Adapter, and Implementation evidence bind their exact subject kind,
      ID, semantic version, and identity hash
- [ ] Method correctness, Capability conformance, Implementation evidence, Adapter review,
      provider authentication, data provenance, execution integrity, domain review, and
      independent reproduction remain separate claims
- [ ] No independent review, provider authentication, entitlement, or successful reproduction is
      self-awarded

## Provider and data safety

- [ ] A provider or optional-backend Adapter is registered only with real executable adapter code,
      packaging, tests, artifact evidence, and safe failure translation in this pull request
- [ ] No placeholder OpenBB, LSEG/Reuters, statsmodels, QuantLib, database, HTTP API, or external
      MCP support is advertised as registered
- [ ] Provider SDKs and optional dependencies remain outside the core distribution
- [ ] Credentials, tokens, secrets, connection strings, imports, executable control fields,
      arbitrary URLs, raw SQL, and arbitrary MCP tools are absent from registry and proposal data
- [ ] Test data is synthetic, seeded, and redistributable; no vendor or scraped provider data or
      response fixture is committed

## Migration and compatibility

- [ ] No new legacy five-file `categories/<category>/<component>/` folder was added
- [ ] A legacy folder was changed only as migration input or explicitly time-bounded compatibility
- [ ] Useful public Method IDs and website slugs remain stable where practical
- [ ] Any protocol, identity, hash, or compatibility break is listed below with its deletion or
      migration milestone
- [ ] The website consumes only deterministic static registry projections and does not import
      Adapter or Implementation code

<!-- Breaking changes and milestones: -->

## Verification

```bash
uv run pytest tests/registry tests/methods tests/capabilities \
  tests/implementations tests/adapters tests/product
uv run python authoring/build_registry_bundle.py --output dist/registry.json
uv run python authoring/export_component_pages.py
uv run ruff check protocol shared authoring adapters tests
uv run --no-editable mypy shared authoring/*.py
uv run --no-editable mypy -p defined_quant_protocol
uv build
git diff --check
```

- [ ] Applicable focused and product tests pass locally
- [ ] Registry loading, canonical ordering, hashes, and static exports are deterministic
- [ ] Installed-wheel isolation was tested when packaging or discovery changed
- [ ] Cross-platform impact and any CI-only verification are stated below

<!-- Verification notes: -->

## Author declaration

- [ ] I can license the code under Apache-2.0 and the prose under CC BY 4.0
- [ ] I described implementation-independent comparisons and conflicts of interest accurately
- [ ] I have not represented installed, planned, or fixture-only support as trusted production
      support

<!-- Conflicts or disclosures, if any: -->
