"""Bounded immutable storage for methods-first plan, step, and run records.

This store is deliberately separate from :mod:`defined_quant.session_cas`.  The latter owns the
frozen dataset and legacy operation formats, including their suffixed references and filesystem
layout.  Methods-first records use their canonical unsuffixed protocol identities and remain
ephemeral to one host session until a later retention policy explicitly promotes them.

Publication keeps the exact tagged canonical bytes used by the protocol hash functions.  A
reference can be published once, an identical retry is idempotent, and different bytes at an
existing reference are rejected.  No adapter package or provider SDK is imported here.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import TypeVar, cast

from defined_quant_protocol.canonical import canonical_hash, canonical_json_bytes
from defined_quant_protocol.execution import (
    RunRecord,
    RunRef,
    StepRecord,
    StepRef,
)
from defined_quant_protocol.resolution import PlanRecord, PlanRef
from pydantic import BaseModel, ValidationError

MAX_METHOD_RECORD_BYTES = 8 * 1024 * 1024
MAX_METHOD_SESSION_BYTES = 128 * 1024 * 1024
MAX_METHOD_SESSION_RECORDS = 10_000
METHOD_SESSION_BINDING_HASH_DOMAIN = "records.method_session_binding"

_REFERENCE_PATTERN = re.compile(r"^(dqplan|dqstep|dqrun):[0-9a-f]{64}$")
_SECRET_KEY_TOKENS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "authorization_header",
        "client_secret",
        "connection_string",
        "connection_uri",
        "connection_url",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "pwd",
        "refresh_token",
        "secret",
        "secrets",
        "token",
    }
)
_SAFE_CREDENTIAL_STATUS_VALUES = frozenset(
    {
        "not_required",
        "not_checked",
        "satisfied",
        "unsatisfied",
        "unknown",
    }
)
_SECRET_VALUE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"(?:^|[;\s])(?:password|passwd|pwd|api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|client[_-]?secret|connection[_-]?string)\s*[:=]",
        r"(?:https?|ftp|file|jdbc|postgres(?:ql)?|mysql|mssql|mongodb|redis)://"
        r"[^/@\s:]+:[^/@\s]+@",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"(?:^|\s)Bearer\s+[A-Za-z0-9._~+/-]+=*(?:\s|$)",
        r"\bAKIA[0-9A-Z]{16}\b",
    )
)


class MethodRecordStoreErrorCode(StrEnum):
    """Closed failure vocabulary for the methods-first session store."""

    CLOSED = "closed"
    INVALID_RECORD = "invalid_record"
    INVALID_REFERENCE = "invalid_reference"
    REFERENCE_NOT_FOUND = "reference_not_found"
    REFERENCE_SCOPE_DENIED = "reference_scope_denied"
    SENSITIVE_MATERIAL = "sensitive_material"
    RECORD_TOO_LARGE = "record_too_large"
    SESSION_FULL = "session_full"
    RECORD_COUNT_EXCEEDED = "record_count_exceeded"
    PUBLICATION_CONFLICT = "publication_conflict"
    DEPENDENCY_MISSING = "dependency_missing"


class MethodRecordStoreError(RuntimeError):
    """Safe typed store failure without record or provider content in its message."""

    def __init__(self, code: MethodRecordStoreErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class MethodRecordLimits:
    """Per-store ceilings bounded by hard process limits."""

    record_bytes: int = MAX_METHOD_RECORD_BYTES
    session_bytes: int = MAX_METHOD_SESSION_BYTES
    records: int = MAX_METHOD_SESSION_RECORDS

    def __post_init__(self) -> None:
        bounds = (
            (self.record_bytes, MAX_METHOD_RECORD_BYTES),
            (self.session_bytes, MAX_METHOD_SESSION_BYTES),
            (self.records, MAX_METHOD_SESSION_RECORDS),
        )
        if any(type(value) is not int or not 1 <= value <= ceiling for value, ceiling in bounds):
            raise ValueError("method record limits must be positive integers within hard maxima")
        if self.record_bytes > self.session_bytes:
            raise ValueError("one record cannot exceed the whole method-record session")


@dataclass(frozen=True, slots=True)
class StoredPlan:
    reference: str
    record: PlanRecord
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class StoredStep:
    reference: str
    record: StepRecord
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class StoredRun:
    reference: str
    record: RunRecord
    canonical_bytes: bytes


class MethodRecordScopeRegistry:
    """Process-local index used only to distinguish foreign active session references."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions_by_reference: dict[str, set[str]] = {}

    def register(self, session_id: str, reference: str) -> None:
        with self._lock:
            self._sessions_by_reference.setdefault(reference, set()).add(session_id)

    def known_outside(self, session_id: str, reference: str) -> bool:
        with self._lock:
            owners = self._sessions_by_reference.get(reference, set())
            return bool(owners.difference({session_id}))

    def unregister_session(self, session_id: str) -> None:
        with self._lock:
            empty: list[str] = []
            for reference, owners in self._sessions_by_reference.items():
                owners.discard(session_id)
                if not owners:
                    empty.append(reference)
            for reference in empty:
                del self._sessions_by_reference[reference]


