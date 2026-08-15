"""Transport-neutral execution for exactly identified Defined Quant components."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from defined_quant import __version__
from defined_quant.catalog import (
    ComponentRecord,
    component_models,
    component_record,
    load_component,
    verify_subject,
)
from defined_quant.charts import save_svg, visualization_hash
from defined_quant.types import (
    AmbiguousInput,
    ComponentContractError,
    ComponentLoadError,
    ComponentNotFound,
    ComponentOutput,
    ContractEvaluationError,
    DomainError,
    DQError,
    UnsupportedScope,
)
from defined_quant.validation import preflight
from defined_quant_protocol import (
    ComponentRef,
    FileDigest,
    OperationError,
    OperationErrorCode,
    OperationFailure,
    OperationManifest,
    OperationRequest,
    OperationResult,
    OperationSuccess,
    RunnerIdentity,
    SvgArtifact,
)
from pydantic import BaseModel, ValidationError

_RUNNER_NAME = "use_defined_quant"
_RUNNER_VERSION = "0.1.0"
_MANIFEST_MEMBER = "manifest.json"
_AT_FDCWD = -100
_RENAME_NOREPLACE = 0x00000001
_RENAME_EXCL = 0x00000004
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ExecutedComponent:
    """One validated component invocation before transport or bundle projection."""

    validated_input: BaseModel
    result: ComponentOutput


class _NoiseCapture:
    """Bounded text sink that records only whether a component emitted output."""

    def __init__(self) -> None:
        self.emitted = False

    def write(self, value: str) -> int:
        if value:
            self.emitted = True
        return len(value)

    def flush(self) -> None:
        return None


class OperationRuntimeError(Exception):
    """Stable runtime exception converted to a closed protocol failure."""

    def __init__(
        self,
        code: OperationErrorCode,
        message: str,
        *,
        component: ComponentRef | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.component = component
        self.details = details or {}


def _validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    return list(json.loads(exc.json(include_input=False, include_url=False)))


def _sanitize_json_value(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        return json.loads(encoded)
    except Exception:
        return {"unavailable_type": type(value).__name__}


def _safe_details(value: Any) -> dict[str, Any]:
    sanitized = _sanitize_json_value(value)
    if isinstance(sanitized, dict):
        return sanitized
    return {"value": sanitized}


def _safe_text(value: Any, *, fallback: str) -> str:
    try:
        text = str(value)
    except Exception:
        return fallback
    return text[:500] or fallback


def _dq_error_details(exc: DQError) -> dict[str, Any]:
    if isinstance(
        exc,
        (ComponentContractError, ComponentLoadError, ContractEvaluationError),
    ):
        return {"code": _safe_text(exc.code, fallback="component_integrity_error")}
    return {
        "code": _safe_text(exc.code, fallback="defined_quant_error"),
        "message": _safe_text(exc.message, fallback="Component error details unavailable."),
        "details": _sanitize_json_value(exc.details),
    }


def _mapped_dq_error(
    exc: DQError,
    *,
    component: ComponentRef | None,
) -> OperationRuntimeError:
    details = {"component_error": _dq_error_details(exc)}
    if isinstance(
        exc,
        (ComponentContractError, ComponentLoadError, ContractEvaluationError),
    ):
        return OperationRuntimeError(
            OperationErrorCode.COMPONENT_CONTRACT_ERROR,
            "The installed component or its declarative contract is invalid.",
            component=component,
            details=details,
        )
    if isinstance(exc, (AmbiguousInput, UnsupportedScope, DomainError)):
        return OperationRuntimeError(
            OperationErrorCode.COMPONENT_REFUSED,
            "The component refused the requested calculation.",
            component=component,
            details=details,
        )
    return OperationRuntimeError(
        OperationErrorCode.COMPONENT_EXECUTION_FAILED,
        "The component failed outside the supported refusal protocol.",
        component=component,
        details=details,
    )


def _quiet_component_call(
    callback: Callable[[], _T],
    *,
    component: ComponentRef | None,
) -> _T:
    stdout = _NoiseCapture()
    stderr = _NoiseCapture()
    result: object = None
    caught: Exception | None = None
    with redirect_stdout(cast(Any, stdout)), redirect_stderr(cast(Any, stderr)):
        try:
            result = callback()
        except Exception as exc:
            caught = exc
    if stdout.emitted or stderr.emitted:
        raise OperationRuntimeError(
            OperationErrorCode.COMPONENT_CONTRACT_ERROR,
            "Component code emitted process output outside the typed protocol.",
            component=component,
            details={
                "stdout_emitted": stdout.emitted,
                "stderr_emitted": stderr.emitted,
            },
        )
    if caught is not None:
        raise caught
    return cast(_T, result)


def component_reference(
    record: ComponentRecord,
    *,
    requested: ComponentRef | None = None,
) -> ComponentRef:
    try:
        digest = _quiet_component_call(
            lambda: verify_subject(
                record.component_id,
                root=record.path.parents[1],
            ),
            component=requested,
        )
        return ComponentRef(id=record.component_id, version=record.version, subject_hash=digest)
    except DQError as exc:
        raise _mapped_dq_error(exc, component=requested) from exc
    except ValidationError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.COMPONENT_CONTRACT_ERROR,
            "The installed component identity is not valid protocol data.",
            component=requested,
            details={"errors": _validation_details(exc)},
        ) from exc


def component_record_for_request(
    request: OperationRequest,
    *,
    catalog_root: Path | None,
) -> ComponentRecord:
    try:
        record = component_record(request.component.id, root=catalog_root)
    except ComponentNotFound as exc:
        raise OperationRuntimeError(
            OperationErrorCode.COMPONENT_NOT_FOUND,
            "The requested component is not installed in this catalog.",
            component=request.component,
        ) from exc
    except DQError as exc:
        raise _mapped_dq_error(exc, component=request.component) from exc
    installed_ref = component_reference(record, requested=request.component)
    if installed_ref != request.component:
        raise OperationRuntimeError(
            OperationErrorCode.COMPONENT_IDENTITY_MISMATCH,
            "The installed component does not match the exact requested identity.",
            component=request.component,
            details={"installed_component": installed_ref.model_dump(mode="json")},
        )
    return record


def resolve_component(
    component_identifier: str | Path,
    *,
    catalog_root: Path | None,
) -> tuple[ComponentRecord, ComponentRef]:
    """Resolve and freshly bind one installed component for an unmanaged caller."""

    try:
        record = component_record(component_identifier, root=catalog_root)
    except ComponentNotFound as exc:
        raise OperationRuntimeError(
            OperationErrorCode.COMPONENT_NOT_FOUND,
            "The requested component is not installed in this catalog.",
        ) from exc
    except DQError as exc:
        raise _mapped_dq_error(exc, component=None) from exc
    return record, component_reference(record)


def component_execution_models(
    record: ComponentRecord,
    *,
    component: ComponentRef,
) -> tuple[type[BaseModel], type[BaseModel]]:
    """Load the canonical input and output models without leaking component output."""

    try:
        return _quiet_component_call(
            lambda: component_models(record),
            component=component,
        )
    except DQError as exc:
        raise _mapped_dq_error(exc, component=component) from exc


def validate_component_input(
    record: ComponentRecord,
    model: type[BaseModel],
    raw_input: Mapping[str, Any],
    *,
    component: ComponentRef,
) -> BaseModel:
    """Validate raw evidence or request input through the canonical component model."""

    try:
        return _quiet_component_call(
            lambda: model.model_validate(raw_input),
            component=component,
        )
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        missing_fields = {
            str(error["loc"][0])
            for error in errors
            if error.get("type") == "missing" and error.get("loc")
        }
        guidance = record.metadata.get("guidance")
        questions = (
            guidance.get("required_questions", []) if isinstance(guidance, Mapping) else []
        )
        question_fields = {
            str(question["resolves_to"])
            for question in questions
            if isinstance(question, Mapping) and "resolves_to" in question
        }
        if errors and len(missing_fields) == len(errors) and missing_fields <= question_fields:
            try:
                _quiet_component_call(
                    lambda: preflight(record.component_id, **raw_input),
                    component=component,
                )
            except DQError as question:
                raise _mapped_dq_error(question, component=component) from question
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_COMPONENT_INPUT,
            "Input does not satisfy the component's canonical Inputs model.",
            component=component,
            details={"errors": _validation_details(exc)},
        ) from exc
    except DQError as exc:
        raise _mapped_dq_error(exc, component=component) from exc


def _invoke_component(
    record: ComponentRecord,
    validated_input: BaseModel,
    *,
    component: ComponentRef,
) -> Any:
    try:
        callable_ = _quiet_component_call(
            lambda: load_component(record),
            component=component,
        )
    except DQError as exc:
        raise _mapped_dq_error(exc, component=component) from exc
    try:
        return _quiet_component_call(
            lambda: callable_(**validated_input.model_dump(mode="python")),
            component=component,
        )
    except OperationRuntimeError:
        raise
    except DQError as exc:
        raise _mapped_dq_error(exc, component=component) from exc
    except ValidationError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.OUTPUT_VALIDATION_FAILED,
            "The component could not construct output satisfying its canonical model.",
            component=component,
            details={"errors": _validation_details(exc)},
        ) from exc
    except Exception as exc:
        raise OperationRuntimeError(
            OperationErrorCode.COMPONENT_EXECUTION_FAILED,
            "The component callable failed outside the Defined Quant refusal protocol.",
            component=component,
            details={"type": type(exc).__name__},
        ) from exc


def _validate_component_output(
    record: ComponentRecord,
    model: type[BaseModel],
    raw_result: Any,
    *,
    component: ComponentRef,
) -> ComponentOutput:
    if not issubclass(model, ComponentOutput):
        raise ComponentContractError(
            "component Output must extend defined_quant.types.ComponentOutput",
            component_id=record.component_id,
        )
    if not isinstance(raw_result, model):
        raise ComponentContractError(
            "component callable must return an instance of its declared Output model",
            component_id=record.component_id,
            details={"returned_type": type(raw_result).__name__},
        )
    try:
        result = _quiet_component_call(
            lambda: model.model_validate(raw_result.model_dump(mode="python")),
            component=component,
        )
    except ValidationError as exc:
        raise ComponentContractError(
            "component returned data that does not satisfy its canonical Output model",
            component_id=record.component_id,
            details={"errors": _validation_details(exc)},
        ) from exc

    if result.component_id != component.id:
        raise ComponentContractError(
            "component output ID does not match the requested component",
            component_id=record.component_id,
            details={"output_component_id": result.component_id},
        )
    if result.version != component.version:
        raise ComponentContractError(
            "component output version does not match the requested component",
            component_id=record.component_id,
            details={"output_version": result.version},
        )
    if result.subject_hash != component.subject_hash:
        raise ComponentContractError(
            "component output subject hash does not match the requested component",
            component_id=record.component_id,
            details={"output_subject_hash": result.subject_hash},
        )
    return result


def execute_validated_component(
    record: ComponentRecord,
    output_model: type[BaseModel],
    validated_input: BaseModel,
    *,
    component: ComponentRef,
) -> ComponentOutput:
    """Invoke and validate one component using already validated canonical input."""

    raw_result = _invoke_component(
        record,
        validated_input,
        component=component,
    )
    try:
        return _validate_component_output(
            record,
            output_model,
            raw_result,
            component=component,
        )
    except ComponentContractError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.OUTPUT_VALIDATION_FAILED,
            "The component violated its canonical output contract.",
            component=component,
            details={"component_error": _dq_error_details(exc)},
        ) from exc
    except DQError as exc:
        raise _mapped_dq_error(exc, component=component) from exc


def execute_component(
    record: ComponentRecord,
    component: ComponentRef,
    raw_input: Mapping[str, Any],
) -> ExecutedComponent:
    """Run the shared validation/invocation core without requiring protocol JSON."""

    input_model, output_model = component_execution_models(record, component=component)
    validated_input = validate_component_input(
        record,
        input_model,
        raw_input,
        component=component,
    )
    result = execute_validated_component(
        record,
        output_model,
        validated_input,
        component=component,
    )
    return ExecutedComponent(validated_input=validated_input, result=result)


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return stem or "visualization"


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _normalized_model_bytes(
    model: BaseModel,
    *,
    component: ComponentRef,
    error_code: OperationErrorCode,
    message: str,
) -> bytes:
    try:
        value = _quiet_component_call(
            lambda: model.model_dump(mode="json"),
            component=component,
        )
        return _pretty_json_bytes(value)
    except OperationRuntimeError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise OperationRuntimeError(
            error_code,
            message,
            component=component,
            details={"type": type(exc).__name__},
        ) from exc


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_output_directory(
    argument: Path,
    *,
    component: ComponentRef | None,
) -> Path:
    expanded = argument.expanduser()
    if expanded.is_symlink():
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OUTPUT_DIRECTORY,
            "Output directory must not be a symbolic link.",
            component=component,
        )
    output_dir = expanded.resolve()
    if output_dir in {Path(output_dir.anchor), Path.home().resolve()}:
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OUTPUT_DIRECTORY,
            "Output directory must not be a filesystem root or the user home directory.",
            component=component,
        )
    if output_dir.exists() and not output_dir.is_dir():
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OUTPUT_DIRECTORY,
            "Output directory target exists and is not a directory.",
            component=component,
        )
    return output_dir


def prepare_output_directory(
    argument: Path,
    *,
    component: ComponentRef | None,
) -> Path:
    output_dir = _safe_output_directory(argument, component=component)
    _assert_output_available(output_dir, component=component)
    return output_dir


def _assert_output_available(
    output_dir: Path,
    *,
    component: ComponentRef | None = None,
) -> None:
    if not os.path.lexists(output_dir):
        return
    raise OperationRuntimeError(
        OperationErrorCode.OUTPUT_EXISTS,
        "Output directory already exists; choose a new directory.",
        component=component,
    )


def _assert_flat_members(members: tuple[str, ...]) -> None:
    if any(
        "/" in member
        or "\\" in member
        or Path(member).name != member
        or member in {".", ".."}
        for member in members
    ):
        raise OperationRuntimeError(
            OperationErrorCode.ARTIFACT_WRITE_FAILED,
            "The runner generated a non-flat output member path.",
        )


def _atomic_rename_no_replace(staging_dir: Path, output_dir: Path) -> None:
    """Atomically rename a directory while refusing every existing destination entry."""

    source = os.fsencode(staging_dir)
    destination = os.fsencode(output_dir)
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "renameat2 is unavailable")
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            _AT_FDCWD,
            source,
            _AT_FDCWD,
            destination,
            _RENAME_NOREPLACE,
        )
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise OSError(errno.ENOTSUP, "renamex_np is unavailable")
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source, destination, _RENAME_EXCL)
    elif os.name == "nt":
        os.rename(staging_dir, output_dir)
        return
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory publication is unavailable")

    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), os.fspath(output_dir))


def _publish_staged_directory(
    staging_dir: Path,
    output_dir: Path,
    members: tuple[str, ...],
    *,
    component: ComponentRef,
) -> None:
    """Publish a complete new output directory with one atomic rename."""

    _assert_flat_members(members)
    _assert_output_available(output_dir, component=component)
    try:
        _atomic_rename_no_replace(staging_dir, output_dir)
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY} or os.path.lexists(output_dir):
            raise OperationRuntimeError(
                OperationErrorCode.OUTPUT_EXISTS,
                "Output directory appeared before publication completed; choose a new directory.",
                component=component,
            ) from exc
        raise


def verify_operation_bundle(
    directory: Path,
    manifest: OperationManifest,
) -> None:
    """Reconcile every staged member and digest before atomic publication."""

    declared: tuple[FileDigest | SvgArtifact, ...] = (
        manifest.input,
        manifest.result,
        *manifest.artifacts,
    )
    expected_names = {member.path for member in declared} | {_MANIFEST_MEMBER}
    try:
        entries = tuple(directory.iterdir())
        if (
            {entry.name for entry in entries} != expected_names
            or any(entry.is_symlink() or not entry.is_file() for entry in entries)
        ):
            raise ValueError("operation bundle members do not match the manifest")

        manifest_bytes = (directory / _MANIFEST_MEMBER).read_bytes()
        if manifest_bytes != _pretty_json_bytes(manifest.model_dump(mode="json")):
            raise ValueError("operation manifest bytes are not canonical")
        if OperationManifest.model_validate_json(manifest_bytes) != manifest:
            raise ValueError("operation manifest does not round-trip exactly")

        for member in declared:
            if _sha256((directory / member.path).read_bytes()) != member.sha256:
                raise ValueError("operation bundle member digest does not match")
    except OperationRuntimeError:
        raise
    except (OSError, ValidationError, ValueError) as exc:
        raise OperationRuntimeError(
            OperationErrorCode.ARTIFACT_WRITE_FAILED,
            "The staged operation bundle failed deterministic reconciliation.",
            component=manifest.component,
            details={"type": type(exc).__name__},
        ) from exc


def _materialize(
    request: OperationRequest,
    result: ComponentOutput,
    output_dir: Path,
    *,
    input_bytes: bytes,
    result_bytes: bytes,
) -> OperationManifest:
    input_member = "input.json"
    result_member = "result.json"
    artifact_members = (
        tuple(
            f"{index:02d}-{_safe_stem(spec.id)}.svg"
            for index, spec in enumerate(result.visualizations, start=1)
        )
        if request.artifacts is not None
        else ()
    )
    members = (input_member, result_member, *artifact_members, _MANIFEST_MEMBER)
    _assert_flat_members(members)

    try:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        _assert_output_available(output_dir, component=request.component)
        staging_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.dq-stage-", dir=output_dir.parent)
        )
    except OperationRuntimeError:
        raise
    except OSError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.ARTIFACT_WRITE_FAILED,
            "Could not prepare the operation output directory.",
            component=request.component,
            details={"type": type(exc).__name__},
        ) from exc

    published = False
    try:
        (staging_dir / input_member).write_bytes(input_bytes)
        (staging_dir / result_member).write_bytes(result_bytes)

        artifacts: list[SvgArtifact] = []
        requested_visualizations = (
            result.visualizations if request.artifacts is not None else ()
        )
        for spec, member in zip(requested_visualizations, artifact_members, strict=True):
            artifact_path = staging_dir / member
            save_svg(spec, artifact_path)
            artifact_bytes = artifact_path.read_bytes()
            artifacts.append(
                SvgArtifact(
                    path=member,
                    visualization_id=spec.id,
                    title=spec.title,
                    alt_text=spec.alt_text,
                    visualization_hash=visualization_hash(spec),
                    sha256=_sha256(artifact_bytes),
                    renderer="defined_quant_svg",
                    renderer_version=__version__,
                )
            )

        manifest = OperationManifest(
            execution_mode="unmanaged",
            operation_hash=request.operation_hash,
            request=request,
            component=request.component,
            runner=RunnerIdentity(name=_RUNNER_NAME, version=_RUNNER_VERSION),
            input=FileDigest(path=input_member, sha256=_sha256(input_bytes)),
            result=FileDigest(path=result_member, sha256=_sha256(result_bytes)),
            artifacts=tuple(artifacts),
        )
        (staging_dir / _MANIFEST_MEMBER).write_bytes(
            _pretty_json_bytes(manifest.model_dump(mode="json"))
        )
        verify_operation_bundle(staging_dir, manifest)
        _publish_staged_directory(
            staging_dir,
            output_dir,
            members,
            component=request.component,
        )
        published = not staging_dir.exists()
        return manifest
    except OperationRuntimeError:
        raise
    except (OSError, ValidationError, ValueError) as exc:
        raise OperationRuntimeError(
            OperationErrorCode.ARTIFACT_WRITE_FAILED,
            "The component completed, but its portable artifacts could not be materialized.",
            component=request.component,
            details={"type": type(exc).__name__},
        ) from exc
    finally:
        if not published and staging_dir.exists():
            shutil.rmtree(staging_dir)


def _execute_operation_success(
    request: OperationRequest,
    record: ComponentRecord,
    output_dir: Path,
) -> OperationSuccess:
    inputs_model, output_model = component_execution_models(
        record,
        component=request.component,
    )
    validated_input = validate_component_input(
        record,
        inputs_model,
        request.input,
        component=request.component,
    )
    input_bytes = _normalized_model_bytes(
        validated_input,
        component=request.component,
        error_code=OperationErrorCode.INVALID_COMPONENT_INPUT,
        message="Normalized component input contains a non-finite or non-JSON value.",
    )
    result = execute_validated_component(
        record,
        output_model,
        validated_input,
        component=request.component,
    )
    result_bytes = _normalized_model_bytes(
        result,
        component=request.component,
        error_code=OperationErrorCode.OUTPUT_VALIDATION_FAILED,
        message="Normalized component output contains a non-finite or non-JSON value.",
    )
    manifest = _materialize(
        request,
        result,
        output_dir,
        input_bytes=input_bytes,
        result_bytes=result_bytes,
    )
    return OperationSuccess(manifest=manifest)


def operation_failure(
    exc: Exception,
    *,
    request: OperationRequest | None,
) -> OperationFailure:
    try:
        if isinstance(exc, OperationRuntimeError):
            error = OperationError(
                code=exc.code,
                message=_safe_text(exc.message, fallback="The unmanaged operation failed."),
                component=exc.component,
                details=_safe_details(exc.details),
            )
        else:
            error = OperationError(
                code=OperationErrorCode.OPERATION_FAILED,
                message="The unmanaged operation failed unexpectedly.",
                component=request.component if request is not None else None,
                details={"type": type(exc).__name__},
            )
        return OperationFailure(
            operation_hash=request.operation_hash if request is not None else None,
            error=error,
        )
    except Exception:
        return OperationFailure(
            operation_hash=None,
            error=OperationError(
                code=OperationErrorCode.OPERATION_FAILED,
                message="The unmanaged operation failure could not be serialized safely.",
                details={},
            ),
        )


def execute_operation(
    request: OperationRequest,
    *,
    output_dir: Path,
    catalog_root: Path | None = None,
) -> OperationResult:
    """Execute one canonical unmanaged request without exposing runtime exceptions."""

    validated_request: OperationRequest | None = None
    try:
        request_value: Any = (
            request.model_dump(mode="python", warnings=False)
            if isinstance(request, OperationRequest)
            else request
        )
        try:
            validated_request = OperationRequest.model_validate(request_value)
        except ValidationError as exc:
            raise OperationRuntimeError(
                OperationErrorCode.INVALID_OPERATION_REQUEST,
                "Request does not satisfy the canonical OperationRequest schema.",
                details={"errors": _validation_details(exc)},
            ) from exc
        prepared_output = prepare_output_directory(
            output_dir,
            component=validated_request.component,
        )
        record = component_record_for_request(
            validated_request,
            catalog_root=catalog_root,
        )
        return _execute_operation_success(
            validated_request,
            record,
            prepared_output,
        )
    except Exception as exc:
        return operation_failure(exc, request=validated_request)


def _execute_resolved_operation(
    request: OperationRequest,
    record: ComponentRecord,
    *,
    output_dir: Path,
) -> OperationResult:
    """Execute a request whose output target and component were already resolved."""

    try:
        return _execute_operation_success(request, record, output_dir)
    except Exception as exc:
        return operation_failure(exc, request=request)


__all__ = [
    "ExecutedComponent",
    "OperationRuntimeError",
    "component_execution_models",
    "component_record_for_request",
    "component_reference",
    "execute_component",
    "execute_operation",
    "execute_validated_component",
    "operation_failure",
    "prepare_output_directory",
    "resolve_component",
    "validate_component_input",
    "verify_operation_bundle",
]
