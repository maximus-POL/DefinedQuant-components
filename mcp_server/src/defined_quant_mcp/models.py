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
from defined_quant.run_views import (
    DEFAULT_ARTIFACT_CHUNK_BYTES,
    MAX_ARTIFACT_CHUNK_BYTES,
)
from defined_quant_protocol import (
    BackendRole,
    CallerProvenance,
    CapabilityRef,
    ComponentRef,
    PlanProposal,
    ResolutionConstraint,
    ResolutionConstraintSet,
    canonical_json_bytes,
)
from defined_quant_protocol import (
    PlanProposalV1 as LegacyPlanProposal,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

_COMPONENT_ID = r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
_METHOD_ID = r"^dq(?:\.[a-z][a-z0-9_]*){2,5}$"
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
_PLAN_REF = r"^dqplan:[0-9a-f]{64}$"
_RUN_REF = r"^dqrun:[0-9a-f]{64}$"
_CURSOR = r"^[A-Za-z0-9_-]{1,512}$"

SemVer = Annotated[str, Field(max_length=128, pattern=_SEMVER)]
Sha256 = Annotated[str, Field(pattern=_SHA256)]
DatasetRef = Annotated[str, Field(pattern=_DATASET_REF)]
OperationRef = Annotated[str, Field(pattern=_OPERATION_REF)]
PlanRef = Annotated[str, Field(pattern=_PLAN_REF)]
RunRef = Annotated[str, Field(pattern=_RUN_REF)]
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


class MethodSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: tuple[str, ...] = Field(default=(), max_length=64)
    tags: tuple[str, ...] = Field(default=(), max_length=64)
    intents: tuple[str, ...] = Field(default=(), max_length=64)
    input_concepts: tuple[str, ...] = Field(default=(), max_length=64)
    output_concepts: tuple[str, ...] = Field(default=(), max_length=64)
    lifecycles: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator(
        "categories",
        "tags",
        "intents",
        "input_concepts",
        "output_concepts",
        "lifecycles",
    )
    @classmethod
    def _filter_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _utf8(value, minimum=1, maximum=128)
        return values


class SearchMethodsRequest(ClosedRequest):
    query: str
    filters: MethodSearchFilters = Field(default_factory=MethodSearchFilters)
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 10

    @field_validator("query")
    @classmethod
    def _query_bytes(cls, value: str) -> str:
        return _utf8(value, maximum=512)


class InspectMethodRequest(ClosedRequest):
    method_id: str = Field(pattern=_METHOD_ID)
    version: SemVer | None = None

    @field_validator("method_id")
    @classmethod
    def _method_bytes(cls, value: str) -> str:
        if not value.isascii():
            raise ValueError("method id must be ASCII")
        return _utf8(value, minimum=1, maximum=320)


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


class ResolutionScopeRequest(BaseModel):
    """Caller-visible constraint scope without host receipt controls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    all_steps: Literal[True] | None = None
    step_id: SafeId | None = None
    capability: CapabilityRef | None = None


class ResolutionConstraintRequest(BaseModel):
    """Raw user-explicit preference accepted from an agent proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    constraint_id: SafeId
    scope: ResolutionScopeRequest
    dimension: Literal[
        "implementation",
        "backend",
        "adapter_family",
        "backend_kind",
        "transport",
        "locality",
        "network",
    ]
    backend_role: BackendRole | None = None
    mode: Literal["required", "preferred", "forbidden", "allowed_set"]
    targets: tuple[Annotated[str, Field(min_length=1, max_length=320)], ...] = Field(
        min_length=1,
        max_length=64,
    )
    asserted_origin: Literal["user_explicit"]
    fallback: Literal[
        "forbidden",
        "within_preferences",
        "any_policy_eligible",
    ] = "forbidden"

    def to_protocol(self) -> ResolutionConstraint:
        return ResolutionConstraint.model_validate(self.model_dump(mode="json"))


class ResolutionConstraintSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    constraints: tuple[ResolutionConstraintRequest, ...] = Field(
        default=(),
        max_length=128,
    )

    def to_protocol(self) -> ResolutionConstraintSet:
        return ResolutionConstraintSet(
            constraints=tuple(item.to_protocol() for item in self.constraints)
        )


class PlanProposalRequest(BaseModel):
    """Closed non-secret raw proposal; trusted receipts are deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    method_id: str = Field(pattern=_METHOD_ID, max_length=320)
    method_version: SemVer
    financial_inputs: dict[str, JsonValue]
    conventions: dict[str, JsonValue]
    resolution_constraints: ResolutionConstraintSetRequest = Field(
        default_factory=ResolutionConstraintSetRequest
    )
    agent_rationale: str | None = Field(default=None, min_length=1, max_length=4000)

    def to_protocol(self) -> PlanProposal:
        return PlanProposal(
            schema_version=self.schema_version,
            method_id=self.method_id,
            method_version=self.method_version,
            financial_inputs=self.financial_inputs,
            conventions=self.conventions,
            resolution_constraints=self.resolution_constraints.to_protocol(),
            agent_rationale=self.agent_rationale,
        )

    @model_validator(mode="after")
    def _validate_protocol(self) -> PlanProposalRequest:
        self.to_protocol()
        return self


class CompilePlanRequest(ClosedRequest):
    proposal: PlanProposalRequest


class CompileComponentPlanRequest(ClosedRequest):
    """Compatibility-only request for the legacy component projection."""

    proposal: LegacyPlanProposal


class ExecutePlanRequest(ClosedRequest):
    plan_ref: PlanRef


class GetPlanRequest(ClosedRequest):
    plan_ref: PlanRef


class GetRunRequest(ClosedRequest):
    run_ref: RunRef
    view: Literal[
        "summary",
        "steps",
        "warnings",
        "artifacts",
        "datasets",
        "output_fields",
        "output",
    ] = "summary"
    field: SafeId | None = None
    cursor: Cursor | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=1000)] | None = None

    @model_validator(mode="after")
    def _selectors(self) -> GetRunRequest:
        if self.view == "summary":
            if self.field is not None or self.cursor is not None or self.limit is not None:
                raise ValueError("summary view forbids selectors")
        elif self.view == "output":
            if self.field is None:
                raise ValueError("output view requires field")
        elif self.field is not None:
            raise ValueError("field is available only for output view")
        return self


class ReadArtifactRequest(ClosedRequest):
    run_ref: RunRef
    artifact_id: SafeId
    cursor: Cursor | None = None
    limit_bytes: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_ARTIFACT_CHUNK_BYTES),
    ] = DEFAULT_ARTIFACT_CHUNK_BYTES


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
    "search_methods": SearchMethodsRequest,
    "inspect_method": InspectMethodRequest,
    "compile_plan": CompilePlanRequest,
    "execute_plan": ExecutePlanRequest,
    "get_plan": GetPlanRequest,
    "get_run": GetRunRequest,
    "get_dataset": DescribeDatasetRequest,
    "read_artifact": ReadArtifactRequest,
    "register_dataset": RegisterDatasetRequest,
    # Compatibility-only component surfaces. Remove no later than 2026-12-31 or the first 0.2.0
    # release; they are intentionally named separately from the canonical methods API.
    "search_components": SearchRequest,
    "inspect_component": InspectRequest,
    "describe_dataset": DescribeDatasetRequest,
    "compare_ports": ComparePortsRequest,
    "compile_component_plan": CompileComponentPlanRequest,
    "execute_component": ExecuteComponentRequest,
    "get_operation": GetOperationRequest,
}


__all__ = [
    "ComparePortsRequest",
    "CompileComponentPlanRequest",
    "CompilePlanRequest",
    "DescribeDatasetRequest",
    "ExecuteComponentRequest",
    "ExecutePlanRequest",
    "GetOperationRequest",
    "GetPlanRequest",
    "GetRunRequest",
    "InspectMethodRequest",
    "InspectRequest",
    "MethodSearchFilters",
    "REQUEST_MODELS",
    "ReadArtifactRequest",
    "RegisterDatasetRequest",
    "SearchMethodsRequest",
    "SearchRequest",
]
