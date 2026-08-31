"""Strict, transport-neutral normalization for session-scoped dataset records."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, RLock
from typing import Any, cast

from defined_quant.data_records import (
    MAX_DATASET_ROWS,
    DatasetPayloadV1,
    DatasetRecordV1,
    DatasetRegistrationRequest,
    InlineJsonSource,
    InlineRowsSource,
    LocalCsvSource,
    LocalJsonSource,
    cas_json_bytes,
    strict_json_loads,
)
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.local_host_platform import (
    PinnedRootHandle,
    SecureFilesystem,
    SecureFilesystemError,
    SecureFilesystemErrorCode,
    local_host_platform,
)
from defined_quant_protocol import canonical_json_bytes
from pydantic import ValidationError

MAX_CONFIGURED_ROOTS = 8
MAX_INLINE_JSON_BYTES = 512 * 1024
MAX_LOCAL_FILE_BYTES = 64 * 1024 * 1024
MAX_NORMALIZED_PAYLOAD_BYTES = 512 * 1024
MAX_DATASET_COLUMNS = 128
MAX_DECODED_CELL_BYTES = 64 * 1024

_TIMESTAMP = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,6})?"
    r"(?P<zone>Z|[+-]\d{2}:\d{2})$"
)
_JSON_INTEGER = re.compile(r"^-?(?:0|[1-9]\d*)$")
_JSON_NUMBER = re.compile(
    r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$"
)
_ARCHIVE_SUFFIXES = {
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
}
_CSV_LIMIT_LOCK = Lock()


class DatasetRegistryError(HostFailureException):
    """A safe, closed dataset-registration failure before host-envelope projection."""

    def __init__(
        self,
        code: HostFailureCode | str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        closed_code = HostFailureCode(code)
        safe_details = dict(details or {})
        super().__init__(closed_code, details=safe_details)
        self.details = safe_details


@dataclass(frozen=True, slots=True)
class NormalizedDataset:
    """The two immutable roots produced by one accepted source."""

    payload: DatasetPayloadV1
    record: DatasetRecordV1


def _limit_error(name: str, maximum: int, actual: int) -> DatasetRegistryError:
    return DatasetRegistryError(
        "input_limit_exceeded",
        details={"limit_name": name, "maximum": maximum, "actual": actual},
    )


def _invalid_dataset(*codes: str) -> DatasetRegistryError:
    safe = tuple(dict.fromkeys(code for code in codes if code))[:32]
    return DatasetRegistryError(
        "invalid_dataset",
        details={"finding_codes": list(safe or ("invalid_dataset_structure",))},
    )


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _invalid_dataset("invalid_unicode") from exc


def _validate_caller_text(request: DatasetRegistrationRequest) -> None:
    provenance = request.provenance
    if _utf8_size(provenance.label) > 240:
        raise _invalid_dataset("provenance_text_too_large")
    for value in (*provenance.references, *provenance.assumptions):
        if _utf8_size(value) > 500:
            raise _invalid_dataset("provenance_text_too_large")


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _decode_json(content: bytes) -> Any:
    if content.startswith(b"\xef\xbb\xbf"):
        raise _invalid_dataset("utf8_bom_not_allowed")
    try:
        return strict_json_loads(content)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise _invalid_dataset("invalid_json_source") from exc


def _canonical_timestamp(value: str) -> str:
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise _invalid_dataset("invalid_timestamp")
    if match.group("time").startswith("24:") or match.group("time").endswith(":60"):
        raise _invalid_dataset("invalid_timestamp")
    zone = match.group("zone")
    if zone != "Z":
        hours = int(zone[1:3])
        minutes = int(zone[4:6])
        if hours > 23 or minutes > 59:
            raise _invalid_dataset("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp has no UTC offset")
        utc = parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise _invalid_dataset("invalid_timestamp") from exc
    base = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        base += f".{utc.microsecond:06d}".rstrip("0")
    return base + "Z"


def _validate_cell_size(value: Any) -> None:
    if isinstance(value, str):
        size = _utf8_size(value)
        if size > MAX_DECODED_CELL_BYTES:
            raise _limit_error("decoded_cell_bytes", MAX_DECODED_CELL_BYTES, size)


def _json_cell(value: Any, data_type: str) -> tuple[Any, bool]:
    if value is None:
        return None, False
    if data_type == "boolean":
        if type(value) is not bool:
            raise _invalid_dataset("invalid_boolean_cell")
        return value, False
    if data_type == "integer":
        if type(value) is not int or abs(value) > 9_007_199_254_740_991:
            raise _invalid_dataset("invalid_integer_cell")
        return value, False
    if data_type == "number":
        if type(value) not in {int, float}:
            raise _invalid_dataset("invalid_number_cell")
        if not math.isfinite(value) or (
            isinstance(value, int) and abs(value) > 9_007_199_254_740_991
        ):
            raise _invalid_dataset("invalid_number_cell")
        try:
            canonical_json_bytes(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise _invalid_dataset("invalid_number_cell") from exc
        return value, False
    if data_type == "string":
        if not isinstance(value, str):
            raise _invalid_dataset("invalid_string_cell")
        _validate_cell_size(value)
        return value, False
    if data_type == "timestamp":
        if not isinstance(value, str):
            raise _invalid_dataset("invalid_timestamp")
        _validate_cell_size(value)
        canonical = _canonical_timestamp(value)
        return canonical, canonical != value
    raise _invalid_dataset("invalid_column_type")


def _csv_cell(value: str, data_type: str) -> tuple[Any, str | None, bool]:
    _validate_cell_size(value)
    if value == "" and data_type != "string":
        return None, "csv_empty_field_to_null", False
    if data_type == "string":
        return value, None, False
    if data_type == "boolean":
        if value not in {"true", "false"}:
            raise _invalid_dataset("invalid_boolean_cell")
        return value == "true", "csv_boolean_parsed", False
    if data_type == "integer":
        if _JSON_INTEGER.fullmatch(value) is None:
            raise _invalid_dataset("invalid_integer_cell")
        parsed_integer = int(value)
        if abs(parsed_integer) > 9_007_199_254_740_991:
            raise _invalid_dataset("invalid_integer_cell")
        return parsed_integer, "csv_integer_parsed", False
    if data_type == "number":
        if _JSON_NUMBER.fullmatch(value) is None:
            raise _invalid_dataset("invalid_number_cell")
        parsed_number: int | float
        parsed_number = int(value) if _JSON_INTEGER.fullmatch(value) else float(value)
        if not math.isfinite(parsed_number) or (
            isinstance(parsed_number, int)
            and abs(parsed_number) > 9_007_199_254_740_991
        ):
            raise _invalid_dataset("invalid_number_cell")
        try:
            canonical_json_bytes(parsed_number)
        except (TypeError, ValueError, OverflowError) as exc:
            raise _invalid_dataset("invalid_number_cell") from exc
        return parsed_number, "csv_number_parsed", False
    if data_type == "timestamp":
        canonical = _canonical_timestamp(value)
        return canonical, None, canonical != value
    raise _invalid_dataset("invalid_column_type")


def _lexical_path_parts(path: Path) -> tuple[str, ...]:
    value = os.fspath(path)
    if any(token in value for token in ("~", "$", "*", "?", "[", "]")):
        raise DatasetRegistryError("unsafe_input_path")
    if not path.is_absolute() or ".." in path.parts:
        raise DatasetRegistryError("unsafe_input_path")
    if path.suffix.casefold() in _ARCHIVE_SUFFIXES:
        raise DatasetRegistryError("unsupported_data_format")
    return tuple(part for part in path.parts if part not in {path.anchor, os.sep})


class ConfiguredFileRoots:
    """Fail-closed configured-root reader that never records a caller path."""

    __slots__ = ("_closed", "_lock", "_roots", "_secure_filesystem")

    @dataclass(frozen=True, slots=True)
    class _PinnedRoot:
        path: Path
        handle: PinnedRootHandle

    def __init__(self, roots: Sequence[Path] = ()) -> None:
        self._lock = RLock()
        self._closed = False
        self._roots: tuple[ConfiguredFileRoots._PinnedRoot, ...] = ()
        self._secure_filesystem: SecureFilesystem | None = None
        if len(roots) > MAX_CONFIGURED_ROOTS:
            raise DatasetRegistryError("input_root_denied")
        if roots:
            try:
                self._secure_filesystem = local_host_platform().secure_filesystem
            except SecureFilesystemError as exc:
                raise DatasetRegistryError("input_root_denied") from exc
        validated: list[ConfiguredFileRoots._PinnedRoot] = []
        try:
            for raw_root in roots:
                root = Path(raw_root)
                _lexical_path_parts(root)
                secure_filesystem = self._secure_filesystem
                if secure_filesystem is None:
                    raise DatasetRegistryError("input_root_denied")
                handle = secure_filesystem.pin_configured_root(root)
                validated.append(self._PinnedRoot(path=root, handle=handle))
        except DatasetRegistryError:
            for pinned in validated:
                if self._secure_filesystem is not None:
                    self._secure_filesystem.close_pinned_root(pinned.handle)
            raise
        except SecureFilesystemError as exc:
            for pinned in validated:
                if self._secure_filesystem is not None:
                    self._secure_filesystem.close_pinned_root(pinned.handle)
            raise DatasetRegistryError("input_root_denied") from exc
        self._roots = tuple(validated)

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(root.path for root in self._roots)

    def _matching_root(
        self, path: Path
    ) -> tuple[ConfiguredFileRoots._PinnedRoot, tuple[str, ...]]:
        _lexical_path_parts(path)
        for root in self._roots:
            try:
                relative = path.relative_to(root.path)
            except ValueError:
                continue
            parts = relative.parts
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise DatasetRegistryError("unsafe_input_path")
            return root, parts
        if not self._roots:
            raise DatasetRegistryError("input_root_denied")
        raise DatasetRegistryError("input_root_denied")

    def read(self, path_value: str) -> bytes:
        path = Path(path_value)
        root, parts = self._matching_root(path)
        try:
            with self._lock:
                if self._closed or self._secure_filesystem is None:
                    raise DatasetRegistryError("input_root_denied")
                secure_filesystem = self._secure_filesystem
            return secure_filesystem.read_file_beneath(
                root.handle,
                parts,
                maximum_bytes=MAX_LOCAL_FILE_BYTES,
            )
        except SecureFilesystemError as exc:
            if exc.code is SecureFilesystemErrorCode.NOT_REGULAR_FILE:
                raise DatasetRegistryError("unsupported_data_format")
            if exc.code is SecureFilesystemErrorCode.BYTE_LIMIT_EXCEEDED:
                raise _limit_error(
                    "local_file_bytes",
                    MAX_LOCAL_FILE_BYTES,
                    exc.actual if exc.actual is not None else MAX_LOCAL_FILE_BYTES + 1,
                )
            raise DatasetRegistryError("unsafe_input_path") from exc

    def close(self) -> None:
        """Release pinned configured-root handles; repeated closes are harmless."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            for root in self._roots:
                if self._secure_filesystem is not None:
                    self._secure_filesystem.close_pinned_root(root.handle)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _source_rows(
    request: DatasetRegistrationRequest,
    roots: ConfiguredFileRoots,
) -> tuple[list[dict[str, Any]] | list[list[str]], str, str, dict[str, Any]]:
    source = request.source
    if isinstance(source, InlineRowsSource):
        raw_rows = [dict(row) for row in source.rows]
        try:
            raw_bytes = canonical_json_bytes(raw_rows)
        except (TypeError, ValueError, OverflowError) as exc:
            raise _invalid_dataset("invalid_inline_rows") from exc
        return raw_rows, "json", hashlib.sha256(raw_bytes).hexdigest(), {
            "kind": "inline_rows",
        }

    if isinstance(source, InlineJsonSource):
        raw_bytes = source.text.encode("utf-8")
        if len(raw_bytes) > MAX_INLINE_JSON_BYTES:
            raise _limit_error(
                "inline_json_source_bytes", MAX_INLINE_JSON_BYTES, len(raw_bytes)
            )
        decoded = _decode_json(raw_bytes)
        return decoded, "json", hashlib.sha256(raw_bytes).hexdigest(), {
            "kind": "inline_json",
        }

    if not isinstance(source, (LocalCsvSource, LocalJsonSource)):
        raise _invalid_dataset("unsupported_source_kind")
    raw_bytes = roots.read(source.path)
    if isinstance(source, LocalJsonSource):
        decoded = _decode_json(raw_bytes)
        return decoded, "json", hashlib.sha256(raw_bytes).hexdigest(), {
            "kind": "local_file",
            "format": "json",
        }
    if not isinstance(source, LocalCsvSource):
        raise DatasetRegistryError("unsupported_data_format")
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise _invalid_dataset("utf8_bom_not_allowed")
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
        with _CSV_LIMIT_LOCK:
            previous_field_limit = csv.field_size_limit()
            try:
                csv.field_size_limit(MAX_LOCAL_FILE_BYTES)
                reader = csv.reader(
                    io.StringIO(text, newline=""),
                    delimiter=source.csv.delimiter,
                    strict=True,
                )
                header = next(reader)
                values: list[list[str]] = []
                for row_number, row in enumerate(reader, start=1):
                    if row_number > MAX_DATASET_ROWS:
                        raise _limit_error(
                            "dataset_rows", MAX_DATASET_ROWS, row_number
                        )
                    values.append(row)
            finally:
                csv.field_size_limit(previous_field_limit)
    except StopIteration as exc:
        raise _invalid_dataset("empty_dataset") from exc
    except DatasetRegistryError:
        raise
    except (UnicodeDecodeError, csv.Error) as exc:
        raise _invalid_dataset("invalid_csv_source") from exc
    if not header or len(set(header)) != len(header):
        raise _invalid_dataset("invalid_csv_header")
    return [header, *values], "csv", hashlib.sha256(raw_bytes).hexdigest(), {
        "kind": "local_file",
        "format": "csv",
        "csv": {"delimiter": source.csv.delimiter, "header": True},
    }


