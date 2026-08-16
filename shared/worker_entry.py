"""Strict one-request entry point for the bounded subprocess worker."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import defined_quant
from defined_quant.data_records import cas_json_bytes, strict_cas_json_loads
from defined_quant.host_failures import HostFailureException
from defined_quant.operation_runtime import (
    WorkerStreamLimitExceeded,
    component_execution_models,
    component_record_for_request,
    execute_operation,
    worker_output_limit,
)
from defined_quant_protocol import OperationFailure, OperationRequest
from pydantic import ValidationError

_MAX_WORKER_REQUEST_BYTES = 2 * 1024 * 1024
_CAPTURE_LIMIT_BYTES = 1024 * 1024


def _request() -> tuple[
    Literal["operation", "inspect_component", "compare_ports"],
    OperationRequest | dict[str, Any],
    Path | None,
    Path | None,
]:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(0, min(64 * 1024, _MAX_WORKER_REQUEST_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_WORKER_REQUEST_BYTES:
            raise ValueError("worker request exceeds its fixed limit")
    content = b"".join(chunks)
    value = strict_cas_json_loads(content, maximum_bytes=_MAX_WORKER_REQUEST_BYTES)
    if not isinstance(value, dict):
        raise ValueError("worker request envelope is invalid")
    if value.get("schema_version") != 1:
        raise ValueError("worker request schema is unsupported")
    if set(value) == {"catalog_root", "output_dir", "request", "schema_version"}:
        action: Literal["operation", "inspect_component", "compare_ports"] = "operation"
        payload: OperationRequest | dict[str, Any] = OperationRequest.model_validate(
            value.get("request")
        )
    elif set(value) == {"action", "arguments", "catalog_root", "schema_version"}:
        raw_action = value.get("action")
        if raw_action not in {"inspect_component", "compare_ports"}:
            raise ValueError("worker request action is invalid")
        action = cast(Literal["inspect_component", "compare_ports"], raw_action)
        raw_arguments = value.get("arguments")
        if not isinstance(raw_arguments, Mapping):
            raise ValueError("worker request arguments are invalid")
        payload = dict(raw_arguments)
    else:
        raise ValueError("worker request envelope is invalid")
    output_value = value.get("output_dir")
    catalog_value = value.get("catalog_root")
    if action == "operation" and (
        not isinstance(output_value, str) or not output_value or "\x00" in output_value
    ):
        raise ValueError("worker output path is invalid")
    if catalog_value is not None and (
        not isinstance(catalog_value, str) or not catalog_value or "\x00" in catalog_value
    ):
        raise ValueError("worker catalog path is invalid")
    output_dir = Path(output_value) if isinstance(output_value, str) else None
    catalog_root = None if catalog_value is None else Path(catalog_value)
    if (output_dir is not None and not output_dir.is_absolute()) or (
        catalog_root is not None and not catalog_root.is_absolute()
    ):
        raise ValueError("worker paths must be absolute")
    return action, payload, output_dir, catalog_root


def _activate_catalog(catalog_root: Path | None) -> None:
    if catalog_root is None:
        return
    package_root = catalog_root / "categories"
    value = os.fspath(package_root)
    if value not in defined_quant.__path__:
        defined_quant.__path__.append(value)


def _input_fields(request: OperationRequest, catalog_root: Path | None) -> tuple[str, ...]:
    try:
        record = component_record_for_request(request, catalog_root=catalog_root)
        input_model, _output_model = component_execution_models(
            record,
            component=request.component,
        )
        return tuple(input_model.model_fields)
    except Exception:
        return ()


def _write_control(control_fd: int, value: dict[str, Any]) -> None:
    content = cas_json_bytes(value)
    view = memoryview(content)
    while view:
        written = os.write(control_fd, view)
        if written <= 0:
            raise OSError
        view = view[written:]


def worker_main(control_fd: int) -> int:
    """Read one canonical request and emit one canonical control response."""

    try:
        action, payload, output_dir, catalog_root = _request()
        _activate_catalog(catalog_root)
        with worker_output_limit(_CAPTURE_LIMIT_BYTES):
            if action == "operation":
                assert isinstance(payload, OperationRequest)
                assert output_dir is not None
                result = execute_operation(
                    payload,
                    output_dir=output_dir,
                    catalog_root=catalog_root,
                )
                input_fields = _input_fields(payload, catalog_root)
            else:
                from defined_quant.worker_inspection import (
                    compare_ports,
                    inspect_component,
                )

                assert isinstance(payload, dict)
                handler = inspect_component if action == "inspect_component" else compare_ports
                try:
                    data = handler(payload, catalog_root=catalog_root)
                except HostFailureException as exc:
                    _write_control(
                        control_fd,
                        {
                            "failure": exc.failure.model_dump(mode="json"),
                            "kind": "host_failure",
                            "schema_version": 1,
                        },
                    )
                    return 0
                _write_control(
                    control_fd,
                    {
                        "data": data,
                        "kind": "inspection_result",
                        "schema_version": 1,
                    },
                )
                return 0
        if (
            isinstance(result, OperationFailure)
            and result.error.details.get("type") == "MemoryError"
        ):
            _write_control(
                control_fd,
                {"kind": "resource_limit", "schema_version": 1},
            )
            return 0
        _write_control(
            control_fd,
            {
                "input_fields": list(input_fields),
                "kind": "operation_result",
                "result": result.model_dump(mode="json"),
                "schema_version": 1,
            },
        )
        return 0
    except (MemoryError, WorkerStreamLimitExceeded):
        try:
            _write_control(
                control_fd,
                {"kind": "resource_limit", "schema_version": 1},
            )
            return 0
        except Exception:
            return 70
    except (OSError, TypeError, ValueError, ValidationError):
        return 70
    except BaseException:
        return 70
    finally:
        try:
            os.close(control_fd)
        except OSError:
            pass


__all__ = ["worker_main"]
