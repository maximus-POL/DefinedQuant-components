#!/usr/bin/env python3
"""Execute any installed Defined Quant component and materialize declared views."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

from _source_runtime import activate_source_runtime
from pydantic import BaseModel, ValidationError

activate_source_runtime()

from defined_quant import (  # noqa: E402
    AdapterIdentity,
    ComponentIdentity,
    DashboardSpec,
    FileDigest,
    InterpretationMethod,
    OperationError,
    OperationFailure,
    OperationManifest,
    OperationProvenance,
    OperationReceipt,
    OperationRequest,
    OperationSuccess,
    OperationViewRequest,
    SourceKind,
    VerificationStatus,
    ViewBundleSpec,
    __version__,
    component_models,
    component_record,
    dashboard_hash,
    load_component,
    operation_hash,
    save_dashboard_html,
    save_dashboard_svg,
    save_svg,
    select_view,
    subject_hash,
    view_hash,
    visualization_hash,
)
from defined_quant.catalog import ComponentRecord  # noqa: E402
from defined_quant.types import (  # noqa: E402
    ComponentContractError,
    ComponentOutput,
    DQError,
)

_ADAPTER_NAME = "use-defined-quant"
_ADAPTER_SCHEMA_VERSION = 2


class AdapterError(Exception):
    """A stable error raised by the generic host adapter."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        component_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.component_id = component_id
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
        if self.component_id is not None:
            error["component_id"] = self.component_id
        return error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate inputs, execute one installed Defined Quant component, and render every "
            "component-declared visualization."
        )
    )
    request_source = parser.add_mutually_exclusive_group(required=True)
    request_source.add_argument(
        "--request",
        help=(
            "Canonical OperationRequest JSON path, or - for stdin. This is the preferred "
            "agent-facing interface."
        ),
    )
    request_source.add_argument(
        "--component",
        help="Stable component ID or component path for the legacy flag interface.",
    )
    parser.add_argument(
        "--input",
        help="Legacy component input JSON object path, or - for stdin.",
    )
    parser.add_argument("--output-dir", type=Path, help="Legacy output directory.")
    parser.add_argument(
        "--catalog-root",
        type=Path,
        help="Optional project root, categories directory, or installed-package catalog root.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace generated files with matching names in the output directory.",
    )
    parser.add_argument(
        "--view-use-case",
        choices=("chat", "portable", "print"),
        default="chat",
        help="Deterministic view-selection intent. Defaults to the component's chat view.",
    )
    parser.add_argument(
        "--supported-media-type",
        action="append",
        choices=("text/html", "image/svg+xml"),
        default=[],
        help=(
            "Repeat to constrain host-supported view media types. "
            "When omitted, every component-declared type is supported."
        ),
    )
    return parser


