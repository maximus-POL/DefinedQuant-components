"""Closed, host-neutral models for one unmanaged component operation."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

from .canonical import (
    CANONICALIZATION_ID,
    canonical_hash,
    canonical_hash_framing,
    canonical_json_bytes,
)
from .version import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS, ProtocolVersion

_COMPONENT_ID_PATTERN = r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
_SAFE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_RELATIVE_MEMBER_PATH_PATTERN = (
    r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)*$"
)
_UNSAFE_MEMBER_PATH_CHARACTER_PATTERN = r"[^A-Za-z0-9_./-]"
_WINDOWS_DEVICE_MEMBER_PATTERN = (
    r"(^|/)(?:[Cc][Oo][Nn]|[Pp][Rr][Nn]|[Aa][Uu][Xx]|[Nn][Uu][Ll]|"
    r"[Cc][Oo][Mm][1-9]|[Ll][Pp][Tt][1-9])(?:\.[A-Za-z0-9_-]+)*(?:/|$)"
)
_RELATIVE_MEMBER_PATH_ERROR = (
    "member path must use safe-ASCII segments with dots only inside segment names"
)
_ASCII_CASEFOLD_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)
_WINDOWS_DEVICE_MEMBER_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_OPERATION_HASH_DOMAIN = "operation.request.v1"


def portable_member_key(value: str) -> str:
    """Return the platform-independent identity key for one portable member path."""

    try:
        encoded = value.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ValueError(_RELATIVE_MEMBER_PATH_ERROR) from exc
    if not 1 <= len(encoded) <= 512 or re.fullmatch(_RELATIVE_MEMBER_PATH_PATTERN, value) is None:
        raise ValueError(_RELATIVE_MEMBER_PATH_ERROR)

    folded_segments: list[str] = []
    for segment in value.split("/"):
        if (
            segment in {".", ".."}
            or segment.endswith((".", " "))
            or ":" in segment
            or "\\" in segment
        ):
            raise ValueError(_RELATIVE_MEMBER_PATH_ERROR)
        folded = segment.translate(_ASCII_CASEFOLD_TABLE)
        if folded.split(".", 1)[0] in _WINDOWS_DEVICE_MEMBER_NAMES:
            raise ValueError(_RELATIVE_MEMBER_PATH_ERROR)
        folded_segments.append(folded)
    return "/".join(folded_segments)


def _relative_member_path(value: str) -> str:
    """Validate one portable safe-ASCII bundle-member path."""

    portable_member_key(value)
    return value


RelativeMemberPath: TypeAlias = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=_RELATIVE_MEMBER_PATH_PATTERN,
        json_schema_extra={
            "not": {
                "anyOf": [
                    {"pattern": _UNSAFE_MEMBER_PATH_CHARACTER_PATTERN},
                    {"pattern": _WINDOWS_DEVICE_MEMBER_PATTERN},
                ]
            }
        },
    ),
    AfterValidator(_relative_member_path),
]
ProvenanceText: TypeAlias = Annotated[str, Field(min_length=1, max_length=500)]


class SourceKind(StrEnum):
    """Where caller-supplied values originated."""

    USER_PROMPT = "user_prompt"
    USER_ATTACHMENT = "user_attachment"
    EXTERNAL_PROVIDER = "external_provider"
    SYNTHETIC = "synthetic"


class InterpretationMethod(StrEnum):
    """How source values became canonical component input."""

    AI_INTERPRETED = "ai_interpreted"
    CALLER_STRUCTURED = "caller_structured"
    ADAPTER_NORMALIZED = "adapter_normalized"


class ProvenanceStatus(StrEnum):
    """The two provenance claims an unmanaged caller may honestly make."""

    UNVERIFIED = "unverified"
    CALLER_CONFIRMED = "caller_confirmed"


VerificationStatus = ProvenanceStatus


class CallerProvenance(BaseModel):
    """Source and interpretation context asserted by the unmanaged caller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_kind: SourceKind
    interpretation_method: InterpretationMethod
    verification_status: ProvenanceStatus = ProvenanceStatus.UNVERIFIED
    label: str = Field(min_length=1, max_length=240)
    references: tuple[ProvenanceText, ...] = Field(default=(), max_length=50)
    content_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    assumptions: tuple[ProvenanceText, ...] = Field(default=(), max_length=50)


