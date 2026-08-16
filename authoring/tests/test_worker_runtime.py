"""Native Phase-4 subprocess isolation and shared lifecycle tests."""

from __future__ import annotations

import ctypes
import importlib
import json
import os
import sys
import time
from pathlib import Path
from textwrap import dedent
from threading import Event, Thread
from typing import Any

import defined_quant
import pytest
from defined_quant import invalidate_subject_cache, subject_hash
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.service import DefinedQuantService
from defined_quant.worker_runtime import WorkerController, WorkerLimits
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    OperationRequest,
    OperationSuccess,
)

COMPONENT_ID = "dq.worker_fixture.phase4"
COMPONENT_VERSION = "1.0.0"
COMPONENT_MODULE = "defined_quant.worker_fixture.phase4.component"
COMMON_ENVIRONMENT = {
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

_COMPONENT_SOURCE = dedent(
    '''\
    """Synthetic component used only to probe the native worker boundary."""

    import ctypes
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    from typing import Literal

    from defined_quant import subject_hash
    from defined_quant.types import DiagnosticOutput, Unit
    from pydantic import BaseModel, ConfigDict

    COMPONENT_ID = "dq.worker_fixture.phase4"
    COMPONENT_VERSION = "1.0.0"
    CATALOG_ROOT = Path(__file__).resolve().parents[2]
    Mode = Literal[
        "normal", "sleep", "spawn_tree", "memory", "crash",
        "stdout_overflow", "stderr_overflow", "python_output_overflow",
        "environment", "handle_probe", "breakaway_probe",
    ]


    class Inputs(BaseModel):
        model_config = ConfigDict(frozen=True, extra="forbid")

        mode: Mode
        marker: str = ""
        handle_value: int = -1


    class Output(DiagnosticOutput):
        unit: Literal[Unit.UNITLESS]
        mode: Mode
        environment_keys: tuple[str, ...] = ()
        secret_present: bool = False
        inherited_handle: bool | None = None
        breakaway_succeeded: bool | None = None


    def _mark(path: str, value: str) -> None:
        if path:
            Path(path).write_text(value, encoding="ascii")


    def _spawn_tree(marker: str) -> None:
        child_source = (
            "import os,subprocess,sys,time;from pathlib import Path;"
            "grand=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
            "close_fds=True);"
            "Path(sys.argv[1]).write_text(f'{os.getpid()} {grand.pid}',encoding='ascii');"
            "time.sleep(30)"
        )
        subprocess.Popen(
            [sys.executable, "-c", child_source, marker],
            close_fds=True,
            shell=False,
        )
        deadline = time.monotonic() + 10
        while not Path(marker).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(30)


    def _handle_is_valid(value: int) -> bool:
        if os.name == "nt":
            flags = ctypes.c_ulong()
            return bool(
                ctypes.windll.kernel32.GetHandleInformation(
                    ctypes.c_void_p(value), ctypes.byref(flags)
                )
            )
        try:
            os.fstat(value)
        except OSError:
            return False
        return True


    def phase4(mode: Mode, marker: str = "", handle_value: int = -1) -> Output:
        inputs = Inputs.model_validate(
            {"mode": mode, "marker": marker, "handle_value": handle_value}
        )
        if inputs.mode == "sleep":
            _mark(inputs.marker, str(os.getpid()))
            time.sleep(30)
        elif inputs.mode == "spawn_tree":
            _spawn_tree(inputs.marker)
        elif inputs.mode == "memory":
            blocks = []
            while True:
                blocks.append(bytearray(8 * 1024 * 1024))
                time.sleep(0.005)
        elif inputs.mode == "crash":
            os._exit(23)
        elif inputs.mode == "stdout_overflow":
            os.write(1, b"x" * (2 * 1024 * 1024))
        elif inputs.mode == "stderr_overflow":
            os.write(2, b"x" * (2 * 1024 * 1024))
        elif inputs.mode == "python_output_overflow":
            print("x" * (2 * 1024 * 1024))

        environment_keys = tuple(sorted(os.environ)) if mode == "environment" else ()
        inherited = _handle_is_valid(handle_value) if mode == "handle_probe" else None
        breakaway = None
        if mode == "breakaway_probe":
            try:
                child = subprocess.Popen(
                    [sys.executable, "-c", "pass"],
                    close_fds=True,
                    creationflags=0x01000000,
                    shell=False,
                )
            except OSError:
                breakaway = False
            else:
                breakaway = True
                child.wait(timeout=5)
        return Output(
            component_id=COMPONENT_ID,
            version=COMPONENT_VERSION,
            subject_hash=subject_hash(COMPONENT_ID, root=CATALOG_ROOT),
            unit=Unit.UNITLESS,
            assumptions=("The fixture has no financial interpretation.",),
            disclosures=(),
            warnings=(),
            transformations=(),
            visualizations=(),
            passed=True,
            findings=(),
            coverage={"worker_boundary": 1.0},
            mode=mode,
            environment_keys=environment_keys,
            secret_present="DEFINED_QUANT_PARENT_SECRET" in os.environ,
            inherited_handle=inherited,
            breakaway_succeeded=breakaway,
        )
    '''
)


def _install_component(catalog_root: Path) -> ComponentRef:
    component_root = catalog_root / "categories" / "worker_fixture" / "phase4"
    component_root.mkdir(parents=True)
    (component_root / "component.py").write_text(_COMPONENT_SOURCE, encoding="utf-8")
    contract = {
        "schema_version": 1,
        "id": COMPONENT_ID,
        "slug": "phase4",
        "title": "Phase 4 Worker Fixture",
        "category": "worker_fixture",
        "group": "integration",
        "version": COMPONENT_VERSION,
        "lifecycle": "draft",
        "template": {"profile": "diagnostic", "version": 1},
        "callable": f"{COMPONENT_MODULE}:phase4",
        "summary": "Exercises native worker containment without financial behavior.",
        "discovery": {
            "aliases": ["phase 4 worker fixture"],
            "intents": ["test_worker_containment"],
            "input_concepts": ["worker_probe"],
            "output_concepts": ["worker_observation"],
        },
        "tags": ["integration", "synthetic"],
        "assumptions": ["Inputs are deterministic synthetic probes."],
        "limitations": ["This component exists only inside this test."],
        "depends_on": [],
        "supported_python": [">=3.11", "<3.14"],
        "guidance": {
            "use_when": ["The worker boundary is under native test."],
            "do_not_use_when": ["A financial result is requested."],
            "unsupported_scope": ["Financial interpretation."],
            "required_questions": [],
            "advisory_questions": [],
            "allowed_defaults": [],
            "constraints": [],
            "interpretation": "Treat the output only as a worker-boundary probe.",
        },
        "display": {
            "formula": "identity(mode)",
            "intent": "Probe one worker isolation behavior.",
            "output": "A deterministic worker observation.",
        },
    }
    (component_root / "contract.yaml").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    invalidate_subject_cache()
    reference = ComponentRef(
        id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        subject_hash=subject_hash(COMPONENT_ID, root=catalog_root),
    )
    for name in tuple(sys.modules):
        if name.startswith("defined_quant.worker_fixture"):
            sys.modules.pop(name)
    return reference


@pytest.fixture
def worker_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, ComponentRef]:
    catalog_root = tmp_path / "catalog"
    package_root = catalog_root / "categories"
    monkeypatch.setattr(defined_quant, "__path__", [*defined_quant.__path__, str(package_root)])
    return catalog_root, _install_component(catalog_root)


