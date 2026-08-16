"""Suspended-process Windows Job Object provider for bounded workers."""

from __future__ import annotations

import ctypes
import math
import msvcrt
import os
import subprocess
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO, cast

from defined_quant.worker_process import (
    WorkerProcessError,
    WorkerProcessErrorCode,
    WorkerProcessHandle,
)

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_MEMORY_USAGE_INFORMATION_CLASS = 28
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_STILL_ACTIVE = 259
_INFINITE = 0xFFFFFFFF
_INVALID_RESUME_RESULT = 0xFFFFFFFF
_O_BINARY = getattr(os, "O_BINARY", 0)


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_MEMORY_USAGE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("JobMemory", ctypes.c_ulonglong),
        ("PeakJobMemory", ctypes.c_ulonglong),
    ]


def _failure(code: WorkerProcessErrorCode) -> WorkerProcessError:
    return WorkerProcessError(code)


class _WindowsWorkerApi:
    def __init__(self) -> None:
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        self.kernel32: Any = loader("kernel32", use_last_error=True)

        self.CreatePipe = self.kernel32.CreatePipe
        self.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            wintypes.DWORD,
        ]
        self.CreatePipe.restype = wintypes.BOOL

        self.SetHandleInformation = self.kernel32.SetHandleInformation
        self.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        self.SetHandleInformation.restype = wintypes.BOOL

        self.CloseHandle = self.kernel32.CloseHandle
        self.CloseHandle.argtypes = [wintypes.HANDLE]
        self.CloseHandle.restype = wintypes.BOOL

        self.CreateJobObjectW = self.kernel32.CreateJobObjectW
        self.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.CreateJobObjectW.restype = wintypes.HANDLE

        self.SetInformationJobObject = self.kernel32.SetInformationJobObject
        self.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.SetInformationJobObject.restype = wintypes.BOOL

        self.QueryInformationJobObject = self.kernel32.QueryInformationJobObject
        self.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.QueryInformationJobObject.restype = wintypes.BOOL

        self.InitializeProcThreadAttributeList = (
            self.kernel32.InitializeProcThreadAttributeList
        )
        self.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.InitializeProcThreadAttributeList.restype = wintypes.BOOL

        self.UpdateProcThreadAttribute = self.kernel32.UpdateProcThreadAttribute
        self.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.UpdateProcThreadAttribute.restype = wintypes.BOOL

        self.DeleteProcThreadAttributeList = self.kernel32.DeleteProcThreadAttributeList
        self.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        self.DeleteProcThreadAttributeList.restype = None

        self.CreateProcessW = self.kernel32.CreateProcessW
        self.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOW),
            ctypes.POINTER(_PROCESS_INFORMATION),
        ]
        self.CreateProcessW.restype = wintypes.BOOL

        self.AssignProcessToJobObject = self.kernel32.AssignProcessToJobObject
        self.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.AssignProcessToJobObject.restype = wintypes.BOOL

        self.ResumeThread = self.kernel32.ResumeThread
        self.ResumeThread.argtypes = [wintypes.HANDLE]
        self.ResumeThread.restype = wintypes.DWORD

        self.TerminateJobObject = self.kernel32.TerminateJobObject
        self.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.TerminateJobObject.restype = wintypes.BOOL

        self.WaitForSingleObject = self.kernel32.WaitForSingleObject
        self.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.WaitForSingleObject.restype = wintypes.DWORD

        self.GetExitCodeProcess = self.kernel32.GetExitCodeProcess
        self.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.GetExitCodeProcess.restype = wintypes.BOOL


@dataclass(slots=True, repr=False)
class _WindowsWorkerProcess(WorkerProcessHandle):
    process_handle: int
    job_handle: int
    stdin_writer: BinaryIO
    stdout_reader: BinaryIO
    stderr_reader: BinaryIO
    control_reader: BinaryIO
    memory_limit_bytes: int
    closed: bool = False
    lock: RLock = field(default_factory=RLock)

    def __repr__(self) -> str:
        return "<WorkerProcessHandle>"


