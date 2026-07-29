#!/usr/bin/env python3
"""Execute any installed Defined Quant component and materialize declared views."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from defined_quant import (
    __version__,
    component_models,
    component_record,
    load_component,
    save_svg,
    subject_hash,
    visualization_hash,
)
from defined_quant.catalog import ComponentRecord
from defined_quant.types import ComponentContractError, ComponentOutput, DQError
from pydantic import BaseModel, ValidationError

_ADAPTER_NAME = "use-defined-quant"
_ADAPTER_SCHEMA_VERSION = 1


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
    parser.add_argument("--component", required=True, help="Stable component ID or component path.")
    parser.add_argument("--input", required=True, help="Input JSON object path, or - for stdin.")
    parser.add_argument("--output-dir", required=True, type=Path)
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
    validated_input: BaseModel,
    result: ComponentOutput,
    output_dir_argument: Path,
    *,
    expected_subject_hash: str,
    overwrite: bool,
) -> dict[str, Any]:
    output_dir = output_dir_argument.expanduser().resolve()
    normalized_input_path = output_dir / "input.json"
    result_path = output_dir / "result.json"
    manifest_path = output_dir / "manifest.json"
    artifact_paths = [
        output_dir / f"{index:02d}-{_safe_stem(spec.id)}.svg"
        for index, spec in enumerate(result.visualizations, start=1)
    ]

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _assert_targets_available(
            [normalized_input_path, result_path, manifest_path, *artifact_paths],
            overwrite=overwrite,
        )

        input_data = validated_input.model_dump(mode="json")
        result_data = result.model_dump(mode="json")
        input_bytes = _json_bytes(input_data)
        result_bytes = _json_bytes(result_data)
        _write_bytes(normalized_input_path, input_bytes, overwrite=overwrite)
        _write_bytes(result_path, result_bytes, overwrite=overwrite)

        artifacts: list[dict[str, Any]] = []
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

        manifest = {
            "schema_version": _ADAPTER_SCHEMA_VERSION,
            "adapter": {"name": _ADAPTER_NAME, "version": _ADAPTER_SCHEMA_VERSION},
            "component": {
                "id": record.component_id,
                "title": record.metadata.get("title"),
                "version": record.version,
                "callable": record.callable_path,
                "subject_hash": expected_subject_hash,
            },
            "input": {
                "path": str(normalized_input_path),
                "sha256": hashlib.sha256(input_bytes).hexdigest(),
            },
            "result": {
                "path": str(result_path),
                "sha256": hashlib.sha256(result_bytes).hexdigest(),
            },
            "manifest_path": str(manifest_path),
            "artifacts": artifacts,
        }
        _write_bytes(manifest_path, _json_bytes(manifest), overwrite=overwrite)
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
    input_path: str,
    output_dir: Path,
    *,
    catalog_root: Path | None,
    overwrite: bool,
) -> dict[str, Any]:
    record = component_record(component_identifier, root=catalog_root)
    inputs_model, output_model = component_models(record)
    payload = _read_payload(input_path)
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
        validated_input,
        result,
        output_dir,
        expected_subject_hash=expected_subject_hash,
        overwrite=overwrite,
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
    try:
        manifest = _run(
            args.component,
            args.input,
            args.output_dir,
            catalog_root=args.catalog_root,
            overwrite=args.overwrite,
        )
    except (AdapterError, DQError, json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": _error(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