def _request(reference: ComponentRef, mode: str, **values: Any) -> OperationRequest:
    return OperationRequest(
        component=reference,
        input={"mode": mode, **values},
        provenance=CallerProvenance(
            source_kind="synthetic",
            interpretation_method="caller_structured",
            label="Native Phase-4 worker fixture.",
        ),
    )


def _run(
    controller: WorkerController,
    catalog_root: Path,
    reference: ComponentRef,
    tmp_path: Path,
    mode: str,
    **values: Any,
) -> tuple[Path, dict[str, Any]]:
    output = tmp_path / f"operation-{mode}"
    execution = controller.execute_operation(
        _request(reference, mode, **values),
        output_dir=output,
        catalog_root=catalog_root,
    )
    assert isinstance(execution.result, OperationSuccess)
    return output, json.loads((output / "result.json").read_text(encoding="utf-8"))


def _failure_code(
    controller: WorkerController,
    catalog_root: Path,
    reference: ComponentRef,
    output: Path,
    mode: str,
    **values: Any,
) -> HostFailureCode:
    with pytest.raises(HostFailureException) as caught:
        controller.execute_operation(
            _request(reference, mode, **values),
            output_dir=output,
            catalog_root=catalog_root,
        )
    assert not output.exists()
    return caught.value.code