def _normalized_rows(
    source_rows: list[dict[str, Any]] | list[list[str]],
    source_format: str,
    columns: Sequence[Any],
) -> tuple[list[list[Any]], tuple[dict[str, Any], ...]]:
    names = [column.source_name for column in columns]
    field_ids = [column.field_id for column in columns]
    if len(names) != len(set(names)) or len(field_ids) != len(set(field_ids)):
        raise _invalid_dataset("duplicate_column")
    events: Counter[tuple[str, str]] = Counter()
    normalized: list[list[Any]] = []

    if source_format == "csv":
        if not source_rows or not isinstance(source_rows[0], list):
            raise _invalid_dataset("invalid_csv_source")
        header = source_rows[0]
        if len(header) != len(names) or set(header) != set(names):
            raise _invalid_dataset("invalid_csv_header")
        positions = {name: index for index, name in enumerate(header)}
        data_rows = source_rows[1:]
        for raw in data_rows:
            if not isinstance(raw, list) or len(raw) != len(header):
                raise _invalid_dataset("invalid_csv_row_width")
            output: list[Any] = []
            for column in columns:
                cell, event, timestamp_changed = _csv_cell(
                    raw[positions[column.source_name]], _value(column.data_type)
                )
                output.append(cell)
                if event is not None:
                    events[(event, column.field_id)] += 1
                if timestamp_changed:
                    events[("timestamp_to_canonical_utc", column.field_id)] += 1
            normalized.append(output)
    else:
        if not isinstance(source_rows, list):
            raise _invalid_dataset("invalid_json_source")
        allowed = set(names)
        for object_row in cast(list[dict[str, Any]], source_rows):
            if not isinstance(object_row, dict) or any(
                not isinstance(key, str) for key in object_row
            ):
                raise _invalid_dataset("json_row_not_object")
            if set(object_row) - allowed:
                raise _invalid_dataset("undeclared_source_field")
            output = []
            for column in columns:
                if column.source_name not in object_row:
                    output.append(None)
                    events[("json_missing_field_to_null", column.field_id)] += 1
                    continue
                cell, timestamp_changed = _json_cell(
                    object_row[column.source_name], _value(column.data_type)
                )
                output.append(cell)
                if timestamp_changed:
                    events[("timestamp_to_canonical_utc", column.field_id)] += 1
            normalized.append(output)

    if not normalized:
        raise _invalid_dataset("empty_dataset")
    if len(normalized) > MAX_DATASET_ROWS:
        raise _limit_error("dataset_rows", MAX_DATASET_ROWS, len(normalized))
    ledger = tuple(
        {"code": code, "field_id": field_id, "count": count}
        for (code, field_id), count in sorted(
            events.items(), key=lambda item: (item[0][0].encode(), item[0][1].encode())
        )
    )
    return normalized, ledger