def _read_payload(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        with Path(path).expanduser().open(encoding="utf-8") as handle:
            value = json.load(handle)
    if not isinstance(value, dict):
        raise AdapterError(
            "invalid_component_input",
            "Input JSON must contain one object.",
            details={"type": type(value).__name__},
        )
    return value


def _read_operation_request(path: str) -> OperationRequest:
    try:
        if path == "-":
            value = json.load(sys.stdin)
        else:
            with Path(path).expanduser().open(encoding="utf-8") as handle:
                value = json.load(handle)
        return OperationRequest.model_validate(value)
    except ValidationError as exc:
        raise AdapterError(
            "invalid_operation_request",
            "Request does not satisfy the canonical OperationRequest schema.",
            details={"errors": _validation_details(exc)},
        ) from exc


def _validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    return list(json.loads(exc.json(include_input=False, include_url=False)))


def _validate_input(
    model: type[BaseModel],
    payload: dict[str, Any],
    *,
    component_id: str,
) -> BaseModel:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise AdapterError(
            "invalid_component_input",
            "Input does not satisfy the component's canonical Inputs model.",
            component_id=component_id,
            details={"errors": _validation_details(exc)},
        ) from exc


def _execute(
    record: ComponentRecord,
    validated_input: BaseModel,
) -> Any:
    component = load_component(record)
    keyword_arguments = validated_input.model_dump(mode="python")
    try:
        return component(**keyword_arguments)
    except DQError:
        raise
    except Exception as exc:
        raise AdapterError(
            "component_execution_failed",
            "The component callable failed outside the Defined Quant refusal protocol.",
            component_id=record.component_id,
            details={"type": type(exc).__name__, "message": str(exc)},
        ) from exc


def _validate_output(
    record: ComponentRecord,
    model: type[BaseModel],
    raw_result: Any,
    *,
    expected_subject_hash: str,
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
        result = model.model_validate(raw_result.model_dump(mode="python"))
    except ValidationError as exc:
        raise ComponentContractError(
            "component returned data that does not satisfy its canonical Output model",
            component_id=record.component_id,
            details={"errors": _validation_details(exc)},
        ) from exc

    if result.component_id != record.component_id:
        raise ComponentContractError(
            "component output ID does not match its catalog record",
            component_id=record.component_id,
            details={"output_component_id": result.component_id},
        )
    if result.version != record.version:
        raise ComponentContractError(
            "component output version does not match its catalog record",
            component_id=record.component_id,
            details={"catalog_version": record.version, "output_version": result.version},
        )
    if result.subject_hash != expected_subject_hash:
        raise ComponentContractError(
            "component output subject hash does not match the current behavior and contract",
            component_id=record.component_id,
            details={
                "expected_subject_hash": expected_subject_hash,
                "output_subject_hash": result.subject_hash,
            },
        )
    return result


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return stem or "visualization"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _write_bytes(path: Path, content: bytes, *, overwrite: bool) -> None:
    mode = "wb" if overwrite else "xb"
    with path.open(mode) as handle:
        handle.write(content)


def _assert_targets_available(paths: list[Path], *, overwrite: bool) -> None:
    if overwrite:
        return
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise AdapterError(
            "output_exists",
            "Generated output files already exist; choose a new directory or pass --overwrite.",
            details={"paths": existing},
        )


def _materialize(
    record: ComponentRecord,
    operation_request: OperationRequest,
    validated_input: BaseModel,
    result: ComponentOutput,
    output_dir_argument: Path,
    *,
    expected_subject_hash: str,
    overwrite: bool,
) -> OperationManifest:
    view_use_case: Literal["chat", "portable", "print"] = (
        operation_request.view.use_case.value
    )
    supported_media_types = tuple(
        media_type.value for media_type in operation_request.view.supported_media_types
    )
    output_dir = output_dir_argument.expanduser().resolve()
    if output_dir in {Path(output_dir.anchor), Path.home().resolve()}:
        raise AdapterError(
            "invalid_output_directory",
            "Output directory must not be a filesystem root or the user home directory.",
            component_id=record.component_id,
            details={"path": str(output_dir)},
        )
    normalized_input_path = output_dir / "input.json"
    result_path = output_dir / "result.json"
    manifest_path = output_dir / "manifest.json"
    dashboard = getattr(result, "dashboard", None)
    if dashboard is not None and not isinstance(dashboard, DashboardSpec):
        raise ComponentContractError(
            "an output field named dashboard must satisfy defined_quant.charts.DashboardSpec",
            component_id=record.component_id,
        )
    view_bundle = getattr(result, "view_bundle", None)
    if view_bundle is not None and not isinstance(view_bundle, ViewBundleSpec):
        raise ComponentContractError(
            "an output field named view_bundle must satisfy "
            "defined_quant.charts.ViewBundleSpec",
            component_id=record.component_id,
        )
    if view_bundle is not None and dashboard is None:
        raise ComponentContractError(
            "view_bundle requires a component-declared DashboardSpec",
            component_id=record.component_id,
        )
    selected_view = (
        select_view(
            view_bundle,
            use_case=view_use_case,
            supported_media_types=supported_media_types,
        )
        if view_bundle is not None
        else None
    )
    legacy_dashboard_path = (
        output_dir / f"00-{_safe_stem(dashboard.id)}.svg"
        if dashboard is not None and view_bundle is None
        else None
    )
    view_paths = (
        [
            (
                view,
                output_dir
                / (
                    f"00-{_safe_stem(view.id)}"
                    + (".html" if view.media_type == "text/html" else ".svg")
                ),
            )
            for view in view_bundle.views
        ]
        if view_bundle is not None
        else []
    )
    artifact_paths = [
        output_dir / f"{index:02d}-{_safe_stem(spec.id)}.svg"
        for index, spec in enumerate(result.visualizations, start=1)
    ]

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _assert_targets_available(
            [
                normalized_input_path,
                result_path,
                manifest_path,
                *(
                    [legacy_dashboard_path]
                    if legacy_dashboard_path is not None
                    else []
                ),
                *(path for _, path in view_paths),
                *artifact_paths,
            ],
            overwrite=overwrite,
        )

        input_data = validated_input.model_dump(mode="json")
        result_data = result.model_dump(mode="json")
        input_bytes = _json_bytes(input_data)
        result_bytes = _json_bytes(result_data)
        _write_bytes(normalized_input_path, input_bytes, overwrite=overwrite)
        _write_bytes(result_path, result_bytes, overwrite=overwrite)

        artifacts: list[dict[str, Any]] = []
        if dashboard is not None and legacy_dashboard_path is not None:
            save_dashboard_svg(
                dashboard,
                result.visualizations,
                legacy_dashboard_path,
                overwrite=overwrite,
            )
            dashboard_bytes = legacy_dashboard_path.read_bytes()
            artifacts.append(
                {
                    "kind": "image",
                    "role": "primary",
                    "dashboard_id": dashboard.id,
                    "title": dashboard.title,
                    "alt_text": dashboard.alt_text,
                    "media_type": "image/svg+xml",
                    "path": str(legacy_dashboard_path),
                    "dashboard_hash": dashboard_hash(
                        dashboard,
                        result.visualizations,
                    ),
                    "artifact_sha256": hashlib.sha256(dashboard_bytes).hexdigest(),
                    "renderer": "defined_quant.charts:render_dashboard_svg",
                    "renderer_version": __version__,
                }
            )
        if dashboard is not None and view_bundle is not None and selected_view is not None:
            for view, view_path in view_paths:
                if view.renderer == "dashboard_html":
                    save_dashboard_html(
                        dashboard,
                        result.visualizations,
                        view_path,
                        overwrite=overwrite,
                    )
                    renderer = "defined_quant.charts:render_dashboard_html"
                    kind = "interactive"
                else:
                    save_dashboard_svg(
                        dashboard,
                        result.visualizations,
                        view_path,
                        overwrite=overwrite,
                    )
                    renderer = "defined_quant.charts:render_dashboard_svg"
                    kind = "image"
                artifact_bytes = view_path.read_bytes()
                artifacts.append(
                    {
                        "kind": kind,
                        "role": (
                            "primary" if view.id == selected_view.id else "alternative"
                        ),
                        "view_id": view.id,
                        "dashboard_id": dashboard.id,
                        "title": dashboard.title,
                        "alt_text": dashboard.alt_text,
                        "media_type": view.media_type,
                        "interactive": view.interactive,
                        "path": str(view_path),
                        "view_hash": view_hash(
                            view,
                            dashboard,
                            result.visualizations,
                        ),
                        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                        "renderer": renderer,
                        "renderer_version": __version__,
                    }
                )
        for spec, artifact_path in zip(
            result.visualizations,
            artifact_paths,
            strict=True,
        ):
            save_svg(spec, artifact_path, overwrite=overwrite)
            artifact_bytes = artifact_path.read_bytes()
            artifacts.append(
                {
                    "kind": "image",
                    "role": "supporting" if dashboard is not None else "primary",
                    "visualization_id": spec.id,
                    "title": spec.title,
                    "alt_text": spec.alt_text,
                    "media_type": "image/svg+xml",
                    "path": str(artifact_path),
                    "visualization_hash": visualization_hash(spec),
                    "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                    "renderer": "defined_quant.charts:render_svg",
                    "renderer_version": __version__,
                }
            )

        manifest_data: dict[str, Any] = {
            "schema_version": _ADAPTER_SCHEMA_VERSION,
            "operation": OperationReceipt(
                operation_hash=operation_hash(operation_request),
                provenance=operation_request.provenance,
            ),
            "adapter": AdapterIdentity(version=_ADAPTER_SCHEMA_VERSION),
            "component": ComponentIdentity(
                id=record.component_id,
                title=record.metadata.get("title"),
                version=record.version,
                callable=record.callable_path,
                subject_hash=expected_subject_hash,
            ),
            "input": FileDigest(
                path=str(normalized_input_path),
                sha256=hashlib.sha256(input_bytes).hexdigest(),
            ),
            "result": FileDigest(
                path=str(result_path),
                sha256=hashlib.sha256(result_bytes).hexdigest(),
            ),
            "manifest_path": str(manifest_path),
            "artifacts": artifacts,
        }
        if view_bundle is not None and selected_view is not None:
            default_selected = (
                view_use_case == "chat"
                and selected_view.id == view_bundle.default_chat_view_id
            )
            use_case_match = view_use_case in selected_view.use_cases
            manifest_data["view_selection"] = {
                "use_case": view_use_case,
                "selected_view_id": selected_view.id,
                "selection_reason": (
                    "default_chat"
                    if default_selected
                    else "use_case_match"
                    if use_case_match
                    else "fallback"
                ),
                "supported_media_types": (
                    list(supported_media_types) if supported_media_types else ["*"]
                ),
            }
        manifest = OperationManifest.model_validate(manifest_data)
        _write_bytes(
            manifest_path,
            _json_bytes(manifest.model_dump(mode="json", exclude_none=True)),
            overwrite=overwrite,
        )
    except AdapterError:
        raise
    except OSError as exc:
        raise AdapterError(
            "artifact_write_failed",
            "The component completed, but its result artifacts could not be written.",
            component_id=record.component_id,
            details={"type": type(exc).__name__, "message": str(exc)},
        ) from exc
    return manifest


def _run(
    component_identifier: str,
    payload: dict[str, Any],
    operation_request: OperationRequest,
) -> OperationManifest:
    catalog_root = (
        Path(operation_request.catalog_root)
        if operation_request.catalog_root is not None
        else None
    )
    record = component_record(component_identifier, root=catalog_root)
    inputs_model, output_model = component_models(record)
    validated_input = _validate_input(
        inputs_model,
        payload,
        component_id=record.component_id,
    )
    expected_subject_hash = subject_hash(record)
    raw_result = _execute(record, validated_input)
    result = _validate_output(
        record,
        output_model,
        raw_result,
        expected_subject_hash=expected_subject_hash,
    )
    return _materialize(
        record,
        operation_request,
        validated_input,
        result,
        Path(operation_request.output_dir),
        expected_subject_hash=expected_subject_hash,
        overwrite=operation_request.overwrite,
    )


def _legacy_request(args: argparse.Namespace) -> OperationRequest:
    if args.component is None or args.input is None or args.output_dir is None:
        raise AdapterError(
            "invalid_adapter_arguments",
            "Legacy execution requires --component, --input, and --output-dir.",
        )
    record = component_record(args.component, root=args.catalog_root)
    source_kind = SourceKind.USER_PROMPT if args.input == "-" else SourceKind.USER_ATTACHMENT
    return OperationRequest(
        component_id=record.component_id,
        input=_read_payload(args.input),
        provenance=OperationProvenance(
            source_kind=source_kind,
            interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
            verification_status=VerificationStatus.UNVERIFIED,
            label=(
                "Structured component input from standard input"
                if args.input == "-"
                else f"Structured component input from {Path(args.input).name}"
            ),
        ),
        view=OperationViewRequest(
            use_case=args.view_use_case,
            supported_media_types=args.supported_media_type,
        ),
        output_dir=str(args.output_dir),
        catalog_root=str(args.catalog_root) if args.catalog_root is not None else None,
        overwrite=args.overwrite,
    )


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AdapterError):
        return exc.as_dict()
    if isinstance(exc, DQError):
        return exc.as_dict()
    if isinstance(exc, json.JSONDecodeError):
        return {
            "code": "invalid_json",
            "message": exc.msg,
            "details": {"line": exc.lineno, "column": exc.colno},
        }
    return {
        "code": "adapter_request_failed",
        "message": str(exc),
        "details": {"type": type(exc).__name__},
    }


def main() -> int:
    args = _parser().parse_args()
    request_mode = args.request is not None
    try:
        request = (
            _read_operation_request(args.request)
            if request_mode
            else _legacy_request(args)
        )
        manifest = _run(
            request.component_id,
            request.input,
            request,
        )
    except (AdapterError, DQError, json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        error = _error(exc)
        response = (
            OperationFailure(error=OperationError.model_validate(error)).model_dump(
                mode="json",
                exclude_none=True,
            )
            if request_mode
            else {"error": error}
        )
        print(json.dumps(response, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    response = (
        OperationSuccess(manifest=manifest).model_dump(mode="json", exclude_none=True)
        if request_mode
        else manifest.model_dump(mode="json", exclude_none=True)
    )
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