OperationProvenance = CallerProvenance


class ComponentRef(BaseModel):
    """Exact behavior identity requested from the installed catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_COMPONENT_ID_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)
    subject_hash: str = Field(pattern=_SHA256_PATTERN)


class SvgArtifactRequest(BaseModel):
    """Request deterministic SVGs for every visualization emitted by the component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["svg"] = "svg"
    selection: Literal["all"] = "all"


class OperationRequest(BaseModel):
    """Semantic request for any exactly bound component, independent of its host runtime."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-defined-quant-canonicalization": CANONICALIZATION_ID},
    )

    schema_version: Literal[1] = 1
    component: ComponentRef
    input: dict[str, JsonValue]
    provenance: CallerProvenance
    artifacts: SvgArtifactRequest | None = None

    @model_validator(mode="after")
    def validate_canonical_input(self) -> OperationRequest:
        canonical_json_bytes(self.input)
        return self

    @property
    def operation_hash(self) -> str:
        """Return the domain-separated binding of every semantic request field."""

        return canonical_hash(self.model_dump(mode="json"), domain=_OPERATION_HASH_DOMAIN)


def operation_hash(request: OperationRequest) -> str:
    """Compatibility function for callers that prefer an explicit hash operation."""

    return request.operation_hash


class RunnerIdentity(BaseModel):
    """Versioned identity of the catalog-wide deterministic runner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_SAFE_ID_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)


class FileDigest(BaseModel):
    """Portable bundle member and its exact content digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: RelativeMemberPath
    sha256: str = Field(pattern=_SHA256_PATTERN)


class SvgArtifact(BaseModel):
    """One deterministic SVG bundle member."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["svg"] = "svg"
    media_type: Literal["image/svg+xml"] = "image/svg+xml"
    path: RelativeMemberPath
    visualization_id: str = Field(pattern=_SAFE_ID_PATTERN)
    title: str = Field(min_length=1, max_length=160)
    alt_text: str = Field(min_length=1, max_length=500)
    visualization_hash: str = Field(pattern=_SHA256_PATTERN)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    renderer: str = Field(pattern=_SAFE_ID_PATTERN)
    renderer_version: str = Field(pattern=_SEMVER_PATTERN)