def _record_source(
    source_projection: dict[str, Any], request: DatasetRegistrationRequest
) -> dict[str, Any]:
    provenance = request.provenance.model_dump(mode="json")
    if provenance.get("content_sha256") is None:
        provenance.pop("content_sha256", None)
    value = dict(source_projection)
    value["provenance"] = provenance
    return value


def _preflight_request_limits(request: Mapping[str, Any]) -> None:
    if "host_schema_version" in request:
        version = request["host_schema_version"]
        if type(version) is not int or abs(version) > 9_007_199_254_740_991:
            raise HostFailureException(
                HostFailureCode.INVALID_TOOL_REQUEST,
                details={"fields": ["host_schema_version"]},
            )
        if version != 1:
            raise HostFailureException(
                HostFailureCode.UNSUPPORTED_HOST_SCHEMA,
                details={"requested_version": version},
            )
    columns = request.get("columns")
    if isinstance(columns, Sequence) and not isinstance(columns, (str, bytes, bytearray)):
        if len(columns) > MAX_DATASET_COLUMNS:
            raise _limit_error("dataset_columns", MAX_DATASET_COLUMNS, len(columns))
    source = request.get("source")
    if not isinstance(source, Mapping):
        return
    if source.get("kind") == "inline_json":
        text = source.get("text")
        if isinstance(text, str):
            try:
                size = len(text.encode("utf-8"))
            except UnicodeEncodeError:
                return
            if size > MAX_INLINE_JSON_BYTES:
                raise _limit_error(
                    "inline_json_source_bytes", MAX_INLINE_JSON_BYTES, size
                )
    if source.get("kind") == "inline_rows":
        rows = source.get("rows")
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
            if len(rows) > MAX_DATASET_ROWS:
                raise _limit_error("dataset_rows", MAX_DATASET_ROWS, len(rows))


