"""Transport-neutral immutable dataset and operation record contracts.

The models in this module are host records, not MCP wire models.  They deliberately depend only
on the core protocol package and preserve the closed hash projections frozen for the local host.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias, cast

from defined_quant._immutable_json import freeze_json_object
from defined_quant_protocol import (
    CallerProvenance,
    ComponentRef,
    InterpretationMethod,
    PortCompatibility,
    PortDirection,
    ProtocolVersion,
    ProvenanceStatus,
    RelativeMemberPath,
    RunnerIdentity,
    SemanticPort,
    SourceKind,
    canonical_hash,
    canonical_json_bytes,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

DATASET_PAYLOAD_HASH_DOMAIN = "mcp.dataset.payload.v1"
DATASET_RECORD_HASH_DOMAIN = "mcp.dataset.v1"
OPERATION_RECORD_HASH_DOMAIN = "mcp.operation.v1"

DATASET_PAYLOAD_SERIALIZATION: Literal["dq-table-v1"] = "dq-table-v1"
CAS_SERIALIZATION = "defined-quant-cas-json-v1"
PRETTY_JSON_SERIALIZATION = "defined-quant-pretty-json-v1"

MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_DATASET_ROWS = 250_000
MAX_DATASET_COLUMNS = 128
MAX_CELL_BYTES = 64 * 1024
MAX_INLINE_JSON_BYTES = 512 * 1024
MAX_LITERAL_FIELDS = 64
MAX_LITERAL_BYTES = 64 * 1024
MAX_LITERALS_BYTES = 512 * 1024
MAX_OPERATION_MEMBER_BYTES = 128 * 1024 * 1024
MAX_OPERATION_ARTIFACTS = 128
MAX_RECORD_BYTES = 2 * 1024 * 1024

_SAFE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_DATASET_REFERENCE_PATTERN = re.compile(r"^dqds:v([0-9]{1,3}):([0-9a-f]{64})$")
_OPERATION_REFERENCE_PATTERN = re.compile(r"^dqop:v([0-9]{1,3}):([0-9a-f]{64})$")
_CANONICAL_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{0,5}[1-9])?Z$"
)

SafeId: TypeAlias = Annotated[str, Field(pattern=_SAFE_ID_PATTERN.pattern)]
Sha256: TypeAlias = Annotated[str, Field(pattern=_SHA256_PATTERN.pattern)]
StrictCount: TypeAlias = Annotated[int, Field(strict=True, ge=0)]


def _utf8_bytes(value: str, *, name: str, minimum: int = 0, maximum: int) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} cannot contain lone Unicode surrogates") from exc
    if not minimum <= len(encoded) <= maximum:
        raise ValueError(f"{name} must contain {minimum}..{maximum} UTF-8 bytes")
    return encoded


def _validate_semver(value: str) -> str:
    _utf8_bytes(value, name="version", minimum=1, maximum=128)
    if _SEMVER_PATTERN.fullmatch(value) is None:
        raise ValueError("version must be a canonical semantic version")
    return value


def _validate_sha256(value: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("digest must contain 64 lowercase hexadecimal characters")
    return value


def _require_integer_one(value: Any, *, name: str) -> Any:
    if type(value) is not int or value != 1:
        raise ValueError(f"{name} must be the integer 1")
    return value


def _json_projection(value: Any) -> Any:
    projection = getattr(value, "canonical_projection", None)
    if callable(projection):
        return projection()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def pretty_json_bytes(value: Any) -> bytes:
    """Serialize a Phase-1 portable member exactly, including its final LF."""

    projected = _json_projection(value)
    canonical_json_bytes(projected)
    return (
        json.dumps(
            projected,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def cas_json_bytes(value: Any) -> bytes:
    """Serialize one immutable CAS document using ``defined-quant-cas-json-v1``."""

    projected = _json_projection(value)
    canonical_json_bytes(projected)
    return (
        json.dumps(
            projected,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object keys must be unique")
        result[key] = value
    return result


def _reject_non_finite(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token!r} is not permitted")


def strict_json_loads(content: bytes, *, maximum_bytes: int | None = None) -> JsonValue:
    """Decode strict UTF-8 JSON, rejecting duplicate keys and non-canonical values."""

    if maximum_bytes is not None and len(content) > maximum_bytes:
        raise ValueError("JSON document exceeds its byte limit")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("JSON document must be strict UTF-8") from exc
    if text.startswith("\ufeff"):
        raise ValueError("JSON document must not contain a byte-order mark")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("JSON document is malformed") from exc
    canonical_json_bytes(value)
    return cast(JsonValue, value)


def strict_cas_json_loads(content: bytes, *, maximum_bytes: int = MAX_RECORD_BYTES) -> JsonValue:
    """Decode and verify the one accepted byte serialization for a CAS document."""

    value = strict_json_loads(content, maximum_bytes=maximum_bytes)
    if cas_json_bytes(value) != content:
        raise ValueError("CAS document does not use defined-quant-cas-json-v1")
    return value


@dataclass(frozen=True, slots=True)
class ParsedReference:
    """Syntactically valid content reference, before supported-version policy."""

    kind: Literal["dataset", "operation"]
    version_token: str
    digest: str

    @property
    def version(self) -> int:
        return int(self.version_token)


def _parse_reference(
    value: str,
    *,
    kind: Literal["dataset", "operation"],
) -> ParsedReference:
    pattern = _DATASET_REFERENCE_PATTERN if kind == "dataset" else _OPERATION_REFERENCE_PATTERN
    match = pattern.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid {kind} reference")
    return ParsedReference(kind=kind, version_token=match.group(1), digest=match.group(2))


def parse_dataset_reference(value: str) -> ParsedReference:
    return _parse_reference(value, kind="dataset")


def parse_operation_reference(value: str) -> ParsedReference:
    return _parse_reference(value, kind="operation")


def dataset_reference(digest: str) -> str:
    return f"dqds:v1:{_validate_sha256(digest)}"


def operation_reference(digest: str) -> str:
    return f"dqop:v1:{_validate_sha256(digest)}"


class _ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetProvenance(_ClosedModel):
    source_kind: SourceKind
    interpretation_method: InterpretationMethod
    verification_status: ProvenanceStatus = ProvenanceStatus.UNVERIFIED
    label: str
    references: tuple[str, ...] = Field(default=(), max_length=50)
    content_sha256: Sha256 | None = None
    assumptions: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator("content_sha256", mode="before")
    @classmethod
    def _content_digest_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("optional content_sha256 must be omitted rather than null")
        return value

    @field_validator("label")
    @classmethod
    def _label_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="provenance label", minimum=1, maximum=240)
        return value

    @field_validator("references", "assumptions")
    @classmethod
    def _provenance_text_bytes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _utf8_bytes(value, name="provenance text", minimum=1, maximum=500)
        return values

    def canonical_projection(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        if self.content_sha256 is None:
            value.pop("content_sha256")
        return value


class NoExternalPreprocessing(_ClosedModel):
    status: Literal["none_declared"]


class UnknownExternalPreprocessing(_ClosedModel):
    status: Literal["unknown"]


class ExternalReceipt(_ClosedModel):
    schema_id: SafeId
    sha256: Sha256


class ReceiptExternalPreprocessing(_ClosedModel):
    status: Literal["receipt_supplied"]
    receipt: ExternalReceipt


ExternalPreprocessing: TypeAlias = (
    NoExternalPreprocessing | UnknownExternalPreprocessing | ReceiptExternalPreprocessing
)


class CsvSourceOptions(_ClosedModel):
    delimiter: Literal[",", ";", "\t"] = ","
    header: Literal[True] = True

    @field_validator("header", mode="before")
    @classmethod
    def _strict_true(cls, value: Any) -> Any:
        if type(value) is not bool or value is not True:
            raise ValueError("CSV header must be the boolean true")
        return value


class CsvRecordSourceOptions(_ClosedModel):
    delimiter: Literal[",", ";", "\t"]
    header: Literal[True]

    @field_validator("header", mode="before")
    @classmethod
    def _strict_true(cls, value: Any) -> Any:
        if type(value) is not bool or value is not True:
            raise ValueError("CSV header must be the boolean true")
        return value


class InlineRowsSource(_ClosedModel):
    kind: Literal["inline_rows"]
    rows: tuple[dict[str, JsonValue], ...] = Field(min_length=1, max_length=MAX_DATASET_ROWS)

    @model_validator(mode="after")
    def _validate_rows(self) -> InlineRowsSource:
        for row in self.rows:
            for key in row:
                _utf8_bytes(key, name="source field name", minimum=1, maximum=240)
            canonical_json_bytes(row)
        return self


class InlineJsonSource(_ClosedModel):
    kind: Literal["inline_json"]
    text: str

    @field_validator("text")
    @classmethod
    def _text_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="inline JSON", minimum=1, maximum=MAX_INLINE_JSON_BYTES)
        return value


class LocalCsvSource(_ClosedModel):
    kind: Literal["local_file"]
    path: str
    format: Literal["csv"]
    csv: CsvSourceOptions = Field(default_factory=CsvSourceOptions)

    @field_validator("path")
    @classmethod
    def _path_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="local file path", minimum=1, maximum=4096)
        return value


class LocalJsonSource(_ClosedModel):
    kind: Literal["local_file"]
    path: str
    format: Literal["json"]

    @field_validator("path")
    @classmethod
    def _path_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="local file path", minimum=1, maximum=4096)
        return value


DatasetSource: TypeAlias = InlineRowsSource | InlineJsonSource | LocalCsvSource | LocalJsonSource


class DatasetInstrument(_ClosedModel):
    namespace: str
    symbol: str

    @field_validator("namespace")
    @classmethod
    def _namespace_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="instrument namespace", minimum=1, maximum=64)
        return value

    @field_validator("symbol")
    @classmethod
    def _symbol_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="instrument symbol", minimum=1, maximum=128)
        return value


class DatasetSemantics(_ClosedModel):
    instrument: DatasetInstrument | None = None
    currency: str | None = None
    frequency: Literal[
        "intraday", "daily", "weekly", "monthly", "quarterly", "annual", "irregular"
    ] | None = None
    timezone: str | None = None
    ordering: Literal["preserve_source_order"]
    price_kind: Literal["adjusted", "unadjusted"] | None = None
    adjustment_policy: str | None = None
    distribution_policy: str | None = None
    calendar: str | None = None
    partial_period_policy: str | None = None

    @field_validator(
        "instrument",
        "currency",
        "frequency",
        "timezone",
        "price_kind",
        "adjustment_policy",
        "distribution_policy",
        "calendar",
        "partial_period_policy",
        mode="before",
    )
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("optional dataset semantics must be omitted rather than null")
        return value

    @field_validator("currency")
    @classmethod
    def _currency_bytes(cls, value: str | None) -> str | None:
        if value is not None:
            _utf8_bytes(value, name="currency", minimum=1, maximum=32)
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone_bytes(cls, value: str | None) -> str | None:
        if value is not None:
            _utf8_bytes(value, name="timezone", minimum=1, maximum=128)
        return value

    @field_validator(
        "adjustment_policy", "distribution_policy", "calendar", "partial_period_policy"
    )
    @classmethod
    def _policy_bytes(cls, value: str | None) -> str | None:
        if value is not None:
            _utf8_bytes(value, name="semantic policy", minimum=1, maximum=240)
        return value

    def canonical_projection(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class DatasetColumn(_ClosedModel):
    source_name: str
    field_id: SafeId
    data_type: Literal["number", "integer", "string", "boolean", "timestamp"]
    role: Literal["value", "timestamp", "instrument", "label", "other"]
    semantic_port: SemanticPort | None = None

    @field_validator("source_name")
    @classmethod
    def _source_name_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="source field name", minimum=1, maximum=240)
        return value

    @field_validator("semantic_port", mode="before")
    @classmethod
    def _semantic_port_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("optional semantic_port must be omitted rather than null")
        return value

    @model_validator(mode="after")
    def _validate_column(self) -> DatasetColumn:
        if self.role == "timestamp" and self.data_type != "timestamp":
            raise ValueError("a timestamp-role column must use timestamp data")
        if self.semantic_port is not None and self.semantic_port.direction != PortDirection.OUTPUT:
            raise ValueError("dataset semantic ports must have output direction")
        return self


class DatasetRegistrationRequest(_ClosedModel):
    host_schema_version: Literal[1] = 1
    source: DatasetSource
    columns: tuple[DatasetColumn, ...] = Field(min_length=1, max_length=MAX_DATASET_COLUMNS)
    semantics: DatasetSemantics
    provenance: DatasetProvenance
    external_preprocessing: ExternalPreprocessing = Field(
        default_factory=lambda: UnknownExternalPreprocessing(status="unknown")
    )

    @field_validator("host_schema_version", mode="before")
    @classmethod
    def _strict_host_schema_version(cls, value: Any) -> Any:
        return _require_integer_one(value, name="host_schema_version")

    @model_validator(mode="after")
    def _validate_columns(self) -> DatasetRegistrationRequest:
        field_ids = [column.field_id for column in self.columns]
        source_names = [column.source_name for column in self.columns]
        if len(set(field_ids)) != len(field_ids):
            raise ValueError("dataset field IDs must be unique")
        if len(set(source_names)) != len(source_names):
            raise ValueError("dataset source names must be unique")
        if sum(column.role == "timestamp" for column in self.columns) > 1:
            raise ValueError("a dataset may have at most one timestamp-role column")
        return self


class DatasetPayloadColumnV1(_ClosedModel):
    field_id: SafeId
    data_type: Literal["number", "integer", "string", "boolean", "timestamp"]


def _validate_canonical_timestamp(value: str) -> None:
    if _CANONICAL_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp is not in canonical UTC form")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp is not a valid Gregorian UTC instant") from exc


def _validate_cell(value: JsonValue, *, data_type: str) -> None:
    if value is None:
        return
    if isinstance(value, (list, dict)):
        raise ValueError("dataset payload cells must be scalar")
    if isinstance(value, str):
        _utf8_bytes(value, name="dataset cell", maximum=MAX_CELL_BYTES)
    if data_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("boolean column contains a non-boolean cell")
    elif data_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("integer column contains a non-integer cell")
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("integer cell exceeds the canonical safe range")
    elif data_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number column contains a non-number cell")
        if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("number cell exceeds the canonical safe range")
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("number cell must be finite")
            if value.is_integer() and abs(value) > MAX_SAFE_INTEGER:
                raise ValueError("integer-valued number cell exceeds the canonical safe range")
    elif data_type == "string":
        if not isinstance(value, str):
            raise ValueError("string column contains a non-string cell")
    elif data_type == "timestamp":
        if not isinstance(value, str):
            raise ValueError("timestamp column contains a non-string cell")
        _validate_canonical_timestamp(value)
    else:  # pragma: no cover - closed Literal makes this unreachable
        raise ValueError("unknown dataset payload type")


class DatasetPayloadV1(_ClosedModel):
    schema_version: Literal[1]
    serialization: Literal["dq-table-v1"]
    columns: tuple[DatasetPayloadColumnV1, ...] = Field(
        min_length=1, max_length=MAX_DATASET_COLUMNS
    )
    rows: tuple[tuple[JsonValue, ...], ...] = Field(min_length=1, max_length=MAX_DATASET_ROWS)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _strict_schema_version(cls, value: Any) -> Any:
        return _require_integer_one(value, name="schema_version")

    @model_validator(mode="after")
    def _validate_table(self) -> DatasetPayloadV1:
        field_ids = [column.field_id for column in self.columns]
        if len(set(field_ids)) != len(field_ids):
            raise ValueError("payload field IDs must be unique")
        width = len(self.columns)
        for row in self.rows:
            if len(row) != width:
                raise ValueError("every payload row must have the declared width")
            for value, column in zip(row, self.columns, strict=True):
                _validate_cell(value, data_type=column.data_type)
        canonical_json_bytes(self.canonical_projection())
        return self

    def canonical_projection(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def digest(self) -> str:
        return canonical_hash(
            self.canonical_projection(), domain=DATASET_PAYLOAD_HASH_DOMAIN
        )


class DatasetPayloadBindingV1(_ClosedModel):
    serialization: Literal["dq-table-v1"]
    sha256: Sha256


class DatasetRecordProvenance(DatasetProvenance):
    verification_status: ProvenanceStatus
    references: tuple[str, ...] = Field(max_length=50)
    assumptions: tuple[str, ...] = Field(max_length=50)


class InlineRowsRecordSourceV1(_ClosedModel):
    kind: Literal["inline_rows"]
    provenance: DatasetRecordProvenance


class InlineJsonRecordSourceV1(_ClosedModel):
    kind: Literal["inline_json"]
    provenance: DatasetRecordProvenance


class LocalCsvRecordSourceV1(_ClosedModel):
    kind: Literal["local_file"]
    format: Literal["csv"]
    csv: CsvRecordSourceOptions
    provenance: DatasetRecordProvenance


class LocalJsonRecordSourceV1(_ClosedModel):
    kind: Literal["local_file"]
    format: Literal["json"]
    provenance: DatasetRecordProvenance


DatasetRecordSourceV1: TypeAlias = (
    InlineRowsRecordSourceV1
    | InlineJsonRecordSourceV1
    | LocalCsvRecordSourceV1
    | LocalJsonRecordSourceV1
)


class DatasetRecordColumnV1(_ClosedModel):
    source_name: str
    field_id: SafeId
    data_type: Literal["number", "integer", "string", "boolean", "timestamp"]
    role: Literal["value", "timestamp", "instrument", "label", "other"]
    semantic_port: SemanticPort | None

    @field_validator("source_name")
    @classmethod
    def _source_name_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="source field name", minimum=1, maximum=240)
        return value

    @model_validator(mode="after")
    def _validate_column(self) -> DatasetRecordColumnV1:
        if self.role == "timestamp" and self.data_type != "timestamp":
            raise ValueError("a timestamp-role column must use timestamp data")
        if self.semantic_port is not None and self.semantic_port.direction != PortDirection.OUTPUT:
            raise ValueError("dataset semantic ports must have output direction")
        return self

    def canonical_projection(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["semantic_port"] = (
            None if self.semantic_port is None else self.semantic_port.model_dump(mode="json")
        )
        return value


class ObservationBoundsV1(_ClosedModel):
    first: str
    last: str

    @field_validator("first", "last")
    @classmethod
    def _canonical_timestamp(cls, value: str) -> str:
        _validate_canonical_timestamp(value)
        return value


class NormalizationEventCode(StrEnum):
    JSON_MISSING_FIELD_TO_NULL = "json_missing_field_to_null"
    CSV_EMPTY_FIELD_TO_NULL = "csv_empty_field_to_null"
    CSV_NUMBER_PARSED = "csv_number_parsed"
    CSV_INTEGER_PARSED = "csv_integer_parsed"
    CSV_BOOLEAN_PARSED = "csv_boolean_parsed"
    TIMESTAMP_TO_CANONICAL_UTC = "timestamp_to_canonical_utc"


class NormalizationEventV1(_ClosedModel):
    code: NormalizationEventCode
    field_id: SafeId
    count: Annotated[int, Field(strict=True, ge=1, le=MAX_DATASET_ROWS)]


class DatasetFindingV1(_ClosedModel):
    code: SafeId
    severity: Literal["warning"]
    message: str
    count: Annotated[int, Field(strict=True, ge=1)]

    @field_validator("message")
    @classmethod
    def _message_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="finding message", minimum=1, maximum=500)
        return value


def _record_source_projection(source: DatasetRecordSourceV1) -> dict[str, Any]:
    value = source.model_dump(mode="json")
    value["provenance"] = source.provenance.canonical_projection()
    return value


class DatasetRecordV1(_ClosedModel):
    host_schema_version: Literal[1]
    record_kind: Literal["dataset"]
    payload: DatasetPayloadBindingV1
    raw_source_sha256: Sha256
    source: DatasetRecordSourceV1
    external_preprocessing: ExternalPreprocessing
    columns: tuple[DatasetRecordColumnV1, ...] = Field(
        min_length=1, max_length=MAX_DATASET_COLUMNS
    )
    semantics: DatasetSemantics
    row_count: Annotated[int, Field(strict=True, ge=1, le=MAX_DATASET_ROWS)]
    column_count: Annotated[int, Field(strict=True, ge=1, le=MAX_DATASET_COLUMNS)]
    observation_bounds: ObservationBoundsV1 | None
    normalization_events: tuple[NormalizationEventV1, ...] = Field(max_length=768)
    findings: tuple[DatasetFindingV1, ...] = Field(max_length=50)
    normalizer: RunnerIdentity

    @field_validator("host_schema_version", mode="before")
    @classmethod
    def _strict_host_schema_version(cls, value: Any) -> Any:
        return _require_integer_one(value, name="host_schema_version")

    @model_validator(mode="after")
    def _validate_record(self) -> DatasetRecordV1:
        if self.column_count != len(self.columns):
            raise ValueError("column_count does not match dataset columns")
        field_ids = [column.field_id for column in self.columns]
        source_names = [column.source_name for column in self.columns]
        if len(set(field_ids)) != len(field_ids) or len(set(source_names)) != len(source_names):
            raise ValueError("dataset record column identifiers must be unique")
        timestamp_columns = [column for column in self.columns if column.role == "timestamp"]
        if len(timestamp_columns) > 1:
            raise ValueError("a dataset may have at most one timestamp-role column")
        if not timestamp_columns and self.observation_bounds is not None:
            raise ValueError("observation bounds require a timestamp-role column")

        event_keys = [(event.code.value, event.field_id) for event in self.normalization_events]
        if len(set(event_keys)) != len(event_keys):
            raise ValueError("normalization events must be unique by code and field ID")
        if event_keys != sorted(event_keys, key=lambda item: (item[0].encode(), item[1].encode())):
            raise ValueError("normalization events must use canonical UTF-8 ordering")
        by_field = {column.field_id: column for column in self.columns}
        is_csv = isinstance(self.source, LocalCsvRecordSourceV1)
        is_json = isinstance(
            self.source,
            (InlineRowsRecordSourceV1, InlineJsonRecordSourceV1, LocalJsonRecordSourceV1),
        )
        for event in self.normalization_events:
            column = by_field.get(event.field_id)
            if column is None or event.count > self.row_count:
                raise ValueError("normalization event does not match the dataset")
            if event.code == NormalizationEventCode.JSON_MISSING_FIELD_TO_NULL and not is_json:
                raise ValueError("JSON missing-field events require a JSON/object source")
            if event.code.value.startswith("csv_") and not is_csv:
                raise ValueError("CSV normalization events require a CSV source")
            expected_types = {
                NormalizationEventCode.CSV_NUMBER_PARSED: "number",
                NormalizationEventCode.CSV_INTEGER_PARSED: "integer",
                NormalizationEventCode.CSV_BOOLEAN_PARSED: "boolean",
            }
            expected = expected_types.get(event.code)
            if expected is not None and column.data_type != expected:
                raise ValueError("CSV parsing event does not match its column type")
            if (
                event.code == NormalizationEventCode.CSV_EMPTY_FIELD_TO_NULL
                and column.data_type == "string"
            ):
                raise ValueError("empty CSV strings are not converted to null")
            if (
                event.code == NormalizationEventCode.TIMESTAMP_TO_CANONICAL_UTC
                and column.data_type != "timestamp"
            ):
                raise ValueError("timestamp event requires a timestamp column")

        if self.findings:
            raise ValueError(
                "dataset findings require a separately frozen code and message vocabulary"
            )

        provenance = self.source.provenance
        if (
            provenance.content_sha256 is not None
            and provenance.content_sha256 != self.raw_source_sha256
        ):
            raise ValueError("provenance content digest does not match the accepted source")
        _validate_semver(self.normalizer.version)
        return self

    def canonical_projection(self) -> dict[str, Any]:
        return {
            "host_schema_version": 1,
            "record_kind": "dataset",
            "payload": self.payload.model_dump(mode="json"),
            "raw_source_sha256": self.raw_source_sha256,
            "source": _record_source_projection(self.source),
            "external_preprocessing": self.external_preprocessing.model_dump(mode="json"),
            "columns": [column.canonical_projection() for column in self.columns],
            "semantics": self.semantics.canonical_projection(),
            "row_count": self.row_count,
            "column_count": self.column_count,
            "observation_bounds": (
                None
                if self.observation_bounds is None
                else self.observation_bounds.model_dump(mode="json")
            ),
            "normalization_events": [
                event.model_dump(mode="json") for event in self.normalization_events
            ],
            "findings": [finding.model_dump(mode="json") for finding in self.findings],
            "normalizer": self.normalizer.model_dump(mode="json"),
        }

    @property
    def digest(self) -> str:
        return canonical_hash(
            self.canonical_projection(), domain=DATASET_RECORD_HASH_DOMAIN
        )

    @property
    def reference(self) -> str:
        return dataset_reference(self.digest)

    def validate_payload(self, payload: DatasetPayloadV1) -> DatasetRecordV1:
        if payload.digest != self.payload.sha256:
            raise ValueError("dataset payload digest does not match its record")
        if len(payload.rows) != self.row_count or len(payload.columns) != self.column_count:
            raise ValueError("dataset payload dimensions do not match its record")
        expected_columns = [(column.field_id, column.data_type) for column in self.columns]
        payload_columns = [(column.field_id, column.data_type) for column in payload.columns]
        if payload_columns != expected_columns:
            raise ValueError("dataset payload columns do not match its record")
        timestamp_indexes = [
            index for index, column in enumerate(self.columns) if column.role == "timestamp"
        ]
        if timestamp_indexes:
            values = [
                row[timestamp_indexes[0]]
                for row in payload.rows
                if row[timestamp_indexes[0]] is not None
            ]
            if not values or not all(isinstance(value, str) for value in values):
                raise ValueError("timestamp-role column has no valid observations")
            string_values = cast(list[str], values)
            expected_bounds = ObservationBoundsV1(
                first=string_values[0], last=string_values[-1]
            )
            if self.observation_bounds != expected_bounds:
                raise ValueError("dataset observation bounds do not match its payload")
        elif self.observation_bounds is not None:
            raise ValueError("dataset without timestamp role cannot have observation bounds")
        return self


class OperationMemberV1(_ClosedModel):
    kind: Literal["normalized_input", "result", "artifact"]
    path: RelativeMemberPath
    sha256: Sha256
    size_bytes: Annotated[int, Field(strict=True, ge=0, le=MAX_OPERATION_MEMBER_BYTES)]


class FieldMappingV1(_ClosedModel):
    source_field: str
    input_field: str

    @field_validator("source_field", "input_field")
    @classmethod
    def _field_bytes(cls, value: str) -> str:
        _utf8_bytes(value, name="field mapping name", minimum=1, maximum=240)
        return value


def _validate_mappings(mappings: Sequence[FieldMappingV1]) -> None:
    sources = [mapping.source_field for mapping in mappings]
    targets = [mapping.input_field for mapping in mappings]
    if len(set(sources)) != len(sources) or len(set(targets)) != len(targets):
        raise ValueError("source mappings must be unique by source and target field")


class DatasetOperationSourceV1(_ClosedModel):
    kind: Literal["dataset"]
    ref: str
    mappings: tuple[FieldMappingV1, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_source(self) -> DatasetOperationSourceV1:
        parsed = parse_dataset_reference(self.ref)
        if parsed.version_token != "1":
            raise ValueError("dataset operation binding requires a v1 reference")
        _validate_mappings(self.mappings)
        return self


class OperationOperationSourceV1(_ClosedModel):
    kind: Literal["operation"]
    ref: str
    mappings: tuple[FieldMappingV1, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_source(self) -> OperationOperationSourceV1:
        parsed = parse_operation_reference(self.ref)
        if parsed.version_token != "1":
            raise ValueError("operation binding requires a v1 reference")
        _validate_mappings(self.mappings)
        return self


OperationBindingSourceV1: TypeAlias = DatasetOperationSourceV1 | OperationOperationSourceV1


class OperationBindingV1(_ClosedModel):
    literals: dict[str, JsonValue]
    source: OperationBindingSourceV1 | None = None

    @field_validator("literals")
    @classmethod
    def _freeze_literals(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return freeze_json_object(value)

    @field_validator("source", mode="before")
    @classmethod
    def _source_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("optional operation source must be omitted rather than null")
        return value

    @model_validator(mode="after")
    def _validate_binding(self) -> OperationBindingV1:
        if len(self.literals) > MAX_LITERAL_FIELDS:
            raise ValueError("operation binding contains too many literal fields")
        for key, value in self.literals.items():
            _utf8_bytes(key, name="literal input field", minimum=1, maximum=240)
            if len(canonical_json_bytes(value)) > MAX_LITERAL_BYTES:
                raise ValueError("operation literal exceeds its canonical byte limit")
        if len(canonical_json_bytes(self.literals)) > MAX_LITERALS_BYTES:
            raise ValueError("operation literals exceed their canonical byte limit")
        if self.source is not None:
            targets = {mapping.input_field for mapping in self.source.mappings}
            if targets.intersection(self.literals):
                raise ValueError("source mappings cannot overlap literal input fields")
        return self

    def canonical_projection(self) -> dict[str, Any]:
        value: dict[str, Any] = {"literals": self.literals}
        if self.source is not None:
            value["source"] = self.source.model_dump(mode="json")
        return value


class OperationRecordV1(_ClosedModel):
    host_schema_version: Literal[1]
    record_kind: Literal["operation"]
    host_binding_version: Literal[1]
    execution_mode: Literal["unmanaged"]
    protocol_version: ProtocolVersion
    component: ComponentRef
    operation_hash: Sha256
    manifest_sha256: Sha256
    members: tuple[OperationMemberV1, ...] = Field(min_length=2, max_length=130)
    runner: RunnerIdentity
    renderers: tuple[RunnerIdentity, ...] = Field(max_length=128)
    binding: OperationBindingV1
    compatibility: tuple[PortCompatibility, ...] = Field(max_length=128)

    @field_validator("host_schema_version", "host_binding_version", mode="before")
    @classmethod
    def _strict_versions(cls, value: Any) -> Any:
        return _require_integer_one(value, name="record version")

    @model_validator(mode="after")
    def _validate_record(self) -> OperationRecordV1:
        kinds = [member.kind for member in self.members]
        if kinds.count("normalized_input") != 1 or kinds.count("result") != 1:
            raise ValueError("operation record requires exactly one input and result member")
        if kinds.count("artifact") > MAX_OPERATION_ARTIFACTS:
            raise ValueError("operation record contains too many artifacts")
        paths = [member.path for member in self.members]
        if len(set(paths)) != len(paths):
            raise ValueError("operation member paths must be unique")
        expected_members = sorted(
            self.members,
            key=lambda member: (
                {"normalized_input": 0, "result": 1, "artifact": 2}[member.kind],
                member.path.encode("utf-8"),
            ),
        )
        if list(self.members) != expected_members:
            raise ValueError("operation members are not canonically ordered")

        renderer_keys = [(item.name, item.version) for item in self.renderers]
        if len(set(renderer_keys)) != len(renderer_keys):
            raise ValueError("operation renderer identities must be unique")
        if renderer_keys != sorted(
            renderer_keys, key=lambda item: (item[0].encode(), item[1].encode())
        ):
            raise ValueError("operation renderer identities are not canonically ordered")
        _utf8_bytes(self.component.id, name="component ID", minimum=1, maximum=240)
        _validate_semver(self.component.version)
        _validate_semver(self.runner.version)
        for renderer in self.renderers:
            _validate_semver(renderer.version)

        if isinstance(self.binding.source, OperationOperationSourceV1):
            if str(self.protocol_version) != "0.5.0":
                raise ValueError("operation-source bindings require protocol 0.5.0")
            if len(self.compatibility) != len(self.binding.source.mappings):
                raise ValueError("operation-source compatibility must match mapping order")
        elif self.compatibility:
            raise ValueError("dataset and literal bindings require empty compatibility")
        return self

    def canonical_projection(self) -> dict[str, Any]:
        return {
            "host_schema_version": 1,
            "record_kind": "operation",
            "host_binding_version": 1,
            "execution_mode": "unmanaged",
            "protocol_version": self.protocol_version,
            "component": self.component.model_dump(mode="json"),
            "operation_hash": self.operation_hash,
            "manifest_sha256": self.manifest_sha256,
            "members": [member.model_dump(mode="json") for member in self.members],
            "runner": self.runner.model_dump(mode="json"),
            "renderers": [renderer.model_dump(mode="json") for renderer in self.renderers],
            "binding": self.binding.canonical_projection(),
            "compatibility": [item.model_dump(mode="json") for item in self.compatibility],
        }

    @property
    def digest(self) -> str:
        return canonical_hash(
            self.canonical_projection(), domain=OPERATION_RECORD_HASH_DOMAIN
        )

    @property
    def reference(self) -> str:
        return operation_reference(self.digest)


# The protocol provenance model remains wire-compatible with the stored dataset form.  This
# assertion is intentionally import-time and contains no component import.
assert set(CallerProvenance.model_fields) == set(DatasetProvenance.model_fields)


__all__ = [
    "CAS_SERIALIZATION",
    "DATASET_PAYLOAD_HASH_DOMAIN",
    "DATASET_PAYLOAD_SERIALIZATION",
    "DATASET_RECORD_HASH_DOMAIN",
    "MAX_RECORD_BYTES",
    "OPERATION_RECORD_HASH_DOMAIN",
    "PRETTY_JSON_SERIALIZATION",
    "CsvSourceOptions",
    "CsvRecordSourceOptions",
    "DatasetColumn",
    "DatasetFindingV1",
    "DatasetInstrument",
    "DatasetOperationSourceV1",
    "DatasetPayloadBindingV1",
    "DatasetPayloadColumnV1",
    "DatasetPayloadV1",
    "DatasetProvenance",
    "DatasetRecordProvenance",
    "DatasetRecordColumnV1",
    "DatasetRecordV1",
    "DatasetRegistrationRequest",
    "DatasetSemantics",
    "ExternalPreprocessing",
    "ExternalReceipt",
    "FieldMappingV1",
    "InlineJsonRecordSourceV1",
    "InlineJsonSource",
    "InlineRowsRecordSourceV1",
    "InlineRowsSource",
    "LocalCsvRecordSourceV1",
    "LocalCsvSource",
    "LocalJsonRecordSourceV1",
    "LocalJsonSource",
    "NoExternalPreprocessing",
    "NormalizationEventCode",
    "NormalizationEventV1",
    "ObservationBoundsV1",
    "OperationBindingV1",
    "OperationMemberV1",
    "OperationOperationSourceV1",
    "OperationRecordV1",
    "ParsedReference",
    "ReceiptExternalPreprocessing",
    "UnknownExternalPreprocessing",
    "cas_json_bytes",
    "dataset_reference",
    "operation_reference",
    "parse_dataset_reference",
    "parse_operation_reference",
    "pretty_json_bytes",
    "strict_cas_json_loads",
    "strict_json_loads",
]
