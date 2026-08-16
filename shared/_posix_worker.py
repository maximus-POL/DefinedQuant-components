"""POSIX process-group provider for bounded Defined Quant workers."""

from __future__ import annotations

import ctypes
import os
import resource
import signal
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import BinaryIO, Literal, cast

from defined_quant.worker_process import (
    WorkerProcessError,
    WorkerProcessErrorCode,
    WorkerProcessHandle,
)


def _failure(code: WorkerProcessErrorCode) -> WorkerProcessError:
    return WorkerProcessError(code)


@dataclass(slots=True, repr=False)
class _PosixWorkerProcess(WorkerProcessHandle):
    process: subprocess.Popen[bytes]
    control_reader: BinaryIO
    known_pids: set[int] = field(default_factory=set)
    closed: bool = False
    lock: RLock = field(default_factory=RLock)

    def __repr__(self) -> str:
        return "<WorkerProcessHandle>"


class _PROC_TASKINFO(ctypes.Structure):
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("context_switches", ctypes.c_int32),
        ("thread_count", ctypes.c_int32),
        ("running_threads", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    ]


class PosixWorkerProcessProvider:
    """Launch workers in new sessions and own their complete process groups."""

    def __init__(self, *, platform_id: Literal["darwin", "linux"]) -> None:
        if not hasattr(resource, "RLIMIT_AS"):
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        self._platform_id = platform_id
        self._entrypoint = Path(__file__).with_name("_posix_worker_entry.py")
        self._entrypoint_module = "defined_quant._posix_worker_entry"
        if not self._entrypoint.is_file():
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)

    def required_environment(self) -> dict[str, str]:
        return {}

    @staticmethod
    def inspection_work_root_parents() -> tuple[Path, ...]:
        """Select the canonical system temporary root for managed inspection state."""

        return (Path(os.path.realpath(tempfile.gettempdir())),)

    @staticmethod
    def work_root_parents(output_parent: Path) -> tuple[Path, ...]:
        return (output_parent,)

    def launch(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        memory_limit_bytes: int,
    ) -> WorkerProcessHandle:
        control_read = -1
        control_write = -1
        process: subprocess.Popen[bytes] | None = None
        try:
            control_read, control_write = os.pipe()
            os.set_inheritable(control_read, False)
            os.set_inheritable(control_write, True)
            process = subprocess.Popen(
                [
                    os.fspath(executable),
                    "-m",
                    self._entrypoint_module,
                    "--control-fd",
                    str(control_write),
                    "--memory-limit",
                    str(memory_limit_bytes),
                    "--address-space-limit",
                    "1" if self._platform_id == "linux" else "0",
                ],
                executable=os.fspath(executable),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                pass_fds=(control_write,),
                start_new_session=True,
                shell=False,
            )
            os.close(control_write)
            control_write = -1
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            reader = os.fdopen(control_read, "rb", buffering=0)
            control_read = -1
            return _PosixWorkerProcess(
                process=process,
                control_reader=reader,
                known_pids={process.pid},
            )
        except Exception:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except Exception:
                    pass
            for descriptor in (control_read, control_write):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED) from None

    def stdin(self, handle: WorkerProcessHandle) -> BinaryIO:
        process = self._process(handle).process
        assert process.stdin is not None
        return cast(BinaryIO, process.stdin)

    def stdout(self, handle: WorkerProcessHandle) -> BinaryIO:
        process = self._process(handle).process
        assert process.stdout is not None
        return cast(BinaryIO, process.stdout)

    def stderr(self, handle: WorkerProcessHandle) -> BinaryIO:
        process = self._process(handle).process
        assert process.stderr is not None
        return cast(BinaryIO, process.stderr)

    def control(self, handle: WorkerProcessHandle) -> BinaryIO:
        return self._process(handle).control_reader

    def poll(self, handle: WorkerProcessHandle) -> int | None:
        return self._process(handle).process.poll()

    def wait(self, handle: WorkerProcessHandle, timeout_seconds: float) -> bool:
        process = self._process(handle).process
        try:
            process.wait(timeout=max(0.0, timeout_seconds))
            return True
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return process.poll() is not None

    def memory_usage(self, handle: WorkerProcessHandle) -> int:
        worker = self._process(handle)
        try:
            records = (
                self._linux_process_records()
                if self._platform_id == "linux"
                else self._darwin_process_records()
            )
            descendants = self._descendants(worker.process.pid, records, worker.known_pids)
            worker.known_pids.update(descendants)
            return sum(records[pid][2] for pid in descendants if pid in records)
        except Exception:
            raise _failure(WorkerProcessErrorCode.MONITOR_FAILED) from None

    def terminate_tree(self, handle: WorkerProcessHandle) -> None:
        self._signal_tree(self._process(handle), signal.SIGTERM)

    def kill_tree(self, handle: WorkerProcessHandle) -> None:
        self._signal_tree(self._process(handle), signal.SIGKILL)

    def close(self, handle: WorkerProcessHandle) -> None:
        worker = self._process(handle)
        with worker.lock:
            if worker.closed:
                return
            worker.closed = True
            streams = (
                worker.process.stdin,
                worker.process.stdout,
                worker.process.stderr,
                worker.control_reader,
            )
            for stream in streams:
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def _signal_tree(self, worker: _PosixWorkerProcess, signal_number: int) -> None:
        try:
            self.memory_usage(worker)
        except WorkerProcessError:
            pass
        try:
            os.killpg(worker.process.pid, signal_number)
        except OSError:
            pass
        for pid in tuple(worker.known_pids):
            if pid == os.getpid():
                continue
            try:
                os.kill(pid, signal_number)
            except OSError:
                pass

    @staticmethod
    def _descendants(
        root_pid: int,
        records: dict[int, tuple[int, int, int]],
        known_pids: set[int],
    ) -> set[int]:
        selected = {root_pid, *known_pids}
        changed = True
        while changed:
            changed = False
            for pid, (parent_pid, process_group, _rss) in records.items():
                if pid in selected:
                    continue
                if parent_pid in selected or process_group == root_pid:
                    selected.add(pid)
                    changed = True
        return selected

    @staticmethod
    def _linux_process_records() -> dict[int, tuple[int, int, int]]:
        records: dict[int, tuple[int, int, int]] = {}
        page_size = os.sysconf("SC_PAGE_SIZE")
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isascii() or not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                try:
                    content = Path(entry.path, "stat").read_text(encoding="ascii")
                    closing = content.rfind(")")
                    fields = content[closing + 2 :].split()
                    parent_pid = int(fields[1])
                    process_group = int(fields[2])
                    resident_bytes = int(fields[21]) * page_size
                    records[pid] = (parent_pid, process_group, resident_bytes)
                except (OSError, UnicodeError, ValueError):
                    continue
        return records

    @staticmethod
    def _darwin_process_records() -> dict[int, tuple[int, int, int]]:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        list_pids = library.proc_listallpids
        list_pids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        list_pids.restype = ctypes.c_int
        pid_info = library.proc_pidinfo
        pid_info.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        pid_info.restype = ctypes.c_int
        capacity = list_pids(None, 0)
        if capacity <= 0:
            raise OSError
        values = (ctypes.c_int * (capacity + 32))()
        count = list_pids(values, ctypes.sizeof(values))
        if count <= 0:
            raise OSError
        records: dict[int, tuple[int, int, int]] = {}
        for pid in values[:count]:
            if pid <= 0:
                continue
            task = _PROC_TASKINFO()
            size = pid_info(
                pid,
                4,
                0,
                ctypes.byref(task),
                ctypes.sizeof(task),
            )
            if size != ctypes.sizeof(task):
                continue
            try:
                process_group = os.getpgid(pid)
            except OSError:
                continue
            records[pid] = (0, process_group, int(task.resident_size))
        return records

    @staticmethod
    def _process(handle: WorkerProcessHandle) -> _PosixWorkerProcess:
        if not isinstance(handle, _PosixWorkerProcess):
            raise _failure(WorkerProcessErrorCode.MONITOR_FAILED)
        return handle


__all__ = ["PosixWorkerProcessProvider"]