_DEFAULT_SCOPE_REGISTRY = MethodRecordScopeRegistry()

RecordModel = TypeVar("RecordModel", PlanRecord, StepRecord, RunRecord)
StoredModel = TypeVar("StoredModel", StoredPlan, StoredStep, StoredRun)


def _projection(record: BaseModel) -> dict[str, object]:
    return cast(
        dict[str, object],
        record.model_dump(mode="json", exclude_computed_fields=True),
    )


def _contains_sensitive_material(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                return True
            normalized = key.casefold().replace("-", "_")
            if normalized in _SECRET_KEY_TOKENS:
                allowed_status = (
                    normalized == "credentials"
                    and isinstance(nested, str)
                    and nested in _SAFE_CREDENTIAL_STATUS_VALUES
                )
                if not allowed_status:
                    return True
            if _contains_sensitive_material(nested):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_sensitive_material(item) for item in value)
    return isinstance(value, str) and any(
        pattern.search(value) is not None for pattern in _SECRET_VALUE_PATTERNS
    )


def _validate_and_encode(
    record: RecordModel,
    model: type[RecordModel],
) -> tuple[RecordModel, bytes]:
    try:
        validated = model.model_validate(_projection(record))
        projection = _projection(validated)
        if _contains_sensitive_material(projection):
            raise MethodRecordStoreError(MethodRecordStoreErrorCode.SENSITIVE_MATERIAL)
        return validated, canonical_json_bytes(projection)
    except MethodRecordStoreError:
        raise
    except (TypeError, ValueError, ValidationError) as exc:
        raise MethodRecordStoreError(MethodRecordStoreErrorCode.INVALID_RECORD) from exc


def _reference_value(
    reference: str | PlanRef | StepRef | RunRef,
    *,
    prefix: str | None = None,
) -> str:
    value = reference if isinstance(reference, str) else reference.reference
    match = _REFERENCE_PATTERN.fullmatch(value)
    if match is None or (prefix is not None and match.group(1) != prefix):
        raise MethodRecordStoreError(MethodRecordStoreErrorCode.INVALID_REFERENCE)
    return value


