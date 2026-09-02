# defined-quant-mcp

`defined-quant-mcp` is the separate optional STDIO transport for the Defined Quant local MCP
alpha. It dispatches only through `DefinedQuantService`; the core `defined-quant` distribution
continues to depend only on Pydantic.

## Platform and release status

Windows, macOS, and Linux are the exact mandatory alpha release platforms. The native platform
providers, worker, and transport are in-tree, but this is not yet a release-support claim: the same
exact core, DQ-native adapter, and MCP wheels must pass the complete native matrix on Python 3.11 and 3.13 before
publication. No cell may skip, xfail, use `continue-on-error`, or substitute WSL/POSIX emulation
for native Windows.

## Install for development

From the repository root, use the locked projects instead of invoking a virtual-environment
executable by a platform-specific path:

```text
uv sync --locked
uv sync --project mcp_server --locked
uv run --project mcp_server defined-quant-mcp
```

The MCP project pins `mcp==2.0.0`, the exact compatible `defined-quant` core, and the independently
installed `defined-quant-adapter-dq-native`. The core still depends only on Pydantic. Installing an
adapter does not trust it: host policy, exact registry identity, availability, and artifact
attestation must all admit it before execution. Do not add the SDK or adapter dependency to the
core project, install `mcp-types` separately, or mix incompatible wheel versions.

## Install release artifacts

CI builds one exact core wheel, one DQ-native adapter wheel, and one MCP wheel, then installs those
same artifacts in every native acceptance cell without source-checkout import leakage. Once the
artifacts have passed all six cells, install the three explicit files into one Python 3.11–3.13
environment:

```text
python -m pip install PATH_TO_DEFINED_QUANT_WHEEL PATH_TO_DQ_NATIVE_ADAPTER_WHEEL PATH_TO_DEFINED_QUANT_MCP_WHEEL
```

Do not use source globs or rebuild one wheel per operating system: all three distributions are universal
Python wheels, while the locked MCP dependency graph selects the required native transitive wheels.

## Run

Run the server without granting access to caller-data roots:

```text
defined-quant-mcp
```

Launch-only settings may select one catalog, up to eight configured local data roots, and one
session-state base directory:

```text
defined-quant-mcp --catalog-root PATH --data-root PATH --state-root PATH
```

`--data-root` may be repeated up to eight times. All paths are launch-only configuration and never
tool arguments. Unicode and long local paths are supported. UNC paths, mapped network drives,
device namespaces, and other network roots fail closed for the alpha.

The canonical surface is `search_methods`, `inspect_method`, `compile_plan`, `execute_plan`,
`get_plan`, `get_run`, `get_dataset`, and `read_artifact`, plus dataset registration. A closed
proposal can carry bounded user-explicit resolution constraints, but it cannot carry host origin
receipts, automatic-resolution controls, credentials, URLs, SQL, imports, or executable code.
Compilation selects exact policy-admitted and available implementations without executing them.
Execution runs only those compiled identities through attested trusted adapters, never performs
runtime fallback, validates canonical results, and publishes complete immutable records.
`execute_plan` returns only a compact status and retained `run_ref`; it never places a potentially
large run record on the tool wire. `get_run` exposes bounded summary, step, warning, artifact,
dataset, output-field, and explicitly selected output views. Sequence and object outputs use
session-bound opaque cursors, stable ordering, exact counts, and digests so every page remains
beneath the tool-result ceiling without silently truncating the retained record.
`read_artifact` likewise returns one digest-bound base64 chunk with its byte offset, total size,
chunk digest, completion state, and session-bound next cursor. The current full-verification reader
refuses artifacts above 8 MiB before loading them; adding a trusted streaming artifact store can
raise that storage boundary later without changing the public chunk contract.

Component-named tools remain temporary compatibility surfaces only until 2026-12-31 or the first
0.2.0 release, whichever comes first. No production external-provider adapter is advertised yet.

## Transport and trust boundary

The transport is local STDIO only. Stdout is reserved for MCP frames. Operational stderr records
contain a closed set of redacted audit fields and never contain request values, paths, exception
text, tracebacks, credentials, or environment values. Caller data is unverified. A governed run
record proves its compiled identities and canonical validation path; it does not by itself prove
provider authentication, financial correctness, domain review, or independent reproduction. The
legacy worker is a bounded native process tree, not an operating-system security sandbox;
installed compatibility component code runs with the local user's permissions.

WSL2 is an unsupported convenience for running the Linux build under Linux semantics. It is not a
Windows support path and never validates Windows DACLs, reparse handling, locking, publication,
cleanup, Job Objects, binary STDIO, or wheel installation.