def normalize_dataset(
    request: DatasetRegistrationRequest | Mapping[str, Any],
    *,
    configured_roots: ConfiguredFileRoots | Sequence[Path] = (),
) -> NormalizedDataset:
    """Normalize one strict source into the two frozen V1 content roots."""

    if isinstance(request, Mapping):
        _preflight_request_limits(request)
    try:
        request_value = (
            request.model_dump(mode="json", exclude_none=True)
            if isinstance(request, DatasetRegistrationRequest)
            else request
        )
        validated = DatasetRegistrationRequest.model_validate(request_value)
    except ValidationError as exc:
        raise HostFailureException(
            HostFailureCode.INVALID_TOOL_REQUEST,
            details={"fields": []},
        ) from exc
    _validate_caller_text(validated)
    if len(validated.columns) > MAX_DATASET_COLUMNS:
        raise _limit_error(
            "dataset_columns", MAX_DATASET_COLUMNS, len(validated.columns)
        )
    owns_roots = not isinstance(configured_roots, ConfiguredFileRoots)
    roots = (
        configured_roots
        if isinstance(configured_roots, ConfiguredFileRoots)
        else ConfiguredFileRoots(configured_roots)
    )
    try:
        source_rows, source_format, raw_digest, source_projection = _source_rows(
            validated, roots
        )
    finally:
        if owns_roots:
            roots.close()
    if (
        validated.provenance.content_sha256 is not None
        and validated.provenance.content_sha256 != raw_digest
    ):
        raise _invalid_dataset("source_digest_mismatch")
    normalized_rows, events = _normalized_rows(
        source_rows, source_format, validated.columns
    )

    payload = DatasetPayloadV1.model_validate(
        {
            "schema_version": 1,
            "serialization": "dq-table-v1",
            "columns": [
                {"field_id": column.field_id, "data_type": _value(column.data_type)}
                for column in validated.columns
            ],
            "rows": normalized_rows,
        }
    )
    payload_size = len(cas_json_bytes(payload.canonical_projection()))
    if payload_size > MAX_NORMALIZED_PAYLOAD_BYTES:
        raise _limit_error(
            "normalized_dataset_payload_bytes",
            MAX_NORMALIZED_PAYLOAD_BYTES,
            payload_size,
        )

    timestamp_columns = [
        (index, column)
        for index, column in enumerate(validated.columns)
        if _value(column.role) == "timestamp"
    ]
    if len(timestamp_columns) > 1:
        raise _invalid_dataset("multiple_timestamp_columns")
    bounds: dict[str, str] | None = None
    if timestamp_columns:
        index, timestamp_column = timestamp_columns[0]
        if _value(timestamp_column.data_type) != "timestamp":
            raise _invalid_dataset("invalid_timestamp_role")
        values = [row[index] for row in normalized_rows if row[index] is not None]
        if not values or any(not isinstance(item, str) for item in values):
            raise _invalid_dataset("missing_timestamp_observation")
        bounds = {"first": values[0], "last": values[-1]}

    try:
        record = DatasetRecordV1.model_validate(
            {
                "host_schema_version": 1,
                "record_kind": "dataset",
                "payload": {
                    "serialization": "dq-table-v1",
                    "sha256": payload.digest,
                },
                "raw_source_sha256": raw_digest,
                "source": _record_source(source_projection, validated),
                "external_preprocessing": validated.external_preprocessing.model_dump(
                    mode="json"
                ),
                "columns": [
                    {
                        "source_name": column.source_name,
                        "field_id": column.field_id,
                        "data_type": _value(column.data_type),
                        "role": _value(column.role),
                        "semantic_port": (
                            column.semantic_port.model_dump(mode="json")
                            if column.semantic_port is not None
                            else None
                        ),
                    }
                    for column in validated.columns
                ],
                "semantics": validated.semantics.model_dump(
                    mode="json", exclude_none=True
                ),
                "row_count": len(normalized_rows),
                "column_count": len(validated.columns),
                "observation_bounds": bounds,
                "normalization_events": list(events),
                "findings": [],
                "normalizer": {
                    "name": "defined_quant_mcp_dataset_normalizer",
                    "version": "1.0.0",
                },
            }
        )
        record.validate_payload(payload)
    except (ValidationError, ValueError) as exc:
        raise _invalid_dataset("invalid_normalized_dataset") from exc
    return NormalizedDataset(payload=payload, record=record)


