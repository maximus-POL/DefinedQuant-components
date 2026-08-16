"""Shared bounded-worker lifecycle and atomic operation publication."""

from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock, RLock, Thread
from typing import BinaryIO

from defined_quant.data_records import cas_json_bytes, strict_cas_json_loads
from defined_quant.host_failures import HostFailure as HostFailureEnvelope
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.local_host_platform import SecureFilesystem, local_host_platform
from defined_quant.operation_runtime import (
    OperationRuntimeError,
    operation_failure,
    prepare_output_directory,
    verify_operation_bundle,
)
from defined_quant.worker_limits import MAX_WORKER_CONTROL_BYTES, MAX_WORKER_REQUEST_BYTES
from defined_quant.worker_process import (
    WorkerProcessError,
    WorkerProcessHandle,
    WorkerProcessProvider,
)
from defined_quant_protocol import (
    OperationErrorCode,
    OperationRequest,
    OperationResult,
    OperationSuccess,
)
from pydantic import TypeAdapter, ValidationError

MAX_WORKER_WALL_SECONDS = 60.0
MAX_WORKER_GRACE_SECONDS = 5.0
MAX_WORKER_STREAM_BYTES = 1024 * 1024
MAX_WORKER_MEMORY_BYTES = 512 * 1024 * 1024
MAX_CONCURRENT_WORKERS = 2
_WORK_ROOT_PREFIX = ".defined-quant-worker-"
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_RESULT_ADAPTER: TypeAdapter[OperationResult] = TypeAdapter(OperationResult)


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    """Closed configurable limits bounded by the frozen alpha maxima."""

    wall_seconds: float = MAX_WORKER_WALL_SECONDS
    grace_seconds: float = MAX_WORKER_GRACE_SECONDS
    stream_bytes: int = MAX_WORKER_STREAM_BYTES
    memory_bytes: int = MAX_WORKER_MEMORY_BYTES
    concurrent_workers: int = MAX_CONCURRENT_WORKERS

    def __post_init__(self) -> None:
        if not 0 < self.wall_seconds <= MAX_WORKER_WALL_SECONDS:
            raise ValueError("worker wall limit is outside the frozen range")
        if not 0 <= self.grace_seconds <= MAX_WORKER_GRACE_SECONDS:
            raise ValueError("worker grace limit is outside the frozen range")
        if not 0 < self.stream_bytes <= MAX_WORKER_STREAM_BYTES:
            raise ValueError("worker stream limit is outside the frozen range")
        if not 0 < self.memory_bytes <= MAX_WORKER_MEMORY_BYTES:
            raise ValueError("worker memory limit is outside the frozen range")
        if not 0 < self.concurrent_workers <= MAX_CONCURRENT_WORKERS:
            raise ValueError("worker concurrency is outside the frozen range")


@dataclass(frozen=True, slots=True)
class WorkerExecution:
    """One validated operation result plus worker-verified component fields."""

    result: OperationResult
    component_input_fields: tuple[str, ...]


class _CleanupState(StrEnum):
    RUNNING = "running"
    STOP_OUTPUT = "stop_output"
    TERMINATE = "terminate"
    WAIT = "wait"
    KILL = "kill"
    CLOSE = "close"
    DONE = "done"


@dataclass(slots=True)
class _Capture:
    limit: int
    content: bytearray = field(default_factory=bytearray)
    overflow: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    failed: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)

    def read(self, stream: BinaryIO, accepting: Event, wake: Event) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                if accepting.is_set():
                    with self.lock:
                        remaining = self.limit + 1 - len(self.content)
                        if remaining > 0:
                            self.content.extend(chunk[:remaining])
                        if len(self.content) > self.limit:
                            self.overflow.set()
                            wake.set()
        except (OSError, ValueError):
            self.failed.set()
            wake.set()
        finally:
            self.done.set()
            wake.set()

    def bytes(self) -> bytes:
        with self.lock:
            return bytes(self.content)


@dataclass(slots=True)
class _ActiveWorker:
    handle: WorkerProcessHandle
    work_root: Path
    accepting_output: Event = field(default_factory=Event)
    wake: Event = field(default_factory=Event)
    native_lock: RLock = field(default_factory=RLock)
    state: _CleanupState = _CleanupState.RUNNING
    closed: bool = False


