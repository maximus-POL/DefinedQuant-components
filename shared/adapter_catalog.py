"""Host-owned availability construction and trusted exact-adapter loading."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, TypeAlias, cast

from defined_quant.adapter_artifacts import (
    ArtifactAttestation,
    ArtifactVerificationError,
    reverify_installed_artifact,
)
from defined_quant.adapter_discovery import InstalledAdapterDiscovery
from defined_quant.policy_evaluation import evaluate_policy_admission
from defined_quant.registry import ProtocolRegistry
from defined_quant_protocol import (
    AdapterExecutionFailure,
    AdapterExecutionRequest,
    AdapterExecutionSuccess,
    AdapterSpec,
    ArtifactPin,
    AvailabilitySnapshot,
    BackendRole,
    ExecutionFailure,
    ExecutionFailureCode,
    FailureFact,
    ImplementationAvailability,
    ImplementationRef,
    ImplementationSpec,
    InstallationStatus,
    RequirementStatus,
    ResolutionPolicy,
    RuntimeIdentity,
)
from defined_quant_protocol import (
    ImplementationAvailabilityReason as AvailabilityReason,
)
from defined_quant_protocol import (
    ImplementationAvailabilityStatus as AvailabilityStatus,
)
from defined_quant_protocol.registry import derive_availability_requirements
from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue

AdapterCallable: TypeAlias = Callable[
    [Mapping[str, object]],
    Mapping[str, JsonValue],
]

_SAFE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _safe_exception_attribute(error: Exception, name: str) -> object:
    try:
        return getattr(error, name, None)
    except Exception:
        return None


class _ClosedHostModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )


class HostAvailabilityAssessment(_ClosedHostModel):
    """Explicit host facts used to construct one public implementation availability."""

    schema_version: Literal[1] = 1
    implementation: ImplementationRef
    enabled: bool
    artifact_attestation: ArtifactAttestation | None
    dependencies: RequirementStatus
    credentials: RequirementStatus
    licence: RequirementStatus
    entitlement: RequirementStatus
    transport: RequirementStatus
    reachability: RequirementStatus


class AdapterCatalogError(RuntimeError):
    """Safe closed failure at the trusted adapter-loading boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _adapter_for(
    registry: ProtocolRegistry,
    implementation: ImplementationSpec,
) -> AdapterSpec:
    selected = tuple(item for item in registry.adapters if item.ref == implementation.adapter)
    if len(selected) != 1:
        raise ValueError("implementation requires one exact registered adapter")
    adapter = selected[0]
    if implementation.artifact.distribution != adapter.distribution:
        raise ValueError("implementation artifact distribution must match its adapter")
    return adapter


def _requirement_status(
    status: RequirementStatus,
    *,
    required: bool,
    name: str,
) -> None:
    if required and status == RequirementStatus.NOT_REQUIRED:
        raise ValueError(f"{name} cannot be not_required for this implementation")
    if not required and status != RequirementStatus.NOT_REQUIRED:
        raise ValueError(f"{name} must be not_required for this implementation")


def _artifact_status(
    attestation: ArtifactAttestation | None,
    adapter: AdapterSpec,
    expected: ArtifactPin,
) -> RequirementStatus:
    if attestation is None:
        return RequirementStatus.UNKNOWN
    if attestation.adapter == adapter.ref and attestation.artifact == expected:
        return RequirementStatus.SATISFIED
    return RequirementStatus.UNAVAILABLE


