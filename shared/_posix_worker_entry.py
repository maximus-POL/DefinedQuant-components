"""Import-safe POSIX bootstrap that installs limits before worker user code."""

from __future__ import annotations

import os
import resource
import sys

_ALLOWED_ENVIRONMENT = {
    "NO_COLOR",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONNOUSERSITE",
    "PYTHONUTF8",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
}


def _arguments() -> tuple[int, int, bool]:
    if (
        len(sys.argv) != 7
        or sys.argv[1] != "--control-fd"
        or sys.argv[3] != "--memory-limit"
        or sys.argv[5] != "--address-space-limit"
        or sys.argv[6] not in {"0", "1"}
    ):
        raise SystemExit(70)
    try:
        control_fd = int(sys.argv[2])
        memory_limit = int(sys.argv[4])
    except ValueError:
        raise SystemExit(70) from None
    if control_fd < 3 or memory_limit <= 0:
        raise SystemExit(70)
    return control_fd, memory_limit, sys.argv[6] == "1"


def main() -> int:
    control_fd, memory_limit, address_space_limit = _arguments()
    for name in tuple(os.environ):
        if name not in _ALLOWED_ENVIRONMENT:
            del os.environ[name]
    if address_space_limit:
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from defined_quant.worker_entry import worker_main

    return worker_main(control_fd)


if __name__ == "__main__":
    raise SystemExit(main())