class WorkerController:
    """Own bounded native workers and expose one deterministic operation outcome."""

    def __init__(self, *, limits: WorkerLimits = WorkerLimits()) -> None:
        platform = local_host_platform()
        self._provider: WorkerProcessProvider = platform.worker_processes
        self._secure: SecureFilesystem = platform.secure_filesystem
        self._limits = limits
        self._required_environment = dict(self._provider.required_environment())
        self._capacity = BoundedSemaphore(limits.concurrent_workers)
        self._lock = RLock()
        self._active: dict[int, _ActiveWorker] = {}
        self._closed = False
        self._shutdown = Event()
        self._all_done = Event()
        self._all_done.set()

    def execute_operation(
        self,
        request: OperationRequest,
        *,
        output_dir: Path,
        catalog_root: Path | None = None,
        cancel_event: Event | None = None,
    ) -> WorkerExecution:
        """Execute once with no queue and publish only a complete verified bundle."""

        if not self._capacity.acquire(blocking=False):
            raise HostFailureException(HostFailureCode.WORKER_CAPACITY)
        active: _ActiveWorker | None = None
        work_root: Path | None = None
        try:
            with self._lock:
                if self._closed:
                    raise HostFailureException(HostFailureCode.WORKER_CANCELLED)
            try:
                validated = OperationRequest.model_validate(
                    request.model_dump(mode="python", warnings=False)
                )
                prepared = prepare_output_directory(
                    output_dir,
                    component=validated.component,
                )
                output_parent = self._existing_parent(prepared.parent)
                work_root = self._create_work_root(
                    self._provider.work_root_parents(output_parent)
                )
                temporary = work_root / "tmp"
                self._secure.create_private_directory(temporary)
                bundle = work_root / "bundle"
                normalized_catalog = (
                    None
                    if catalog_root is None
                    else Path(os.path.abspath(catalog_root))
                )
                request_bytes = cas_json_bytes(
                    {
                        "catalog_root": (
                            None if normalized_catalog is None else os.fspath(normalized_catalog)
                        ),
                        "output_dir": os.fspath(bundle),
                        "request": validated.model_dump(mode="json"),
                        "schema_version": 1,
                    }
                )
                self._enforce_request_limit(request_bytes)
                environment = self._environment(temporary)
                executable = Path(os.path.abspath(sys.executable))
                if not executable.is_file():
                    raise OSError
            except HostFailureException:
                raise
            except Exception as exc:
                failure = operation_failure(exc, request=request)
                return WorkerExecution(result=failure, component_input_fields=())

            try:
                with self._lock:
                    if self._closed:
                        raise HostFailureException(HostFailureCode.WORKER_CANCELLED)
                    handle = self._provider.launch(
                        executable=executable,
                        cwd=work_root,
                        environment=environment,
                        memory_limit_bytes=self._limits.memory_bytes,
                    )
                    active = _ActiveWorker(handle=handle, work_root=work_root)
                    active.accepting_output.set()
                    self._active[id(active)] = active
                    self._all_done.clear()
            except HostFailureException:
                raise
            except WorkerProcessError:
                raise HostFailureException(HostFailureCode.WORKER_CRASHED) from None

            stdout = _Capture(self._limits.stream_bytes)
            stderr = _Capture(self._limits.stream_bytes)
            control = _Capture(MAX_WORKER_CONTROL_BYTES)
            readers = (
                Thread(
                    target=stdout.read,
                    args=(self._provider.stdout(handle), active.accepting_output, active.wake),
                    daemon=True,
                ),
                Thread(
                    target=stderr.read,
                    args=(self._provider.stderr(handle), active.accepting_output, active.wake),
                    daemon=True,
                ),
                Thread(
                    target=control.read,
                    args=(self._provider.control(handle), active.accepting_output, active.wake),
                    daemon=True,
                ),
            )
            for reader in readers:
                reader.start()
            reason: HostFailureCode | None
            try:
                input_stream = self._provider.stdin(handle)
                view = memoryview(request_bytes)
                while view:
                    written = input_stream.write(view)
                    if written is None or written <= 0:
                        raise BrokenPipeError
                    view = view[written:]
                input_stream.flush()
                input_stream.close()
            except (BrokenPipeError, OSError, ValueError):
                reason = HostFailureCode.WORKER_CRASHED
            else:
                reason = self._monitor(
                    active,
                    stdout=stdout,
                    stderr=stderr,
                    control=control,
                    cancel_event=cancel_event,
                )

            self._cleanup_process(active, readers=readers, stop_output=reason is not None)
            self._enforce_control_limit(control)
            if reason is not None:
                raise HostFailureException(reason)
            if self._shutdown.is_set():
                raise HostFailureException(HostFailureCode.WORKER_CANCELLED)

            exit_code = self._provider.poll(handle)
            stdout_bytes = stdout.bytes()
            stderr_bytes = stderr.bytes()
            control_bytes = control.bytes()
            if stdout.overflow.is_set() or stderr.overflow.is_set():
                raise HostFailureException(HostFailureCode.WORKER_RESOURCE_LIMIT)
            if (
                exit_code != 0
                or stdout.failed.is_set()
                or stderr.failed.is_set()
                or control.failed.is_set()
            ):
                raise HostFailureException(HostFailureCode.WORKER_CRASHED)
            result, fields = self._control_response(control_bytes)
            if result is None:
                raise HostFailureException(HostFailureCode.WORKER_RESOURCE_LIMIT)
            if stdout_bytes or stderr_bytes:
                result = operation_failure(
                    OperationRuntimeError(
                        OperationErrorCode.COMPONENT_CONTRACT_ERROR,
                        "Component code emitted process output outside the typed protocol.",
                        component=validated.component,
                        details={
                            "stdout_emitted": bool(stdout_bytes),
                            "stderr_emitted": bool(stderr_bytes),
                        },
                    ),
                    request=validated,
                )
            if isinstance(result, OperationSuccess):
                try:
                    verify_operation_bundle(bundle, result.manifest)
                    with self._lock:
                        if self._closed or (
                            cancel_event is not None and cancel_event.is_set()
                        ):
                            raise HostFailureException(HostFailureCode.WORKER_CANCELLED)
                        # Publication is the linearization point: shutdown cannot begin
                        # between the final cancellation check and the no-replace rename.
                        prepared.parent.mkdir(parents=True, exist_ok=True)
                        self._secure.publish_directory_no_replace(bundle, prepared)
                except HostFailureException:
                    raise
                except Exception as exc:
                    result = operation_failure(
                        OperationRuntimeError(
                            OperationErrorCode.ARTIFACT_WRITE_FAILED,
                            "The worker bundle could not be published safely.",
                            component=validated.component,
                            details={"type": type(exc).__name__},
                        ),
                        request=validated,
                    )
            return WorkerExecution(result=result, component_input_fields=fields)
        except WorkerProcessError:
            raise HostFailureException(HostFailureCode.WORKER_CRASHED) from None
        finally:
            if active is not None:
                self._close_active(active)
            if work_root is not None and work_root.exists():
                try:
                    self._secure.remove_private_tree(work_root)
                except Exception:
                    pass
            self._capacity.release()

    def inspect_component(
        self,
        arguments: Mapping[str, object],
        *,
        catalog_root: Path | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        """Inspect one selected component inside the bounded native process tree."""

        return self._execute_inspection(
            "inspect_component",
            arguments,
            catalog_root=catalog_root,
            cancel_event=cancel_event,
        )

    def compare_ports(
        self,
        arguments: Mapping[str, object],
        *,
        catalog_root: Path | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        """Compare selected semantic ports inside the bounded native process tree."""

        return self._execute_inspection(
            "compare_ports",
            arguments,
            catalog_root=catalog_root,
            cancel_event=cancel_event,
        )

    def _execute_inspection(
        self,
        action: str,
        arguments: Mapping[str, object],
        *,
        catalog_root: Path | None,
        cancel_event: Event | None,
    ) -> dict[str, object]:
        if action not in {"inspect_component", "compare_ports"}:
            raise ValueError("worker inspection action is not supported")
        if not self._capacity.acquire(blocking=False):
            raise HostFailureException(HostFailureCode.WORKER_CAPACITY)
        active: _ActiveWorker | None = None
        work_root: Path | None = None
        try:
            with self._lock:
                if self._closed:
                    raise HostFailureException(HostFailureCode.WORKER_CANCELLED)
            try:
                parent = self._existing_parent(Path(tempfile.gettempdir()).resolve())
                work_root = self._create_work_root(
                    self._provider.work_root_parents(parent)
                )
                temporary = work_root / "tmp"
                self._secure.create_private_directory(temporary)
                normalized_catalog = (
                    None if catalog_root is None else Path(os.path.abspath(catalog_root))
                )
                request_bytes = cas_json_bytes(
                    {
                        "action": action,
                        "arguments": dict(arguments),
                        "catalog_root": (
                            None if normalized_catalog is None else os.fspath(normalized_catalog)
                        ),
                        "schema_version": 1,
                    }
                )
                self._enforce_request_limit(request_bytes)
                environment = self._environment(temporary)
                executable = Path(os.path.abspath(sys.executable))
                if not executable.is_file():
                    raise OSError
            except HostFailureException:
                raise
            except Exception:
                raise HostFailureException(HostFailureCode.WORKER_CRASHED) from None

            try:
                with self._lock:
                    if self._closed:
                        raise HostFailureException(HostFailureCode.WORKER_CANCELLED)
                    handle = self._provider.launch(
                        executable=executable,
                        cwd=work_root,
                        environment=environment,
                        memory_limit_bytes=self._limits.memory_bytes,
                    )
                    active = _ActiveWorker(handle=handle, work_root=work_root)
                    active.accepting_output.set()
                    self._active[id(active)] = active
                    self._all_done.clear()
            except HostFailureException:
                raise
            except WorkerProcessError:
                raise HostFailureException(HostFailureCode.WORKER_CRASHED) from None

            stdout = _Capture(self._limits.stream_bytes)
            stderr = _Capture(self._limits.stream_bytes)
            control = _Capture(MAX_WORKER_CONTROL_BYTES)
            readers = (
                Thread(
                    target=stdout.read,
                    args=(self._provider.stdout(handle), active.accepting_output, active.wake),
                    daemon=True,
                ),
                Thread(
                    target=stderr.read,
                    args=(self._provider.stderr(handle), active.accepting_output, active.wake),
                    daemon=True,
                ),
                Thread(
                    target=control.read,
                    args=(self._provider.control(handle), active.accepting_output, active.wake),
                    daemon=True,
                ),
            )
            for reader in readers:
                reader.start()
            try:
                input_stream = self._provider.stdin(handle)
                view = memoryview(request_bytes)
                while view:
                    written = input_stream.write(view)
                    if written is None or written <= 0:
                        raise BrokenPipeError
                    view = view[written:]
                input_stream.flush()
                input_stream.close()
            except (BrokenPipeError, OSError, ValueError):
                reason: HostFailureCode | None = HostFailureCode.WORKER_CRASHED
            else:
                reason = self._monitor(
                    active,
                    stdout=stdout,
                    stderr=stderr,
                    control=control,
                    cancel_event=cancel_event,
                )
            self._cleanup_process(active, readers=readers, stop_output=reason is not None)
            self._enforce_control_limit(control)
            if reason is not None:
                raise HostFailureException(reason)
            if self._shutdown.is_set():
                raise HostFailureException(HostFailureCode.WORKER_CANCELLED)
            if stdout.overflow.is_set() or stderr.overflow.is_set():
                raise HostFailureException(HostFailureCode.WORKER_RESOURCE_LIMIT)
            if (
                self._provider.poll(handle) != 0
                or stdout.failed.is_set()
                or stderr.failed.is_set()
                or control.failed.is_set()
            ):
                raise HostFailureException(HostFailureCode.WORKER_CRASHED)
            if stdout.bytes() or stderr.bytes():
                raise HostFailureException(HostFailureCode.COMPONENT_CONTRACT_ERROR)
            return self._inspection_response(control.bytes())
        except WorkerProcessError:
            raise HostFailureException(HostFailureCode.WORKER_CRASHED) from None
        finally:
            if active is not None:
                self._close_active(active)
            if work_root is not None and work_root.exists():
                try:
                    self._secure.remove_private_tree(work_root)
                except Exception:
                    pass
            self._capacity.release()

    def close(self) -> None:
        """Cancel all active trees and wait through the shared cleanup state machine."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._shutdown.set()
            active = tuple(self._active.values())
        for worker in active:
            worker.wake.set()
            if not worker.closed:
                try:
                    self._provider.terminate_tree(worker.handle)
                except WorkerProcessError:
                    pass
        if not self._all_done.wait(self._limits.grace_seconds):
            with self._lock:
                remaining = tuple(self._active.values())
            for worker in remaining:
                if not worker.closed:
                    try:
                        self._provider.kill_tree(worker.handle)
                    except WorkerProcessError:
                        pass
            self._all_done.wait(1.0)

    def _monitor(
        self,
        active: _ActiveWorker,
        *,
        stdout: _Capture,
        stderr: _Capture,
        control: _Capture,
        cancel_event: Event | None,
    ) -> HostFailureCode | None:
        deadline = time.monotonic() + self._limits.wall_seconds
        while True:
            if self._shutdown.is_set() or (
                cancel_event is not None and cancel_event.is_set()
            ):
                return HostFailureCode.WORKER_CANCELLED
            if stdout.overflow.is_set() or stderr.overflow.is_set():
                return HostFailureCode.WORKER_RESOURCE_LIMIT
            if control.overflow.is_set() or control.failed.is_set():
                return HostFailureCode.WORKER_CRASHED
            try:
                if self._provider.memory_usage(active.handle) > self._limits.memory_bytes:
                    return HostFailureCode.WORKER_RESOURCE_LIMIT
                exit_code = self._provider.poll(active.handle)
            except WorkerProcessError:
                return HostFailureCode.WORKER_CRASHED
            if exit_code is not None:
                return None
            if time.monotonic() >= deadline:
                return HostFailureCode.WORKER_TIMEOUT
            active.wake.wait(0.02)
            active.wake.clear()

    def _cleanup_process(
        self,
        active: _ActiveWorker,
        *,
        readers: tuple[Thread, Thread, Thread],
        stop_output: bool,
    ) -> None:
        if stop_output:
            active.state = _CleanupState.STOP_OUTPUT
            active.accepting_output.clear()
        with active.native_lock:
            if active.closed:
                return
            active.state = _CleanupState.TERMINATE
            try:
                self._provider.terminate_tree(active.handle)
            except WorkerProcessError:
                pass
            active.state = _CleanupState.WAIT
            try:
                self._provider.wait(
                    active.handle,
                    self._limits.grace_seconds,
                )
            except WorkerProcessError:
                pass
            active.state = _CleanupState.KILL
            try:
                # The primary may exit while a descendant remains in its group/job.
                # Force the complete native containment object down on every path.
                self._provider.kill_tree(active.handle)
                self._provider.wait(active.handle, 1.0)
            except WorkerProcessError:
                pass
        for reader in readers:
            reader.join(timeout=1.0)

    def _close_active(self, active: _ActiveWorker) -> None:
        with active.native_lock:
            if not active.closed:
                active.state = _CleanupState.CLOSE
                try:
                    self._provider.close(active.handle)
                except WorkerProcessError:
                    pass
                finally:
                    active.closed = True
                    active.state = _CleanupState.DONE
        with self._lock:
            self._active.pop(id(active), None)
            if not self._active:
                self._all_done.set()

    def _create_work_root(self, parents: tuple[Path, ...]) -> Path:
        for parent in parents:
            for _attempt in range(100):
                candidate = parent / f"{_WORK_ROOT_PREFIX}{secrets.token_hex(16)}"
                try:
                    self._secure.create_private_directory(candidate)
                    return candidate
                except Exception:
                    if candidate.exists():
                        continue
                    if len(parents) == 1:
                        raise
                    break
        raise OSError

    @staticmethod
    def _enforce_request_limit(content: bytes) -> None:
        actual = len(content)
        if actual > MAX_WORKER_REQUEST_BYTES:
            raise HostFailureException(
                HostFailureCode.INPUT_LIMIT_EXCEEDED,
                details={
                    "limit_name": "worker_request_bytes",
                    "maximum": MAX_WORKER_REQUEST_BYTES,
                    "actual": actual,
                },
            )

    @staticmethod
    def _enforce_control_limit(capture: _Capture) -> None:
        if capture.overflow.is_set():
            raise HostFailureException(
                HostFailureCode.RESULT_LIMIT_EXCEEDED,
                details={
                    "limit_name": "worker_control_response_bytes",
                    "maximum": MAX_WORKER_CONTROL_BYTES,
                    "actual": len(capture.bytes()),
                },
            )

    @staticmethod
    def _existing_parent(candidate: Path) -> Path:
        current = candidate
        while not current.exists():
            parent = current.parent
            if parent == current:
                raise OSError
            current = parent
        if not current.is_dir():
            raise OSError
        return current

    def _environment(self, temporary: Path) -> dict[str, str]:
        value = os.fspath(temporary)
        environment = {
            "NO_COLOR": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONWARNINGS": "error",
            "PYTHONUTF8": "1",
            "TEMP": value,
            "TMP": value,
            "TMPDIR": value,
            "TZ": "UTC",
        }
        environment.update(self._required_environment)
        return environment

    @staticmethod
    def _control_response(content: bytes) -> tuple[OperationResult | None, tuple[str, ...]]:
        try:
            value = strict_cas_json_loads(content, maximum_bytes=MAX_WORKER_CONTROL_BYTES)
            if not isinstance(value, Mapping) or value.get("schema_version") != 1:
                raise ValueError
            if value.get("kind") == "resource_limit" and set(value) == {
                "kind",
                "schema_version",
            }:
                return None, ()
            if set(value) != {"input_fields", "kind", "result", "schema_version"}:
                raise ValueError
            if value.get("kind") != "operation_result":
                raise ValueError
            raw_fields = value.get("input_fields")
            if not isinstance(raw_fields, list) or len(raw_fields) > 256:
                raise ValueError
            normalized_fields: list[str] = []
            for field in raw_fields:
                if not isinstance(field, str) or not _FIELD_NAME.fullmatch(field):
                    raise ValueError
                normalized_fields.append(field)
            fields = tuple(normalized_fields)
            if len(set(fields)) != len(fields):
                raise ValueError
            return _RESULT_ADAPTER.validate_python(value.get("result")), fields
        except (TypeError, ValueError, ValidationError):
            raise HostFailureException(HostFailureCode.WORKER_CRASHED) from None

    @staticmethod
    def _inspection_response(content: bytes) -> dict[str, object]:
        try:
            value = strict_cas_json_loads(content, maximum_bytes=MAX_WORKER_CONTROL_BYTES)
            if not isinstance(value, Mapping) or value.get("schema_version") != 1:
                raise ValueError
            if value.get("kind") == "resource_limit" and set(value) == {
                "kind",
                "schema_version",
            }:
                raise HostFailureException(HostFailureCode.WORKER_RESOURCE_LIMIT)
            if value.get("kind") == "host_failure" and set(value) == {
                "failure",
                "kind",
                "schema_version",
            }:
                failure = HostFailureEnvelope.model_validate(value.get("failure"))
                raise HostFailureException(
                    failure.error.code,
                    details=failure.error.details,
                    trust=failure.trust.label,
                )
            if value.get("kind") != "inspection_result" or set(value) != {
                "data",
                "kind",
                "schema_version",
            }:
                raise ValueError
            data = value.get("data")
            if not isinstance(data, Mapping):
                raise ValueError
            return dict(data)
        except HostFailureException:
            raise
        except (TypeError, ValueError, ValidationError):
            raise HostFailureException(HostFailureCode.WORKER_CRASHED) from None


__all__ = [
    "MAX_CONCURRENT_WORKERS",
    "MAX_WORKER_MEMORY_BYTES",
    "MAX_WORKER_STREAM_BYTES",
    "MAX_WORKER_WALL_SECONDS",
    "WorkerController",
    "WorkerExecution",
    "WorkerLimits",
]