def resolve_dataset_fields(
    payload: DatasetPayloadV1,
    mappings: Sequence[Any],
    *,
    literals: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve whole immutable columns to component fields without transformation."""

    source_names = {column.field_id: index for index, column in enumerate(payload.columns)}
    output = dict(literals)
    seen_source: set[str] = set()
    seen_target = set(output)
    invalid: list[str] = []
    for mapping in mappings:
        source_field = mapping.source_field
        input_field = mapping.input_field
        if (
            source_field in seen_source
            or input_field in seen_target
            or source_field not in source_names
        ):
            invalid.extend((source_field, input_field))
            continue
        seen_source.add(source_field)
        seen_target.add(input_field)
        index = source_names[source_field]
        output[input_field] = [row[index] for row in payload.rows]
    if invalid:
        raise DatasetRegistryError(
            "invalid_field_mapping", details={"fields": []}
        )
    return output


__all__ = [
    "ConfiguredFileRoots",
    "DatasetRegistryError",
    "MAX_CONFIGURED_ROOTS",
    "MAX_DATASET_COLUMNS",
    "MAX_DATASET_ROWS",
    "MAX_DECODED_CELL_BYTES",
    "MAX_INLINE_JSON_BYTES",
    "MAX_LOCAL_FILE_BYTES",
    "MAX_NORMALIZED_PAYLOAD_BYTES",
    "NormalizedDataset",
    "normalize_dataset",
    "resolve_dataset_fields",
]
