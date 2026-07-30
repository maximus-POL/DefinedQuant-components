"""Closed agent-operation protocol for discovering and executing components."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_COMPONENT_ID_PATTERN = r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
ProvenanceText = Annotated[str, Field(min_length=1, max_length=500)]


class SourceKind(StrEnum):
    """Where the values supplied to an operation originated."""

    USER_PROMPT = "user_prompt"
    USER_ATTACHMENT = "user_attachment"
    EXTERNAL_PROVIDER = "external_provider"
    SYNTHETIC = "synthetic"


class InterpretationMethod(StrEnum):
    """How source values became canonical component input."""

    AI_INTERPRETED = "ai_interpreted"
    CALLER_STRUCTURED = "caller_structured"
    ADAPTER_NORMALIZED = "adapter_normalized"


class VerificationStatus(StrEnum):
    """What verification claim is supported for the interpreted input."""

    UNVERIFIED = "unverified"
    CALLER_CONFIRMED = "caller_confirmed"
    SOURCE_BOUND = "source_bound"


class ViewUseCase(StrEnum):
    """Presentation intent supplied by an agent host."""

    CHAT = "chat"
    PORTABLE = "portable"
    PRINT = "print"


class SupportedMediaType(StrEnum):
    """Media types supported by the deterministic renderers."""

    HTML = "text/html"
    SVG = "image/svg+xml"


class OperationProvenance(BaseModel):
    """Auditable source and interpretation context supplied by the agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_kind: SourceKind
    interpretation_method: InterpretationMethod
    verification_status: VerificationStatus
    label: str = Field(min_length=1, max_length=240)
    references: tuple[ProvenanceText, ...] = Field(default=(), max_length=50)
    content_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    assumptions: tuple[ProvenanceText, ...] = Field(default=(), max_length=50)


class OperationViewRequest(BaseModel):
    """Host capabilities used for deterministic view selection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    use_case: ViewUseCase = ViewUseCase.CHAT
    supported_media_types: tuple[SupportedMediaType, ...] = ()


class OperationRequest(BaseModel):
    """One generic request that can execute any installed component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    component_id: str = Field(pattern=_COMPONENT_ID_PATTERN)
    input: dict[str, Any]
    provenance: OperationProvenance
    view: OperationViewRequest = Field(default_factory=OperationViewRequest)
    output_dir: str = Field(min_length=1)
    catalog_root: str | None = Field(default=None, min_length=1)
    overwrite: bool = False


class AdapterIdentity(BaseModel):
    """Identity of the catalog-wide host adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["use-defined-quant"] = "use-defined-quant"
    version: int = Field(ge=1)


class ComponentIdentity(BaseModel):
    """Bound component identity recorded for an execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_COMPONENT_ID_PATTERN)
    title: str | None = None
    version: str = Field(pattern=_SEMVER_PATTERN)
    callable: str = Field(min_length=1)
    subject_hash: str = Field(pattern=_SHA256_PATTERN)


class FileDigest(BaseModel):
    """Local materialized file and its content digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class OperationReceipt(BaseModel):
    """Semantic request binding independent of local output paths."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol: Literal["defined_quant_operation"] = "defined_quant_operation"
    request_schema_version: Literal[1] = 1
    operation_hash: str = Field(pattern=_SHA256_PATTERN)
    provenance: OperationProvenance


class ArtifactRecord(BaseModel):
    """One deterministic primary, alternative, or supporting view."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["interactive", "image"]
    role: Literal["primary", "alternative", "supporting"]
    media_type: SupportedMediaType
    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    alt_text: str = Field(min_length=1)
    renderer: str = Field(min_length=1)
    renderer_version: str = Field(pattern=_SEMVER_PATTERN)
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    interactive: bool | None = None
    view_id: str | None = None
    dashboard_id: str | None = None
    visualization_id: str | None = None
    view_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    dashboard_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    visualization_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class ViewSelectionRecord(BaseModel):
    """Why one declared view became the primary host representation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    use_case: ViewUseCase
    selected_view_id: str = Field(min_length=1)
    selection_reason: Literal["default_chat", "use_case_match", "fallback"]
    supported_media_types: tuple[SupportedMediaType | Literal["*"], ...]


class OperationManifest(BaseModel):
    """Typed, hash-bound result manifest emitted by the generic adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    operation: OperationReceipt
    adapter: AdapterIdentity
    component: ComponentIdentity
    input: FileDigest
    result: FileDigest
    manifest_path: str = Field(min_length=1)
    artifacts: tuple[ArtifactRecord, ...]
    view_selection: ViewSelectionRecord | None = None


class OperationError(BaseModel):
    """Stable failure payload returned to an agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    component_id: str | None = Field(default=None, pattern=_COMPONENT_ID_PATTERN)


class OperationSuccess(BaseModel):
    """Successful response for request-envelope execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["succeeded"] = "succeeded"
    manifest: OperationManifest


class OperationFailure(BaseModel):
    """Failed or refused response for request-envelope execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["failed"] = "failed"
    error: OperationError


def operation_hash(request: OperationRequest) -> str:
    """Hash answer- and presentation-relevant request fields, excluding local paths."""

    semantic_request = {
        "schema_version": request.schema_version,
        "component_id": request.component_id,
        "input": request.input,
        "provenance": request.provenance.model_dump(mode="json"),
        "view": request.view.model_dump(mode="json"),
    }
    payload = json.dumps(
        semantic_request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def operation_protocol_schema() -> dict[str, Any]:
    """Return the public schemas required to build an agent tool wrapper."""

    return {
        "name": "defined_quant_operation",
        "schema_version": 1,
        "request_schema": OperationRequest.model_json_schema(),
        "success_schema": OperationSuccess.model_json_schema(),
        "failure_schema": OperationFailure.model_json_schema(),
        "manifest_schema": OperationManifest.model_json_schema(),
    }


__all__ = [
    "AdapterIdentity",
    "ArtifactRecord",
    "ComponentIdentity",
    "FileDigest",
    "InterpretationMethod",
    "OperationError",
    "OperationFailure",
    "OperationManifest",
    "OperationProvenance",
    "OperationReceipt",
    "OperationRequest",
    "OperationSuccess",
    "OperationViewRequest",
    "SourceKind",
    "SupportedMediaType",
    "VerificationStatus",
    "ViewSelectionRecord",
    "ViewUseCase",
    "operation_hash",
    "operation_protocol_schema",
]