def _process_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _assert_processes_stopped(pids: list[int]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_process_is_running(pid) for pid in pids):
        time.sleep(0.02)
    assert not [pid for pid in pids if _process_is_running(pid)]


def test_worker_uses_scratch_environment_and_does_not_import_component_in_controller(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_root, reference = worker_component
    monkeypatch.setenv("DEFINED_QUANT_PARENT_SECRET", "must-not-cross-boundary")
    controller = WorkerController(limits=WorkerLimits(wall_seconds=5))
    _output, result = _run(
        controller, catalog_root, reference, tmp_path, "environment"
    )
    expected = COMMON_ENVIRONMENT | (
        {"SYSTEMROOT", "WINDIR", "COMSPEC"} if sys.platform == "win32" else set()
    )
    assert set(result["environment_keys"]) == expected
    assert result["secret_present"] is False
    assert COMPONENT_MODULE not in sys.modules
    controller.close()


@pytest.mark.parametrize("mode", ["stdout_overflow", "stderr_overflow", "python_output_overflow"])
def test_stdout_and_stderr_are_capped_separately(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
    mode: str,
) -> None:
    catalog_root, reference = worker_component
    controller = WorkerController(limits=WorkerLimits(wall_seconds=5, stream_bytes=64 * 1024))
    assert _failure_code(
        controller,
        catalog_root,
        reference,
        tmp_path / f"operation-{mode}",
        mode,
    ) is HostFailureCode.WORKER_RESOURCE_LIMIT
    controller.close()


def test_timeout_kills_worker_child_and_grandchild(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    marker = tmp_path / "tree-pids"
    controller = WorkerController(
        limits=WorkerLimits(wall_seconds=1.5, grace_seconds=0.1)
    )
    assert _failure_code(
        controller,
        catalog_root,
        reference,
        tmp_path / "operation-tree",
        "spawn_tree",
        marker=os.fspath(marker),
    ) is HostFailureCode.WORKER_TIMEOUT
    pids = [int(value) for value in marker.read_text(encoding="ascii").split()]
    assert len(pids) == 2
    _assert_processes_stopped(pids)
    controller.close()


def test_explicit_cancellation_kills_the_complete_tree(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    marker = tmp_path / "cancel-tree-pids"
    cancellation = Event()
    controller = WorkerController(limits=WorkerLimits(wall_seconds=10, grace_seconds=0.1))
    observed: list[HostFailureCode] = []

    def execute() -> None:
        try:
            controller.execute_operation(
                _request(reference, "spawn_tree", marker=os.fspath(marker)),
                output_dir=tmp_path / "operation-cancel",
                catalog_root=catalog_root,
                cancel_event=cancellation,
            )
        except HostFailureException as exc:
            observed.append(exc.code)

    thread = Thread(target=execute)
    thread.start()
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    cancellation.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert observed == [HostFailureCode.WORKER_CANCELLED]
    pids = [int(value) for value in marker.read_text(encoding="ascii").split()]
    _assert_processes_stopped(pids)
    assert not (tmp_path / "operation-cancel").exists()
    controller.close()


def test_memory_and_crash_have_closed_stable_outcomes(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    memory_controller = WorkerController(
        limits=WorkerLimits(wall_seconds=10, memory_bytes=256 * 1024 * 1024)
    )
    assert _failure_code(
        memory_controller,
        catalog_root,
        reference,
        tmp_path / "operation-memory",
        "memory",
    ) is HostFailureCode.WORKER_RESOURCE_LIMIT
    memory_controller.close()

    crash_controller = WorkerController(limits=WorkerLimits(wall_seconds=5))
    assert _failure_code(
        crash_controller,
        catalog_root,
        reference,
        tmp_path / "operation-crash",
        "crash",
    ) is HostFailureCode.WORKER_CRASHED
    crash_controller.close()


def test_unrelated_inheritable_descriptor_or_handle_is_not_inherited(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(read_fd, True)
        value = read_fd
        if sys.platform == "win32":
            import msvcrt

            value = msvcrt.get_osfhandle(read_fd)
        controller = WorkerController(limits=WorkerLimits(wall_seconds=5))
        _output, result = _run(
            controller,
            catalog_root,
            reference,
            tmp_path,
            "handle_probe",
            handle_value=value,
        )
        assert result["inherited_handle"] is False
        controller.close()
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_windows_worker_supports_unicode_and_long_local_paths(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    parent = tmp_path / "zażółć-gęślą-jaźń"
    while len(os.fspath(parent)) < 280:
        parent /= "long-local-worker-segment"
    parent.mkdir(parents=True)
    output = parent / "operation"
    controller = WorkerController(limits=WorkerLimits(wall_seconds=10))
    execution = controller.execute_operation(
        _request(reference, "normal"),
        output_dir=output,
        catalog_root=catalog_root,
    )
    assert isinstance(execution.result, OperationSuccess)
    assert (output / "manifest.json").is_file()
    controller.close()


def test_windows_job_prevents_child_breakaway(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    controller = WorkerController(limits=WorkerLimits(wall_seconds=5))
    _output, result = _run(
        controller,
        catalog_root,
        reference,
        tmp_path,
        "breakaway_probe",
    )
    assert result["breakaway_succeeded"] is False
    controller.close()


if sys.platform != "win32":
    setattr(test_windows_worker_supports_unicode_and_long_local_paths, "__test__", False)
    setattr(test_windows_job_prevents_child_breakaway, "__test__", False)


def test_controller_shutdown_cancels_and_reaps_active_worker(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    marker = tmp_path / "worker-pid"
    controller = WorkerController(limits=WorkerLimits(wall_seconds=10, grace_seconds=0.1))
    observed: list[HostFailureCode] = []

    def execute() -> None:
        try:
            controller.execute_operation(
                _request(reference, "sleep", marker=os.fspath(marker)),
                output_dir=tmp_path / "operation-shutdown",
                catalog_root=catalog_root,
            )
        except HostFailureException as exc:
            observed.append(exc.code)

    thread = Thread(target=execute)
    thread.start()
    deadline = time.monotonic() + 5
    pid: int | None = None
    while time.monotonic() < deadline:
        try:
            content = marker.read_text(encoding="ascii")
            if content:
                pid = int(content)
                break
        except FileNotFoundError:
            pass
        time.sleep(0.01)
    assert pid is not None
    controller.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert observed == [HostFailureCode.WORKER_CANCELLED]
    _assert_processes_stopped([pid])
    assert not (tmp_path / "operation-shutdown").exists()


def test_worker_capacity_refuses_without_queueing(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    marker = tmp_path / "capacity-worker-pid"
    cancellation = Event()
    controller = WorkerController(
        limits=WorkerLimits(
            wall_seconds=10,
            grace_seconds=0.1,
            concurrent_workers=1,
        )
    )
    first_outcome: list[HostFailureCode] = []

    def execute_first() -> None:
        try:
            controller.execute_operation(
                _request(reference, "sleep", marker=os.fspath(marker)),
                output_dir=tmp_path / "operation-capacity-first",
                catalog_root=catalog_root,
                cancel_event=cancellation,
            )
        except HostFailureException as exc:
            first_outcome.append(exc.code)

    thread = Thread(target=execute_first)
    thread.start()
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    assert _failure_code(
        controller,
        catalog_root,
        reference,
        tmp_path / "operation-capacity-second",
        "normal",
    ) is HostFailureCode.WORKER_CAPACITY
    cancellation.set()
    thread.join(timeout=5)
    assert first_outcome == [HostFailureCode.WORKER_CANCELLED]
    controller.close()


def test_worker_failure_publishes_no_bundle_or_session_reference(
    worker_component: tuple[Path, ComponentRef],
    tmp_path: Path,
) -> None:
    catalog_root, reference = worker_component
    state_root = tmp_path / "state"
    operation = tmp_path / "operation-service-timeout"
    controller = WorkerController(
        limits=WorkerLimits(wall_seconds=0.2, grace_seconds=0.1)
    )
    service = DefinedQuantService(
        catalog_root=catalog_root,
        session_state_root=state_root,
        worker_controller=controller,
    )
    with pytest.raises(HostFailureException) as caught:
        service.execute_recorded_operation(
            reference,
            output_dir=operation,
            literals={"mode": "sleep"},
            provenance={
                "source_kind": "synthetic",
                "interpretation_method": "caller_structured",
                "label": "No-partial-publication worker fixture.",
            },
        )
    assert caught.value.code is HostFailureCode.WORKER_TIMEOUT
    assert not operation.exists()
    assert not state_root.exists()
    service.close()
