"""Import-safe Windows bootstrap for one inherited worker control handle."""

from __future__ import annotations

import msvcrt
import os
import sys

_O_BINARY = getattr(os, "O_BINARY", 0)

_ALLOWED_ENVIRONMENT = {
    "NO_COLOR",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONNOUSERSITE",
    "PYTHONUTF8",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "USERPROFILE",
}


def _arguments() -> tuple[int, int, str]:
    if (
        len(sys.argv) != 7
        or sys.argv[1] != "--control-handle"
        or sys.argv[3] != "--memory-limit"
        or sys.argv[5] != "--working-directory"
    ):
        raise SystemExit(70)
    try:
        control_handle = int(sys.argv[2])
        memory_limit = int(sys.argv[4])
    except ValueError:
        raise SystemExit(70) from None
    working_directory = sys.argv[6]
    if (
        control_handle <= 0
        or memory_limit <= 0
        or not working_directory
        or "\x00" in working_directory
        or not os.path.isabs(working_directory)
    ):
        raise SystemExit(70)
    return control_handle, memory_limit, working_directory


def main() -> int:
    control_handle, _memory_limit, working_directory = _arguments()
    for name in tuple(os.environ):
        if name.upper() not in _ALLOWED_ENVIRONMENT:
            del os.environ[name]
    for descriptor in (0, 1, 2):
        msvcrt.setmode(descriptor, _O_BINARY)  # type: ignore[attr-defined]
    try:
        os.chdir(working_directory)
    except OSError:
        return 70
    control_fd = msvcrt.open_osfhandle(  # type: ignore[attr-defined]
        control_handle,
        os.O_WRONLY | _O_BINARY,
    )
    from defined_quant.worker_entry import worker_main

    return worker_main(control_fd)


if __name__ == "__main__":
    raise SystemExit(main())