class MethodRecordStore:
    """One bounded, ephemeral, thread-safe methods-first record session."""

    def __init__(
        self,
        *,
        scope_registry: MethodRecordScopeRegistry | None = None,
        limits: MethodRecordLimits = MethodRecordLimits(),
    ) -> None:
        self._lock = RLock()
        self._scope_registry = scope_registry or _DEFAULT_SCOPE_REGISTRY
        self._limits = limits
        self._session_id = secrets.token_hex(16)
        self._plans: dict[str, StoredPlan] = {}
        self._steps: dict[str, StoredStep] = {}
        self._runs: dict[str, StoredRun] = {}
        self._used_bytes = 0
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def origin_binding_hash(self) -> str:
        """Opaque binding used by trusted preference-origin receipts for this session."""

        return canonical_hash(
            {"session_id": self._session_id},
            domain=METHOD_SESSION_BINDING_HASH_DOMAIN,
        )

    @property
    def used_bytes(self) -> int:
        return self._used_bytes

    @property
    def record_count(self) -> int:
        return len(self._plans) + len(self._steps) + len(self._runs)

    def _require_open(self) -> None:
        if self._closed:
            raise MethodRecordStoreError(MethodRecordStoreErrorCode.CLOSED)

    def _reserve(self, size: int) -> None:
        if size > self._limits.record_bytes:
            raise MethodRecordStoreError(MethodRecordStoreErrorCode.RECORD_TOO_LARGE)
        if self.record_count >= self._limits.records:
            raise MethodRecordStoreError(MethodRecordStoreErrorCode.RECORD_COUNT_EXCEEDED)
        if self._used_bytes + size > self._limits.session_bytes:
            raise MethodRecordStoreError(MethodRecordStoreErrorCode.SESSION_FULL)

    def _publish(
        self,
        reference: str,
        stored: StoredModel,
        destination: dict[str, StoredModel],
    ) -> str:
        existing = destination.get(reference)
        if existing is not None:
            if existing.canonical_bytes != stored.canonical_bytes:
                raise MethodRecordStoreError(MethodRecordStoreErrorCode.PUBLICATION_CONFLICT)
            return reference
        self._reserve(len(stored.canonical_bytes))
        destination[reference] = stored
        self._used_bytes += len(stored.canonical_bytes)
        self._scope_registry.register(self._session_id, reference)
        return reference

    def publish_plan(self, record: PlanRecord) -> str:
        """Publish one exact compiled-plan record; identical retries are idempotent."""

        with self._lock:
            self._require_open()
            validated, content = _validate_and_encode(record, PlanRecord)
            reference = validated.ref.reference
            return self._publish(
                reference,
                StoredPlan(reference, validated, content),
                self._plans,
            )

    def publish_step(self, record: StepRecord) -> str:
        """Publish an exact step only after its plan exists in this session."""

        with self._lock:
            self._require_open()
            validated, content = _validate_and_encode(record, StepRecord)
            if validated.plan.reference not in self._plans:
                raise MethodRecordStoreError(MethodRecordStoreErrorCode.DEPENDENCY_MISSING)
            reference = validated.ref.reference
            return self._publish(
                reference,
                StoredStep(reference, validated, content),
                self._steps,
            )

    def publish_run(self, record: RunRecord) -> str:
        """Publish a complete run after its exact plan and every step were published."""

        with self._lock:
            self._require_open()
            validated, content = _validate_and_encode(record, RunRecord)
            plan_reference = validated.plan_record.ref.reference
            stored_plan = self._plans.get(plan_reference)
            if stored_plan is None or stored_plan.record != validated.plan_record:
                raise MethodRecordStoreError(MethodRecordStoreErrorCode.DEPENDENCY_MISSING)
            for step in validated.steps:
                stored_step = self._steps.get(step.ref.reference)
                if stored_step is None or stored_step.record != step:
                    raise MethodRecordStoreError(MethodRecordStoreErrorCode.DEPENDENCY_MISSING)
            reference = validated.ref.reference
            return self._publish(
                reference,
                StoredRun(reference, validated, content),
                self._runs,
            )

    def _get(
        self,
        reference: str,
        source: Mapping[str, StoredModel],
    ) -> StoredModel:
        self._require_open()
        stored = source.get(reference)
        if stored is not None:
            return stored
        if self._scope_registry.known_outside(self._session_id, reference):
            raise MethodRecordStoreError(MethodRecordStoreErrorCode.REFERENCE_SCOPE_DENIED)
        raise MethodRecordStoreError(MethodRecordStoreErrorCode.REFERENCE_NOT_FOUND)

    def get_plan(self, reference: str | PlanRef) -> PlanRecord:
        """Retrieve one immutable plan from this exact session."""

        with self._lock:
            value = _reference_value(reference, prefix="dqplan")
            return self._get(value, self._plans).record

    def get_step(self, reference: str | StepRef) -> StepRecord:
        """Retrieve a step for trusted host assembly; this is not a public agent tool."""

        with self._lock:
            value = _reference_value(reference, prefix="dqstep")
            return self._get(value, self._steps).record

    def get_run(self, reference: str | RunRef) -> RunRecord:
        """Retrieve one complete immutable run from this exact session."""

        with self._lock:
            value = _reference_value(reference, prefix="dqrun")
            return self._get(value, self._runs).record

    def record_bytes(self, reference: str | PlanRef | StepRef | RunRef) -> bytes:
        """Return the exact protocol canonical bytes retained for one published record."""

        with self._lock:
            value = _reference_value(reference)
            if value.startswith("dqplan:"):
                return bytes(self._get(value, self._plans).canonical_bytes)
            if value.startswith("dqstep:"):
                return bytes(self._get(value, self._steps).canonical_bytes)
            return bytes(self._get(value, self._runs).canonical_bytes)

    def close(self) -> None:
        """Expire every reference and release all retained record bytes."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._scope_registry.unregister_session(self._session_id)
            self._plans.clear()
            self._steps.clear()
            self._runs.clear()
            self._used_bytes = 0

    def __enter__(self) -> MethodRecordStore:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


__all__ = [
    "MAX_METHOD_RECORD_BYTES",
    "MAX_METHOD_SESSION_BYTES",
    "MAX_METHOD_SESSION_RECORDS",
    "METHOD_SESSION_BINDING_HASH_DOMAIN",
    "MethodRecordLimits",
    "MethodRecordScopeRegistry",
    "MethodRecordStore",
    "MethodRecordStoreError",
    "MethodRecordStoreErrorCode",
    "StoredPlan",
    "StoredRun",
    "StoredStep",
]
