"""Closed project-owned request models for the local MCP transport."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from defined_quant.data_records import (
    MAX_LITERAL_BYTES,
    MAX_LITERAL_FIELDS,
    MAX_LITERALS_BYTES,
    DatasetRegistrationRequest,
    FieldMappingV1,
)
from defined_quant.discovery import FACET_NAMES
from defined_quant_protocol import CallerProvenance, ComponentRef, canonical_json_bytes
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

_COMPONENT_ID = r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
_SEMVER = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256 = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[a-z][a-z0-9_]{0,63}$"
_DATASET_REF = r"^dqds:v[0-9]{1,3}:[0-9a-f]{64}$"
_OPERATION_REF = r"^dqop:v[0-9]{1,3}:[0-9a-f]{64}$"
_CURSOR = r"^[A-Za-z0-9_-]{1,512}$"

SemVer = Annotated[str, Field(max_length=128, pattern=_SEMVER)]
Sha256 = Annotated[str, Field(pattern=_SHA256)]
DatasetRef = Annotated[str, Field(pattern=_DATASET_REF)]
OperationRef = Annotated[str, Field(pattern=_OPERATION_REF)]
Cursor = Annotated[str, Field(pattern=_CURSOR)]
SafeId = Annotated[str, Field(pattern=_SAFE_ID)]


def _utf8(value: str, *, minimum: int = 0, maximum: int) -> str:
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise ValueError("string is not strict UTF-8") from exc
    if not minimum <= size <= maximum:
        raise ValueError("string is outside its UTF-8 byte limit")
    return value


class ClosedRequest(BaseModel):
    """Base for every exact alpha tool argument object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host_schema_version: Literal[1] = 1

    @field_validator("host_schema_version", mode="before")
    @classmethod
    def _strict_host_schema(cls, value: Any) -> Any:
        if type(value) is not int or value != 1:
            raise ValueError("host schema version must be the integer one")
        return value


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: tuple[str, ...] = Field(default=(), max_length=64)
    groups: tuple[str, ...] = Field(default=(), max_length=64)
    tags: tuple[str, ...] = Field(default=(), max_length=64)
    intents: tuple[str, ...] = Field(default=(), max_length=64)
    input_concepts: tuple[str, ...] = Field(default=(), max_length=64)
    output_concepts: tuple[str, ...] = Field(default=(), max_length=64)
    lifecycles: tuple[str, ...] = Field(default=(), max_length=64)
    profiles: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator(*FACET_NAMES)
    @classmethod
    def _filter_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _utf8(value, minimum=1, maximum=128)
        return values


class SearchRequest(ClosedRequest):
    query: str
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: Annotated[int, Field(strict=True, ge=1, le=5)] = 5

    @field_validator("query")
    @classmethod
    def _query_bytes(cls, value: str) -> str:
        return _utf8(value, maximum=512)


class InspectRequest(ClosedRequest):
    component_id: str = Field(pattern=_COMPONENT_ID)
    expected_version: SemVer | None = None
    expected_subject_hash: Sha256 | None = None
    view: Literal["compact", "schemas"] = "compact"

    @field_validator("component_id")
    @classmethod
    def _component_bytes(cls, value: str) -> str:
        if not value.isascii():
            raise ValueError("component id must be ASCII")
        return _utf8(value, minimum=1, maximum=240)

    @field_validator("expected_version", "expected_subject_hash", mode="before")
    @classmethod
    def _optional_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("optional identity fields must be omitted rather than null")
        return value


RegisterDatasetRequest = DatasetRegistrationRequest


class DescribeDatasetRequest(ClosedRequest):
    dataset_ref: DatasetRef
    view: Literal["metadata", "preview"] = "metadata"
    cursor: Cursor | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=50)] = 20

    @model_validator(mode="after")
    def _selectors(self) -> DescribeDatasetRequest:
        if self.view == "metadata" and (self.cursor is not None or self.limit != 20):
            raise ValueError("metadata view forbids paging selectors")
        return self


class PortRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: ComponentRef
    field: str

    @field_validator("field")
    @classmethod
    def _field_bytes(cls, value: str) -> str:
        return _utf8(value, minimum=1, maximum=240)


class ComparePortsRequest(ClosedRequest):
    producer: PortRef
    consumer: PortRef


class DatasetSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["dataset"]
    ref: DatasetRef
    mappings: tuple[FieldMappingV1, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _unique_mappings(self) -> DatasetSource:
        sources = [mapping.source_field for mapping in self.mappings]
        targets = [mapping.input_field for mapping in self.mappings]
        if len(set(sources)) != len(sources) or len(set(targets)) != len(targets):
            raise ValueError("source mappings must be unique")
        return self


class OperationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["operation"]
    ref: OperationRef
    mappings: tuple[FieldMappingV1, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _unique_mappings(self) -> OperationSource:
        sources = [mapping.source_field for mapping in self.mappings]
        targets = [mapping.input_field for mapping in self.mappings]
        if len(set(sources)) != len(sources) or len(set(targets)) != len(targets):
            raise ValueError("source mappings must be unique")
        return self


NonLiteralSource: TypeAlias = DatasetSource | OperationSource


class ExecuteComponentRequest(ClosedRequest):
    component: ComponentRef
    literals: dict[str, JsonValue] = Field(default_factory=dict)
    sources: tuple[NonLiteralSource, ...] = Field(default=(), max_length=128)
    provenance: CallerProvenance | None = None
    artifacts: Literal["none", "svg_all"] = "none"

    @model_validator(mode="after")
    def _closed_binding(self) -> ExecuteComponentRequest:
        if len(self.literals) > MAX_LITERAL_FIELDS:
            raise ValueError("too many literal fields")
        for key, value in self.literals.items():
            _utf8(key, minimum=1, maximum=240)
            if len(canonical_json_bytes(value)) > MAX_LITERAL_BYTES:
                raise ValueError("literal exceeds its canonical byte limit")
        if len(canonical_json_bytes(self.literals)) > MAX_LITERALS_BYTES:
            raise ValueError("literal object exceeds its canonical byte limit")
        targets = {
            mapping.input_field
            for source in self.sources
            for mapping in source.mappings
        }
        if targets.intersection(self.literals):
            raise ValueError("source mappings overlap literal fields")
        if not self.sources and self.provenance is None:
            raise ValueError("literal-only execution requires provenance")
        if self.sources and self.provenance is not None:
            raise ValueError("source execution forbids provenance")
        return self


class GetOperationRequest(ClosedRequest):
    operation_ref: OperationRef
    view: Literal[
        "summary", "manifest", "result_field", "messages", "artifact_metadata"
    ] = "summary"
    field: str | None = None
    artifact_id: SafeId | None = None
    cursor: Cursor | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=1000)] | None = None

    @field_validator("field")
    @classmethod
    def _field_bytes(cls, value: str | None) -> str | None:
        if value is not None:
            _utf8(value, minimum=1, maximum=240)
        return value

    @model_validator(mode="after")
    def _selectors(self) -> GetOperationRequest:
        if self.view in {"summary", "manifest"} and any(
            value is not None for value in (self.field, self.artifact_id, self.cursor, self.limit)
        ):
            raise ValueError("compact view forbids selectors")
        if self.view == "result_field" and (
            self.field is None or self.artifact_id is not None
        ):
            raise ValueError("result field view requires exactly its field selector")
        if self.view == "messages" and (
            self.field is not None
            or self.artifact_id is not None
            or (self.limit is not None and self.limit > 100)
        ):
            raise ValueError("message view selectors are invalid")
        if self.view == "artifact_metadata" and (
            self.artifact_id is None
            or self.field is not None
            or self.cursor is not None
            or self.limit is not None
        ):
            raise ValueError("artifact metadata selectors are invalid")
        return self


REQUEST_MODELS: dict[str, type[BaseModel]] = {
    "search_components": SearchRequest,
    "inspect_component": InspectRequest,
    "register_dataset": RegisterDatasetRequest,
    "describe_dataset": DescribeDatasetRequest,
    "compare_ports": ComparePortsRequest,
    "execute_component": ExecuteComponentRequest,
    "get_operation": GetOperationRequest,
}


__all__ = [
    "ComparePortsRequest",
    "DescribeDatasetRequest",
    "ExecuteComponentRequest",
    "GetOperationRequest",
    "InspectRequest",
    "REQUEST_MODELS",
    "RegisterDatasetRequest",
    "SearchRequest",
]