class WindowsWorkerProcessProvider:
    """Create a suspended worker and assign it to a no-breakaway constrained job."""

    def __init__(self) -> None:
        try:
            self._api = _WindowsWorkerApi()
            self._entrypoint = Path(__file__).with_name("_windows_worker_entry.py")
            self._entrypoint_module = "defined_quant._windows_worker_entry"
            if not self._entrypoint.is_file():
                raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        except WorkerProcessError:
            raise
        except Exception:
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE) from None

    def required_environment(self) -> dict[str, str]:
        result: dict[str, str] = {}
        try:
            for name in ("SYSTEMROOT", "USERPROFILE"):
                value = os.environ.get(name)
                if (
                    value is None
                    or not value
                    or "\x00" in value
                    or value.startswith(("\\\\", "//"))
                    or not Path(value).is_absolute()
                ):
                    raise ValueError
                candidate = Path(value)
                if not candidate.is_dir():
                    raise ValueError
                result[name] = value
            return result
        except (OSError, ValueError):
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE) from None

    def launch(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        memory_limit_bytes: int,
    ) -> WorkerProcessHandle:
        raw_handles: set[int] = set()
        attribute_buffer: ctypes.Array[ctypes.c_char] | None = None
        attribute_values: ctypes.Array[Any] | None = None
        attribute_initialized = False
        process_information = _PROCESS_INFORMATION()
        converted: list[BinaryIO] = []
        job = 0
        try:
            application = self._extended_local_path(executable)
            working_directory = self._extended_local_path(cwd)
            bootstrap_directory = self._extended_local_path(
                Path(environment["SYSTEMROOT"])
            )
            stdin_read, stdin_write = self._pipe(raw_handles, parent="write")
            stdout_read, stdout_write = self._pipe(raw_handles, parent="read")
            stderr_read, stderr_write = self._pipe(raw_handles, parent="read")
            control_read, control_write = self._pipe(raw_handles, parent="read")
            child_handles = (stdin_read, stdout_write, stderr_write, control_write)

            job = self._job(memory_limit_bytes)
            raw_handles.add(job)
            attribute_buffer, attribute_values = self._attribute_list(child_handles)
            attribute_initialized = True
            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = wintypes.HANDLE(stdin_read)
            startup.StartupInfo.hStdOutput = wintypes.HANDLE(stdout_write)
            startup.StartupInfo.hStdError = wintypes.HANDLE(stderr_write)
            startup.lpAttributeList = ctypes.cast(attribute_buffer, ctypes.c_void_p)

            command = subprocess.list2cmdline(
                [
                    application,
                    "-m",
                    self._entrypoint_module,
                    "--control-handle",
                    str(control_write),
                    "--memory-limit",
                    str(memory_limit_bytes),
                    "--working-directory",
                    working_directory,
                ]
            )
            command_buffer = ctypes.create_unicode_buffer(command)
            environment_buffer = ctypes.create_unicode_buffer(
                "\x00".join(
                    f"{name}={value}"
                    for name, value in sorted(
                        environment.items(),
                        key=lambda item: item[0].casefold(),
                    )
                )
                + "\x00\x00"
            )
            flags = (
                _CREATE_SUSPENDED
                | _CREATE_NO_WINDOW
                | _CREATE_UNICODE_ENVIRONMENT
                | _EXTENDED_STARTUPINFO_PRESENT
            )
            if not self._api.CreateProcessW(
                application,
                command_buffer,
                None,
                None,
                True,
                flags,
                environment_buffer,
                bootstrap_directory,
                ctypes.byref(startup.StartupInfo),
                ctypes.byref(process_information),
            ):
                raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED)
            process_handle = int(process_information.hProcess or 0)
            thread_handle = int(process_information.hThread or 0)
            raw_handles.update({process_handle, thread_handle})
            if not self._api.AssignProcessToJobObject(
                wintypes.HANDLE(job),
                wintypes.HANDLE(process_handle),
            ):
                raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED)
            if self._api.ResumeThread(wintypes.HANDLE(thread_handle)) == _INVALID_RESUME_RESULT:
                raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED)
            self._close_handle(thread_handle)
            raw_handles.discard(thread_handle)
            for child in child_handles:
                self._close_handle(child)
                raw_handles.discard(child)

            stdin_stream = self._file(stdin_write, os.O_WRONLY | _O_BINARY, "wb")
            raw_handles.discard(stdin_write)
            converted.append(stdin_stream)
            stdout_stream = self._file(stdout_read, os.O_RDONLY | _O_BINARY, "rb")
            raw_handles.discard(stdout_read)
            converted.append(stdout_stream)
            stderr_stream = self._file(stderr_read, os.O_RDONLY | _O_BINARY, "rb")
            raw_handles.discard(stderr_read)
            converted.append(stderr_stream)
            control_stream = self._file(control_read, os.O_RDONLY | _O_BINARY, "rb")
            raw_handles.discard(control_read)
            converted.append(control_stream)
            raw_handles.discard(process_handle)
            raw_handles.discard(job)
            return _WindowsWorkerProcess(
                process_handle=process_handle,
                job_handle=job,
                stdin_writer=stdin_stream,
                stdout_reader=stdout_stream,
                stderr_reader=stderr_stream,
                control_reader=control_stream,
                memory_limit_bytes=memory_limit_bytes,
            )
        except WorkerProcessError:
            if job:
                self._api.TerminateJobObject(wintypes.HANDLE(job), 1)
            for stream in converted:
                try:
                    stream.close()
                except OSError:
                    pass
            for handle in tuple(raw_handles):
                self._close_handle(handle)
            raise
        except Exception:
            if job:
                self._api.TerminateJobObject(wintypes.HANDLE(job), 1)
            for stream in converted:
                try:
                    stream.close()
                except OSError:
                    pass
            for handle in tuple(raw_handles):
                self._close_handle(handle)
            raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED) from None
        finally:
            if attribute_initialized and attribute_buffer is not None:
                self._api.DeleteProcThreadAttributeList(attribute_buffer)
            del attribute_values

    def stdin(self, handle: WorkerProcessHandle) -> BinaryIO:
        return self._process(handle).stdin_writer

    def stdout(self, handle: WorkerProcessHandle) -> BinaryIO:
        return self._process(handle).stdout_reader

    def stderr(self, handle: WorkerProcessHandle) -> BinaryIO:
        return self._process(handle).stderr_reader

    def control(self, handle: WorkerProcessHandle) -> BinaryIO:
        return self._process(handle).control_reader

    def poll(self, handle: WorkerProcessHandle) -> int | None:
        worker = self._process(handle)
        result = self._api.WaitForSingleObject(wintypes.HANDLE(worker.process_handle), 0)
        if result == _WAIT_TIMEOUT:
            return None
        if result != _WAIT_OBJECT_0:
            raise _failure(WorkerProcessErrorCode.MONITOR_FAILED)
        exit_code = wintypes.DWORD()
        if not self._api.GetExitCodeProcess(
            wintypes.HANDLE(worker.process_handle),
            ctypes.byref(exit_code),
        ):
            raise _failure(WorkerProcessErrorCode.MONITOR_FAILED)
        return None if exit_code.value == _STILL_ACTIVE else int(exit_code.value)

    def wait(self, handle: WorkerProcessHandle, timeout_seconds: float) -> bool:
        worker = self._process(handle)
        milliseconds = min(
            _INFINITE - 1,
            max(0, math.ceil(max(0.0, timeout_seconds) * 1000)),
        )
        result = self._api.WaitForSingleObject(
            wintypes.HANDLE(worker.process_handle),
            milliseconds,
        )
        if result == _WAIT_OBJECT_0:
            return True
        if result == _WAIT_TIMEOUT:
            return False
        raise _failure(WorkerProcessErrorCode.MONITOR_FAILED)

    def memory_usage(self, handle: WorkerProcessHandle) -> int:
        worker = self._process(handle)
        usage = _JOBOBJECT_MEMORY_USAGE_INFORMATION()
        if not self._api.QueryInformationJobObject(
            wintypes.HANDLE(worker.job_handle),
            _JOB_OBJECT_MEMORY_USAGE_INFORMATION_CLASS,
            ctypes.byref(usage),
            ctypes.sizeof(usage),
            None,
        ):
            raise _failure(WorkerProcessErrorCode.MONITOR_FAILED)
        return int(usage.JobMemory)

    def terminate_tree(self, handle: WorkerProcessHandle) -> None:
        worker = self._process(handle)
        self._api.TerminateJobObject(wintypes.HANDLE(worker.job_handle), 1)

    def kill_tree(self, handle: WorkerProcessHandle) -> None:
        self.terminate_tree(handle)

    def close(self, handle: WorkerProcessHandle) -> None:
        worker = self._process(handle)
        with worker.lock:
            if worker.closed:
                return
            worker.closed = True
            self._api.TerminateJobObject(wintypes.HANDLE(worker.job_handle), 1)
            for stream in (
                worker.stdin_writer,
                worker.stdout_reader,
                worker.stderr_reader,
                worker.control_reader,
            ):
                try:
                    stream.close()
                except OSError:
                    pass
            self._close_handle(worker.process_handle)
            self._close_handle(worker.job_handle)

    def _pipe(self, handles: set[int], *, parent: str) -> tuple[int, int]:
        security = _SECURITY_ATTRIBUTES(
            nLength=ctypes.sizeof(_SECURITY_ATTRIBUTES),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        if not self._api.CreatePipe(
            ctypes.byref(read_handle),
            ctypes.byref(write_handle),
            ctypes.byref(security),
            0,
        ):
            raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED)
        read_value = int(read_handle.value or 0)
        write_value = int(write_handle.value or 0)
        handles.update({read_value, write_value})
        parent_handle = write_value if parent == "write" else read_value
        if not self._api.SetHandleInformation(
            wintypes.HANDLE(parent_handle),
            _HANDLE_FLAG_INHERIT,
            0,
        ):
            raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED)
        return read_value, write_value

    def _job(self, memory_limit_bytes: int) -> int:
        raw_job = self._api.CreateJobObjectW(None, None)
        job = int(raw_job or 0)
        if not job:
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        limits.JobMemoryLimit = memory_limit_bytes
        if not self._api.SetInformationJobObject(
            wintypes.HANDLE(job),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            self._close_handle(job)
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        return job

    def _attribute_list(
        self,
        child_handles: tuple[int, ...],
    ) -> tuple[ctypes.Array[ctypes.c_char], ctypes.Array[Any]]:
        required = ctypes.c_size_t()
        self._api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(required))
        if required.value == 0:
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        buffer = ctypes.create_string_buffer(required.value)
        if not self._api.InitializeProcThreadAttributeList(
            buffer,
            1,
            0,
            ctypes.byref(required),
        ):
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        values = (wintypes.HANDLE * len(child_handles))(*child_handles)
        if not self._api.UpdateProcThreadAttribute(
            buffer,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            values,
            ctypes.sizeof(values),
            None,
            None,
        ):
            self._api.DeleteProcThreadAttributeList(buffer)
            raise _failure(WorkerProcessErrorCode.CAPABILITY_UNAVAILABLE)
        return buffer, values

    @staticmethod
    def _file(handle: int, flags: int, mode: str) -> BinaryIO:
        descriptor = msvcrt.open_osfhandle(handle, flags)  # type: ignore[attr-defined]
        return cast(BinaryIO, os.fdopen(descriptor, mode, buffering=0, closefd=True))

    def _close_handle(self, handle: int) -> None:
        if handle:
            self._api.CloseHandle(wintypes.HANDLE(handle))

    @staticmethod
    def _extended_local_path(path: Path) -> str:
        value = os.path.abspath(os.fspath(path))
        if value.startswith("\\\\?\\"):
            suffix = value[4:]
            if len(suffix) < 3 or suffix[1:3] != ":\\" or not suffix[0].isalpha():
                raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED)
            return value
        if value.startswith(("\\\\", "//")):
            raise _failure(WorkerProcessErrorCode.LAUNCH_FAILED)
        return "\\\\?\\" + value

    @staticmethod
    def _process(handle: WorkerProcessHandle) -> _WindowsWorkerProcess:
        if not isinstance(handle, _WindowsWorkerProcess):
            raise _failure(WorkerProcessErrorCode.MONITOR_FAILED)
        return handle


__all__ = ["WindowsWorkerProcessProvider"]