def _availability_outcome(
    *,
    enabled: bool,
    installation: InstallationStatus,
    artifact: RequirementStatus,
    checks: Sequence[tuple[RequirementStatus, AvailabilityReason]],
) -> tuple[AvailabilityStatus, tuple[AvailabilityReason, ...]]:
    hard_reasons: set[AvailabilityReason] = set()
    unknown = False
    if not enabled:
        hard_reasons.add(AvailabilityReason.DISABLED)
    if installation == InstallationStatus.NOT_INSTALLED:
        hard_reasons.add(AvailabilityReason.NOT_INSTALLED)
    elif installation == InstallationStatus.MISMATCH:
        hard_reasons.add(AvailabilityReason.ARTIFACT_MISMATCH)
    elif installation == InstallationStatus.UNKNOWN:
        unknown = True
    for state, reason in (
        (artifact, AvailabilityReason.ARTIFACT_MISMATCH),
        *checks,
    ):
        if state == RequirementStatus.UNAVAILABLE:
            hard_reasons.add(reason)
        elif state == RequirementStatus.UNKNOWN:
            unknown = True
    if hard_reasons:
        return (
            AvailabilityStatus.UNAVAILABLE,
            tuple(sorted(hard_reasons, key=lambda item: item.value)),
        )
    if unknown:
        return AvailabilityStatus.UNKNOWN, (AvailabilityReason.STATUS_UNKNOWN,)
    return AvailabilityStatus.AVAILABLE, (AvailabilityReason.READY,)


def build_availability_snapshot(
    *,
    registry: ProtocolRegistry,
    installed: InstalledAdapterDiscovery,
    assessments: Sequence[HostAvailabilityAssessment],
    snapshot_id: str,
    observed_at: AwareDatetime,
    evaluator: RuntimeIdentity,
) -> AvailabilitySnapshot:
    """Build an immutable, non-secret availability snapshot from explicit host facts."""

    by_hash: dict[str, HostAvailabilityAssessment] = {}
    for assessment in assessments:
        spec_hash = assessment.implementation.spec_hash
        if spec_hash in by_hash:
            raise ValueError("host availability assessments must be unique")
        by_hash[spec_hash] = assessment
    expected = {item.ref.spec_hash for item in registry.implementations}
    if set(by_hash) != expected:
        raise ValueError("host availability requires one assessment per implementation")

    results: list[ImplementationAvailability] = []
    for implementation in registry.implementations:
        assessment = by_hash[implementation.ref.spec_hash]
        if assessment.implementation != implementation.ref:
            raise ValueError("host availability assessment binds the wrong implementation")
        adapter = _adapter_for(registry, implementation)
        requirements = derive_availability_requirements(
            implementation,
            registry.backends,
        )
        _requirement_status(
            assessment.dependencies,
            required=requirements.dependencies_required,
            name="dependencies",
        )
        _requirement_status(
            assessment.credentials,
            required=requirements.credentials_required,
            name="credentials",
        )
        _requirement_status(
            assessment.licence,
            required=requirements.licence_required,
            name="licence",
        )
        _requirement_status(
            assessment.entitlement,
            required=requirements.entitlement_required,
            name="entitlement",
        )
        _requirement_status(
            assessment.reachability,
            required=requirements.reachability_required,
            name="reachability",
        )
        if assessment.transport == RequirementStatus.NOT_REQUIRED:
            raise ValueError("transport requires an explicit operational status")

        installation = installed.installation(adapter, implementation.artifact)
        artifact = _artifact_status(
            assessment.artifact_attestation,
            adapter,
            implementation.artifact,
        )
        checks = (
            (
                assessment.dependencies,
                AvailabilityReason.DEPENDENCY_UNAVAILABLE,
            ),
            (
                assessment.credentials,
                AvailabilityReason.CREDENTIAL_CAPABILITY_UNAVAILABLE,
            ),
            (assessment.licence, AvailabilityReason.LICENCE_UNAVAILABLE),
            (
                assessment.entitlement,
                AvailabilityReason.ENTITLEMENT_UNAVAILABLE,
            ),
            (assessment.transport, AvailabilityReason.TRANSPORT_UNAVAILABLE),
            (
                assessment.reachability,
                AvailabilityReason.REACHABILITY_UNAVAILABLE,
            ),
        )
        status, reasons = _availability_outcome(
            enabled=assessment.enabled,
            installation=installation,
            artifact=artifact,
            checks=checks,
        )
        results.append(
            ImplementationAvailability(
                implementation=implementation.ref,
                adapter=adapter.ref,
                backend_bindings=implementation.backend_bindings,
                enabled=assessment.enabled,
                installation=installation,
                artifact=artifact,
                dependencies=assessment.dependencies,
                credentials=assessment.credentials,
                licence=assessment.licence,
                entitlement=assessment.entitlement,
                transport=assessment.transport,
                reachability=assessment.reachability,
                status=status,
                reasons=reasons,
            )
        )
    return AvailabilitySnapshot(
        snapshot_id=snapshot_id,
        observed_at=observed_at,
        evaluator=evaluator,
        implementations=tuple(
            sorted(
                results,
                key=lambda item: (
                    item.implementation.id,
                    item.implementation.version,
                    item.implementation.spec_hash,
                ),
            )
        ),
    )


