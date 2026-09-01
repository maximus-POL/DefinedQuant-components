# defined-quant-mcp

`defined-quant-mcp` is the separate optional STDIO transport for the Defined Quant local MCP
alpha. It dispatches only through `DefinedQuantService`; the core `defined-quant` distribution
continues to depend only on Pydantic.

## Platform and release status

Windows, macOS, and Linux are the exact mandatory alpha release platforms. The native providers,
worker, and transport are in-tree, but this is not yet a release-support claim: the same exact core
and MCP wheels must pass the complete native matrix on Python 3.11 and 3.13 before publication. No
cell may skip, xfail, use `continue-on-error`, or substitute WSL/POSIX emulation for native Windows.

## Install for development

From the repository root, use the locked projects instead of invoking a virtual-environment
executable by a platform-specific path:

```text
uv sync --locked
uv sync --project mcp_server --locked
uv run --project mcp_server defined-quant-mcp
```

The MCP project pins `mcp==2.0.0` and the exact compatible `defined-quant` core. Do not add the SDK
to the core project, install `mcp-types` separately, or mix a transport wheel with a different core
version.

## Install release artifacts

CI builds one exact core wheel and one exact MCP wheel, then installs that same pair in every
native acceptance cell without source-checkout import leakage. Once those artifacts have passed
all six cells, install both explicit files into one Python 3.11–3.13 environment:

```text
python -m pip install PATH_TO_DEFINED_QUANT_WHEEL PATH_TO_DEFINED_QUANT_MCP_WHEEL
```

Do not use source globs or rebuild one wheel per operating system: both distributions are universal
Python wheels, while the locked MCP dependency graph selects the required native transitive wheels.

## Run

Run the server with no local-file access:

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

The additive `compile_plan` tool accepts only a closed `PlanProposalV1`. It deterministically
binds one approved, available registered implementation per backend-neutral recipe step and
returns `compiled`, `needs_information`, or `refused`. It does not execute the compiled plan,
probe a provider, or create a run record. Existing component execution remains a separate
unmanaged tool.

## Transport and trust boundary

The transport is local STDIO only. Stdout is reserved for MCP frames. Operational stderr records
contain a closed set of redacted audit fields and never contain request values, paths, exception
text, tracebacks, credentials, or environment values. Operations are unmanaged and caller data is
unverified. The worker is a bounded native process tree, not an operating-system security sandbox;
installed component code runs with the local user's permissions.

WSL2 is an unsupported convenience for running the Linux build under Linux semantics. It is not a
Windows support path and never validates Windows DACLs, reparse handling, locking, publication,
cleanup, Job Objects, binary STDIO, or wheel installation.