class OperationManifest(BaseModel):
    """Portable, deterministic record of one successful unmanaged operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    canonicalization_id: Literal["dq-tagged-json-v1"] = "dq-tagged-json-v1"
    execution_mode: Literal["unmanaged"] = "unmanaged"
    operation_hash: str = Field(pattern=_SHA256_PATTERN)
    request: OperationRequest
    component: ComponentRef
    runner: RunnerIdentity
    input: FileDigest
    result: FileDigest
    artifacts: tuple[SvgArtifact, ...] = ()

    @model_validator(mode="after")
    def validate_bindings(self) -> OperationManifest:
        if self.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError("manifest protocol version is not supported by this package")
        if self.canonicalization_id != CANONICALIZATION_ID:
            raise ValueError("manifest canonicalization does not match this package")
        if self.operation_hash != self.request.operation_hash:
            raise ValueError("manifest operation_hash does not bind its request")
        if self.component != self.request.component:
            raise ValueError("manifest component does not match its request")
        paths = [self.input.path, self.result.path, *(artifact.path for artifact in self.artifacts)]
        portable_keys = [portable_member_key(path) for path in paths]
        if (
            len(set(portable_keys)) != len(portable_keys)
            or portable_member_key("manifest.json") in portable_keys
        ):
            raise ValueError("manifest member paths must be unique")
        visualization_ids = [artifact.visualization_id for artifact in self.artifacts]
        if len(set(visualization_ids)) != len(visualization_ids):
            raise ValueError("manifest artifact visualization IDs must be unique")
        if self.request.artifacts is None and self.artifacts:
            raise ValueError("manifest contains artifacts that were not requested")
        return self


class OperationErrorCode(StrEnum):
    """Stable error categories exposed by the generic runner."""

    INVALID_OPERATION_REQUEST = "invalid_operation_request"
    INVALID_JSON = "invalid_json"
    COMPONENT_NOT_FOUND = "component_not_found"
    COMPONENT_IDENTITY_MISMATCH = "component_identity_mismatch"
    INVALID_COMPONENT_INPUT = "invalid_component_input"
    COMPONENT_REFUSED = "component_refused"
    COMPONENT_EXECUTION_FAILED = "component_execution_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    COMPONENT_CONTRACT_ERROR = "component_contract_error"
    INVALID_OUTPUT_DIRECTORY = "invalid_output_directory"
    OUTPUT_EXISTS = "output_exists"
    ARTIFACT_WRITE_FAILED = "artifact_write_failed"
    OPERATION_FAILED = "operation_failed"


class OperationError(BaseModel):
    """Stable, non-traceback failure information safe for protocol callers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: OperationErrorCode
    message: str = Field(min_length=1, max_length=500)
    component: ComponentRef | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_canonical_details(self) -> OperationError:
        canonical_json_bytes(self.details)
        return self


class OperationSuccess(BaseModel):
    """Successful protocol response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["succeeded"] = "succeeded"
    manifest: OperationManifest


class OperationFailure(BaseModel):
    """Failed or refused protocol response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["failed"] = "failed"
    operation_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    error: OperationError


OperationResult: TypeAlias = Annotated[
    OperationSuccess | OperationFailure,
    Field(discriminator="status"),
]


def operation_protocol_schema() -> dict[str, Any]:
    """Return the complete versioned schema surface for host integrations."""

    return {
        "package": "defined_quant_protocol",
        "name": "defined_quant_operation",
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": 1,
        "execution_mode": "unmanaged",
        "canonicalization_id": CANONICALIZATION_ID,
        "hash_algorithm": "sha256",
        "hash_framing": canonical_hash_framing(),
        "operation_request_domain": _OPERATION_HASH_DOMAIN,
        "canonicalization_vector": {
            "input": {"b": 2, "a": 1},
            "domain": "test.vector",
            "canonical_bytes_hex": (
                "5b226f626a656374222c5b5b2261222c5b226e756d626572222c"
                "2233666630303030303030303030303030225d5d2c5b2262222c"
                "5b226e756d626572222c2234303030303030303030303030303030"
                "225d5d5d5d"
            ),
            "sha256": "c2140aabfc1f1f588b06a959e0ac58417983c34f5c88db15ffebebbf0670b26b",
        },
        "schemas": {
            "request": OperationRequest.model_json_schema(),
            "manifest": OperationManifest.model_json_schema(),
            "success": OperationSuccess.model_json_schema(),
            "failure": OperationFailure.model_json_schema(),
            "result": TypeAdapter(OperationResult).json_schema(),
        },
    }


__all__ = [
    "CallerProvenance",
    "ComponentRef",
    "FileDigest",
    "InterpretationMethod",
    "OperationError",
    "OperationErrorCode",
    "OperationFailure",
    "OperationManifest",
    "OperationProvenance",
    "OperationRequest",
    "OperationResult",
    "OperationSuccess",
    "ProvenanceStatus",
    "RelativeMemberPath",
    "RunnerIdentity",
    "SourceKind",
    "SvgArtifact",
    "SvgArtifactRequest",
    "VerificationStatus",
    "operation_hash",
    "operation_protocol_schema",
    "portable_member_key",
]