class TrustedAdapterCatalog:
    """Fail-closed loader for one exact policy-admitted, attested implementation."""

    def __init__(
        self,
        *,
        registry: ProtocolRegistry,
        installed: InstalledAdapterDiscovery,
        policy: ResolutionPolicy,
        artifact_attestations: Sequence[ArtifactAttestation],
        runtime: RuntimeIdentity,
    ) -> None:
        self._registry = registry
        self._installed = installed
        self._policy = policy
        self._runtime = runtime
        self._implementations = {item.ref.spec_hash: item for item in registry.implementations}
        self._backends = {item.ref.spec_hash: item for item in registry.backends}
        self._attestations: dict[tuple[str, str, str, str], ArtifactAttestation] = {}
        for attestation in artifact_attestations:
            pin = attestation.artifact
            key = (
                attestation.adapter.spec_hash,
                pin.distribution,
                pin.version,
                pin.artifact_hash,
            )
            if key in self._attestations:
                raise ValueError("artifact attestations must be unique")
            self._attestations[key] = attestation
        if len(self._implementations) != len(registry.implementations):
            raise ValueError("registry implementation identities must be unique")
        for implementation in registry.implementations:
            _adapter_for(registry, implementation)

    def load(
        self,
        implementation: ImplementationRef,
        *,
        availability: ImplementationAvailability,
    ) -> AdapterCallable:
        """Load only after all exact registry, policy, availability, and artifact checks pass."""

        selected = self._implementations.get(implementation.spec_hash)
        if selected is None or selected.ref != implementation:
            raise AdapterCatalogError(
                "implementation_not_registered",
                "The exact implementation is not registered.",
            )
        adapter = _adapter_for(self._registry, selected)
        policy_facts = evaluate_policy_admission(
            selected,
            self._policy,
            backends=self._backends,
        )
        if not policy_facts.admitted:
            raise AdapterCatalogError(
                "implementation_not_admitted",
                "The active policy does not admit this exact implementation.",
            )
        if (
            availability.implementation != selected.ref
            or availability.adapter != adapter.ref
            or availability.backend_bindings != selected.backend_bindings
            or availability.status != AvailabilityStatus.AVAILABLE
            or availability.installation != InstallationStatus.INSTALLED
            or availability.artifact != RequirementStatus.SATISFIED
        ):
            raise AdapterCatalogError(
                "implementation_unavailable",
                "The exact implementation is not explicitly available.",
            )
        artifact_key = (
            adapter.ref.spec_hash,
            selected.artifact.distribution,
            selected.artifact.version,
            selected.artifact.artifact_hash,
        )
        artifact_attestation = self._attestations.get(artifact_key)
        if (
            artifact_attestation is None
            or artifact_attestation.adapter != adapter.ref
            or artifact_attestation.artifact != selected.artifact
        ):
            raise AdapterCatalogError(
                "artifact_attestation_mismatch",
                "The verified artifact does not match the registered pin.",
            )
        if (
            self._installed.installation(adapter, selected.artifact)
            != InstallationStatus.INSTALLED
        ):
            raise AdapterCatalogError(
                "adapter_installation_mismatch",
                "The exact adapter distribution, version, and dispatch key are not installed.",
            )
        try:
            reverify_installed_artifact(
                attestation=artifact_attestation,
                installed=self._installed,
                adapter=adapter,
                artifact=selected.artifact,
            )
        except ArtifactVerificationError as exc:
            raise AdapterCatalogError(
                "artifact_attestation_mismatch",
                "The installed adapter artifact changed after verification.",
            ) from exc
        try:
            entry_point = self._installed._entry_point_for(adapter, selected.artifact)
        except LookupError as exc:
            raise AdapterCatalogError(
                "adapter_installation_mismatch",
                "The exact adapter entry point is unavailable.",
            ) from exc
        try:
            loaded = entry_point.load()
        except Exception as exc:
            raise AdapterCatalogError(
                "adapter_load_failed",
                "The trusted adapter could not be loaded.",
            ) from exc
        if not callable(loaded):
            raise AdapterCatalogError(
                "adapter_not_callable",
                "The trusted adapter entry point is not callable.",
            )
        return cast(AdapterCallable, loaded)

    def invoke(
        self,
        request: AdapterExecutionRequest,
    ) -> AdapterExecutionSuccess | AdapterExecutionFailure:
        """Invoke exactly the request-bound implementation without selection or fallback."""

        selected = self._implementations.get(request.implementation.spec_hash)
        runtime_binding = None
        if selected is not None:
            runtime_binding = next(
                (item for item in selected.backend_bindings if item.role == BackendRole.RUNTIME),
                None,
            )
        if (
            selected is None
            or selected.ref != request.implementation
            or selected.capability != request.capability
            or selected.adapter != request.adapter
            or selected.backend_bindings != request.backend_bindings
            or runtime_binding is None
            or runtime_binding.transport != request.transport
            or runtime_binding.locality != request.locality
        ):
            return self._failure_result(
                request,
                code=ExecutionFailureCode.INTERNAL_FAILURE,
                message=("The adapter request does not bind one exact registered implementation."),
            )

        try:
            adapter = self.load(
                request.implementation,
                availability=request.execution_availability,
            )
        except AdapterCatalogError as exc:
            failure_code = {
                "implementation_not_admitted": ExecutionFailureCode.POLICY_DRIFT,
                "implementation_unavailable": (ExecutionFailureCode.IMPLEMENTATION_UNAVAILABLE),
                "artifact_attestation_mismatch": ExecutionFailureCode.ARTIFACT_MISMATCH,
            }.get(exc.code, ExecutionFailureCode.ADAPTER_UNAVAILABLE)
            return self._failure_result(
                request,
                code=failure_code,
                message={
                    ExecutionFailureCode.POLICY_DRIFT: (
                        "The compiled implementation is not admitted by the active policy."
                    ),
                    ExecutionFailureCode.IMPLEMENTATION_UNAVAILABLE: (
                        "The exact compiled implementation is unavailable."
                    ),
                    ExecutionFailureCode.ARTIFACT_MISMATCH: (
                        "The installed adapter artifact does not match the registered pin."
                    ),
                    ExecutionFailureCode.ADAPTER_UNAVAILABLE: (
                        "The exact trusted adapter is unavailable."
                    ),
                }[failure_code],
            )

        try:
            output = adapter(request.canonical_inputs)
        except Exception as exc:
            return self._adapter_failure(request, exc)
        if not isinstance(output, Mapping):
            return self._failure_result(
                request,
                code=ExecutionFailureCode.OUTPUT_MAPPING_FAILED,
                message="The adapter did not return a canonical output object.",
            )
        try:
            canonical_output = cast(dict[str, JsonValue], dict(output))
            return AdapterExecutionSuccess(
                request_hash=request.request_hash,
                adapter_runtime=self._runtime,
                canonical_output=canonical_output,
            )
        except Exception:
            return self._failure_result(
                request,
                code=ExecutionFailureCode.OUTPUT_MAPPING_FAILED,
                message="The adapter returned an invalid canonical output object.",
            )

    def _adapter_failure(
        self,
        request: AdapterExecutionRequest,
        error: Exception,
    ) -> AdapterExecutionFailure:
        adapter_code = _safe_exception_attribute(error, "code")
        mapped = {
            "authentication_unavailable": ExecutionFailureCode.AUTHENTICATION_UNAVAILABLE,
            "licence_unavailable": ExecutionFailureCode.LICENCE_UNAVAILABLE,
            "entitlement_unavailable": ExecutionFailureCode.ENTITLEMENT_UNAVAILABLE,
            "rate_limited": ExecutionFailureCode.RATE_LIMITED,
            "timeout": ExecutionFailureCode.TIMEOUT,
            "malformed_backend_response": (ExecutionFailureCode.MALFORMED_BACKEND_RESPONSE),
            "output_mapping_failed": ExecutionFailureCode.OUTPUT_MAPPING_FAILED,
            "non_finite_result": ExecutionFailureCode.OUTPUT_MAPPING_FAILED,
            "data_handling_refused": ExecutionFailureCode.DATA_HANDLING_REFUSED,
            "dependency_failed": ExecutionFailureCode.DEPENDENCY_FAILED,
            "cancelled": ExecutionFailureCode.CANCELLED,
        }
        if isinstance(adapter_code, str) and adapter_code in mapped:
            code = mapped[adapter_code]
        elif isinstance(adapter_code, str) and _SAFE_ID_PATTERN.fullmatch(adapter_code):
            code = ExecutionFailureCode.INPUT_MAPPING_FAILED
        else:
            code = ExecutionFailureCode.INTERNAL_FAILURE
            adapter_code = None
        messages = {
            ExecutionFailureCode.AUTHENTICATION_UNAVAILABLE: (
                "Provider authentication is unavailable."
            ),
            ExecutionFailureCode.LICENCE_UNAVAILABLE: "The required licence is unavailable.",
            ExecutionFailureCode.ENTITLEMENT_UNAVAILABLE: (
                "The required entitlement is unavailable."
            ),
            ExecutionFailureCode.RATE_LIMITED: "The backend rate limit was reached.",
            ExecutionFailureCode.TIMEOUT: "The backend invocation timed out.",
            ExecutionFailureCode.MALFORMED_BACKEND_RESPONSE: (
                "The backend response was malformed."
            ),
            ExecutionFailureCode.INPUT_MAPPING_FAILED: (
                "Canonical inputs could not be mapped to the adapter."
            ),
            ExecutionFailureCode.OUTPUT_MAPPING_FAILED: (
                "The adapter could not produce a canonical output."
            ),
            ExecutionFailureCode.DATA_HANDLING_REFUSED: (
                "The adapter refused the requested data handling."
            ),
            ExecutionFailureCode.DEPENDENCY_FAILED: "An adapter dependency failed.",
            ExecutionFailureCode.CANCELLED: "The adapter invocation was cancelled.",
            ExecutionFailureCode.INTERNAL_FAILURE: "The adapter invocation failed safely.",
        }
        retryable = bool(
            _safe_exception_attribute(error, "retry_allowed") is True
            and code in {ExecutionFailureCode.RATE_LIMITED, ExecutionFailureCode.TIMEOUT}
        )
        facts: tuple[FailureFact, ...] = ()
        if isinstance(adapter_code, str) and _SAFE_ID_PATTERN.fullmatch(adapter_code):
            facts = (FailureFact(key="adapter_code", value=adapter_code),)
        return self._failure_result(
            request,
            code=code,
            message=messages[code],
            retryable=retryable,
            facts=facts,
        )

    def _failure_result(
        self,
        request: AdapterExecutionRequest,
        *,
        code: ExecutionFailureCode,
        message: str,
        retryable: bool = False,
        facts: tuple[FailureFact, ...] = (),
    ) -> AdapterExecutionFailure:
        return AdapterExecutionFailure(
            request_hash=request.request_hash,
            adapter_runtime=self._runtime,
            failure=ExecutionFailure(
                code=code,
                message=message,
                retryable=retryable,
                facts=facts,
            ),
        )


__all__ = [
    "AdapterCallable",
    "AdapterCatalogError",
    "ArtifactAttestation",
    "HostAvailabilityAssessment",
    "TrustedAdapterCatalog",
    "build_availability_snapshot",
]
