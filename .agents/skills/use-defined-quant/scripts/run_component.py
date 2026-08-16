#!/usr/bin/env python3
"""Run one unmanaged operation through the shared Defined Quant runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _source_runtime import activate_source_runtime

activate_source_runtime()

from defined_quant.local_host_platform import (  # noqa: E402
    SecureFilesystemError,
    local_host_platform,
)
from defined_quant.operation_runtime import (  # noqa: E402
    OperationRuntimeError,
    operation_failure,
    prepare_output_directory,
    resolve_component,
)
from defined_quant.service import DefinedQuantService  # noqa: E402
from defined_quant.stdio_framing import (  # noqa: E402
    BinaryFrameError,
    configure_binary_descriptors,
    read_binary_to_eof,
    write_binary_bytes,
)
from defined_quant_protocol import (  # noqa: E402
    CallerProvenance,
    OperationErrorCode,
    OperationRequest,
    OperationResult,
    SvgArtifactRequest,
)
from pydantic import ValidationError  # noqa: E402


class DuplicateJsonKey(ValueError):
    """Raised when a JSON object repeats a member name."""


class InvalidJsonConstant(ValueError):
    """Raised for non-standard JSON constants such as NaN and Infinity."""


class ProtocolArgumentParser(argparse.ArgumentParser):
    """Argument parser whose non-help errors use the typed protocol envelope."""

    def error(self, message: str) -> None:
        del message
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OPERATION_REQUEST,
            "Command arguments do not satisfy the unmanaged runner interface.",
        )


def _parser() -> ProtocolArgumentParser:
    parser = ProtocolArgumentParser(
        description=(
            "Validate and execute one exactly identified Defined Quant component. "
            "This operation path is unmanaged."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--request",
        help="OperationRequest JSON path, or - for stdin.",
    )
    source.add_argument(
        "--component",
        help="Stable component ID or component path for the legacy unmanaged interface.",
    )
    parser.add_argument(
        "--input",
        help="Legacy component input JSON object path, or - for stdin.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Host output directory. It is deliberately outside OperationRequest.",
    )
    parser.add_argument(
        "--catalog-root",
        type=Path,
        help="Optional project root, categories directory, or installed catalog root.",
    )
    return parser


def _validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    return list(json.loads(exc.json(include_input=False, include_url=False)))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKey
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    del value
    raise InvalidJsonConstant


def _load_json_text(content: str) -> Any:
    return json.loads(
        content,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )


def _read_json(path: str, *, purpose: str) -> Any:
    try:
        if path == "-":
            content = read_binary_to_eof(sys.stdin.fileno()).decode(
                "utf-8",
                errors="strict",
            )
        else:
            content = local_host_platform().secure_filesystem.read_regular_file(
                Path(path).expanduser(),
                maximum_bytes=2 * 1024 * 1024,
            ).decode(
                "utf-8",
                errors="strict",
            )
        return _load_json_text(content)
    except (DuplicateJsonKey, InvalidJsonConstant) as exc:
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_JSON,
            f"{purpose} violates strict JSON object or number rules.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_JSON,
            f"{purpose} is not valid JSON.",
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    except UnicodeDecodeError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_JSON,
            f"{purpose} is not valid UTF-8.",
        ) from exc
    except BinaryFrameError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OPERATION_REQUEST,
            f"{purpose} could not be read.",
            details={"type": type(exc).__name__},
        ) from exc
    except (OSError, SecureFilesystemError) as exc:
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OPERATION_REQUEST,
            f"{purpose} could not be read.",
            details={"type": type(exc).__name__},
        ) from exc


def _read_operation_request(path: str) -> OperationRequest:
    value = _read_json(path, purpose="Operation request")
    try:
        return OperationRequest.model_validate(value)
    except ValidationError as exc:
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OPERATION_REQUEST,
            "Request does not satisfy the canonical OperationRequest schema.",
            details={"errors": _validation_details(exc)},
        ) from exc


def _read_component_input(path: str) -> dict[str, Any]:
    value = _read_json(path, purpose="Component input")
    if not isinstance(value, dict):
        raise OperationRuntimeError(
            OperationErrorCode.INVALID_OPERATION_REQUEST,
            "Component input JSON must contain one object.",
            details={"type": type(value).__name__},
        )
    return value


def _legacy_operation(args: argparse.Namespace) -> OperationResult:
    output_dir = prepare_output_directory(args.output_dir, component=None)
    record, component = resolve_component(
        args.component,
        catalog_root=args.catalog_root,
    )
    request = OperationRequest(
        component=component,
        input=_read_component_input(args.input),
        provenance=CallerProvenance(
            source_kind="user_attachment",
            interpretation_method="caller_structured",
            verification_status="unverified",
            label="Legacy CLI component input; unmanaged.",
        ),
        artifacts=SvgArtifactRequest(),
    )
    catalog_root = record.path.parents[1]
    with DefinedQuantService(catalog_root=catalog_root) as service:
        return service.execute_operation(request, output_dir=output_dir)


def _serialize_operation_result(result: OperationResult) -> tuple[bytes, bool]:
    try:
        payload = json.dumps(
            result.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        payload = (
            '{"error":{"code":"operation_failed","details":{},'
            '"message":"Protocol response serialization failed."},'
            '"operation_hash":null,"schema_version":1,"status":"failed"}'
        )
        return (payload + "\n").encode("utf-8"), False
    return (payload + "\n").encode("utf-8"), True


def main() -> int:
    request: OperationRequest | None = None
    try:
        configure_binary_descriptors(
            sys.stdin.fileno(),
            sys.stdout.fileno(),
            sys.stderr.fileno(),
        )
        args = _parser().parse_args()
        if args.request is not None:
            if args.input is not None:
                raise OperationRuntimeError(
                    OperationErrorCode.INVALID_OPERATION_REQUEST,
                    "--input is only valid with the legacy --component interface.",
                )
            request = _read_operation_request(args.request)
            with DefinedQuantService(catalog_root=args.catalog_root) as service:
                response = service.execute_operation(
                    request,
                    output_dir=args.output_dir,
                )
        else:
            if args.input is None:
                raise OperationRuntimeError(
                    OperationErrorCode.INVALID_OPERATION_REQUEST,
                    "--input is required with the legacy --component interface.",
                )
            response = _legacy_operation(args)
    except Exception as exc:
        response = operation_failure(exc, request=request)

    payload, serialized = _serialize_operation_result(response)
    if response.status == "succeeded":
        write_binary_bytes(sys.stdout.fileno(), payload)
        return 0 if serialized else 2
    write_binary_bytes(sys.stderr.fileno(), payload)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
