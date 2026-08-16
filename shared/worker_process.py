"""Transport-neutral native worker-process contracts."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol


class WorkerProcessErrorCode(StrEnum):
    """Closed internal launcher failures that never contain native error text."""

    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    LAUNCH_FAILED = "launch_failed"
    MONITOR_FAILED = "monitor_failed"


class WorkerProcessError(Exception):
    """Stable provider exception without errno, WinError, path, or environment text."""

    def __init__(self, code: WorkerProcessErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class WorkerProcessHandle:
    """Opaque lifetime handle for one native worker process tree."""

    __slots__ = ()


class WorkerProcessProvider(Protocol):
    """Small native-process surface consumed by the shared worker state machine."""

    def required_environment(self) -> Mapping[str, str]: ...

    def work_root_parents(self, output_parent: Path) -> tuple[Path, ...]: ...

    def launch(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        memory_limit_bytes: int,
    ) -> WorkerProcessHandle: ...

    def stdin(self, handle: WorkerProcessHandle) -> BinaryIO: ...

    def stdout(self, handle: WorkerProcessHandle) -> BinaryIO: ...

    def stderr(self, handle: WorkerProcessHandle) -> BinaryIO: ...

    def control(self, handle: WorkerProcessHandle) -> BinaryIO: ...

    def poll(self, handle: WorkerProcessHandle) -> int | None: ...

    def wait(self, handle: WorkerProcessHandle, timeout_seconds: float) -> bool: ...

    def memory_usage(self, handle: WorkerProcessHandle) -> int: ...

    def terminate_tree(self, handle: WorkerProcessHandle) -> None: ...

    def kill_tree(self, handle: WorkerProcessHandle) -> None: ...

    def close(self, handle: WorkerProcessHandle) -> None: ...


__all__ = [
    "WorkerProcessError",
    "WorkerProcessErrorCode",
    "WorkerProcessHandle",
    "WorkerProcessProvider",
]
