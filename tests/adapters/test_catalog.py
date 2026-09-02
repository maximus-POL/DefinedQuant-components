from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from defined_quant.adapter_artifacts import (
    ArtifactAttestation,
    verify_installed_artifact,
)
from defined_quant.adapter_catalog import (
    AdapterCatalogError,
    HostAvailabilityAssessment,
    TrustedAdapterCatalog,
    build_availability_snapshot,
)
from defined_quant.adapter_discovery import (
    ADAPTER_ENTRY_POINT_GROUP,
    discover_installed_adapters,
)
from defined_quant.registry import ProtocolRegistry, load_registry
from defined_quant_protocol import (
    AdapterExecutionFailure,
    AdapterExecutionRequest,
    AdapterExecutionSuccess,
    BackendRole,
    ExecutionFailureCode,
    ImplementationRef,
    InstallationStatus,
    PlanRef,
    RequirementStatus,
    RuntimeIdentity,
)
from defined_quant_protocol import (
    ImplementationAvailabilityReason as AvailabilityReason,
)
from defined_quant_protocol import (
    ImplementationAvailabilityStatus as AvailabilityStatus,
)
from defined_quant_protocol.resolution import (
    BackendRolePolicy,
    CapabilityPolicyRule,
    PolicyImplementation,
    ResolutionPolicy,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
ADAPTER_SOURCE = ROOT / "adapters/dq_native/src"
MANIFEST_PATH = ROOT / "registry/artifacts/defined_quant_adapter_dq_native/manifest.json"


class FakeEntryPoint:
    def __init__(
        self,
        *,
        name: str,
        loader: Callable[[], Any],
        group: str = ADAPTER_ENTRY_POINT_GROUP,
        value: str = "defined_quant_adapter_dq_native.simple_return.adapter:execute",
    ) -> None:
        self.group = group
        self.name = name
        self.value = value
        self._loader = loader
        self.load_count = 0

    def load(self) -> Any:
        self.load_count += 1
        return self._loader()


class FakeDistribution:
    def __init__(
        self,
        *,
        name: str,
        version: str,
        entry_points: tuple[FakeEntryPoint, ...],
        root: Path = ADAPTER_SOURCE,
    ) -> None:
        self.metadata = {"Name": name}
        self.version = version
        self.entry_points = entry_points
        self._root = root
        self.files = tuple(PurePosixPath(item["path"]) for item in _manifest()["members"])

    def locate_file(self, path: object) -> Path:
        return self._root / str(path)


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _registry() -> ProtocolRegistry:
    return load_registry(root=ROOT / "registry").as_protocol_registry()


def _records() -> tuple[ProtocolRegistry, Any, Any, Any, Any]:
    registry = _registry()
    capability = next(item for item in registry.capabilities if item.id == "returns.simple")
    backend = registry.backends[0]
    adapter = next(item for item in registry.adapters if item.id == "dq_native.simple_return")
    implementation = next(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )
    return registry, capability, backend, adapter, implementation


def _entry_point() -> FakeEntryPoint:
    def load_adapter() -> Any:
        from defined_quant_adapter_dq_native.simple_return.adapter import execute

        return execute

    return FakeEntryPoint(name="dq_native_simple_return", loader=load_adapter)


def _entry_points(simple_return: FakeEntryPoint | None = None) -> tuple[FakeEntryPoint, ...]:
    selected = _entry_point() if simple_return is None else simple_return
    result: list[FakeEntryPoint] = []
    for item in _manifest()["entry_points"]:
        if item["name"] == "dq_native_simple_return":
            result.append(selected)
        else:
            result.append(
                FakeEntryPoint(
                    name=item["name"],
                    value=item["value"],
                    loader=lambda: object(),
                )
            )
    return tuple(result)


def _installed(entry_point: FakeEntryPoint | None = None) -> Any:
    selected = _entry_point() if entry_point is None else entry_point
    return discover_installed_adapters(
        distributions=(
            FakeDistribution(
                name="defined-quant-adapter-dq-native",
                version="1.0.0",
                entry_points=_entry_points(selected),
            ),
        )
    )


def _attestation(installed: Any, adapter: Any, implementation: Any) -> ArtifactAttestation:
    return verify_installed_artifact(
        installed=installed,
        adapter=adapter,
        artifact=implementation.artifact,
        expected_manifest=_manifest(),
    )


def _assessment(
    implementation: Any,
    *,
    attestation: ArtifactAttestation | None = None,
) -> HostAvailabilityAssessment:
    return HostAvailabilityAssessment(
        implementation=implementation.ref,
        enabled=True,
        artifact_attestation=attestation,
        dependencies=RequirementStatus.NOT_REQUIRED,
        credentials=RequirementStatus.NOT_REQUIRED,
        licence=RequirementStatus.NOT_REQUIRED,
        entitlement=RequirementStatus.NOT_REQUIRED,
        transport=RequirementStatus.SATISFIED,
        reachability=RequirementStatus.NOT_REQUIRED,
    )


def _availability_snapshot(
    *,
    registry: ProtocolRegistry,
    installed: Any,
    attested_adapter: Any | None = None,
    attestation: ArtifactAttestation | None = None,
) -> Any:
    assessments = tuple(
        _assessment(
            implementation,
            attestation=(
                attestation
                if attested_adapter is not None
                and implementation.adapter == attested_adapter.ref
                else None
            ),
        )
        for implementation in registry.implementations
    )
    return build_availability_snapshot(
        registry=registry,
        installed=installed,
        assessments=assessments,
        snapshot_id="local_test",
        observed_at=NOW,
        evaluator=_runtime(),
    )


def _implementation_availability(snapshot: Any, implementation: Any) -> Any:
    return next(
        item
        for item in snapshot.implementations
        if item.implementation == implementation.ref
    )


def _runtime() -> RuntimeIdentity:
    return RuntimeIdentity(
        name="availability_host",
        version="1.0.0",
        artifact_hash="a" * 64,
    )


def _policy(*, admitted: bool = True, allow_transport: bool = True) -> ResolutionPolicy:
    registry, capability, backend, _, implementation = _records()
    del registry
    implementation_ref = implementation.ref
    if not admitted:
        implementation_ref = ImplementationRef(
            id="dq_native.unregistered",
            version="1.0.0",
            spec_hash="f" * 64,
        )
    role = implementation.backend_bindings[0]
    return ResolutionPolicy(
        id="local_test",
        version="1.0.0",
        capability_rules=(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=(
                    PolicyImplementation(
                        implementation=implementation_ref,
                        priority=0,
                    ),
                ),
                backend_roles=(
                    BackendRolePolicy(
                        role=BackendRole.RUNTIME,
                        allowed_backends=(backend.ref,),
                        allowed_kinds=(backend.kind,),
                        allowed_transports=(
                            (role.transport,) if allow_transport else ("subprocess",)
                        ),
                        allowed_localities=(role.locality,),
                        network_allowed=False,
                        allowed_data_egress=(backend.data_boundary.egress,),
                    ),
                ),
                required_trust_dimensions=tuple(
                    sorted(
                        (item.dimension for item in implementation.trust),
                        key=lambda item: item.value,
                    )
                ),
            ),
        ),
    )


def _snapshot(*, attested: bool = True) -> Any:
    registry, _, _, adapter, implementation = _records()
    installed = _installed()
    attestation = _attestation(installed, adapter, implementation) if attested else None
    return _availability_snapshot(
        registry=registry,
        installed=installed,
        attested_adapter=adapter,
        attestation=attestation,
    )


def test_discovery_reads_metadata_without_loading_entry_points() -> None:
    entry_point = _entry_point()
    registry, _, _, adapter, implementation = _records()
    del registry

    discovered = _installed(entry_point)

    assert entry_point.load_count == 0
    assert len(discovered.descriptors) == 9
    simple_descriptor = next(
        item for item in discovered.descriptors if item.dispatch_key == "dq_native_simple_return"
    )
    assert simple_descriptor == simple_descriptor.__class__(
        distribution="defined-quant-adapter-dq-native",
        version="1.0.0",
        dispatch_key="dq_native_simple_return",
        entry_point_count=1,
    )
    assert (
        discovered.installation(adapter, implementation.artifact)
        == InstallationStatus.INSTALLED
    )
    assert "simple_return.adapter" not in repr(discovered.descriptors)


@pytest.mark.parametrize(
    ("name", "version", "dispatch_key", "expected"),
    [
        (
            "another-distribution",
            "1.0.0",
            "dq_native_simple_return",
            InstallationStatus.NOT_INSTALLED,
        ),
        (
            "defined-quant-adapter-dq-native",
            "2.0.0",
            "dq_native_simple_return",
            InstallationStatus.MISMATCH,
        ),
        (
            "defined-quant-adapter-dq-native",
            "1.0.0",
            "another_dispatch",
            InstallationStatus.MISMATCH,
        ),
    ],
)
def test_installation_requires_exact_distribution_version_and_dispatch_key(
    name: str,
    version: str,
    dispatch_key: str,
    expected: InstallationStatus,
) -> None:
    _, _, _, adapter, implementation = _records()
    entry_point = FakeEntryPoint(name=dispatch_key, loader=lambda: object())
    discovered = discover_installed_adapters(
        distributions=(
            FakeDistribution(
                name=name,
                version=version,
                entry_points=(entry_point,),
            ),
        )
    )

    assert discovered.installation(adapter, implementation.artifact) == expected
    assert entry_point.load_count == 0


def test_duplicate_exact_entry_points_fail_closed_as_mismatch() -> None:
    _, _, _, adapter, implementation = _records()
    first = _entry_point()
    second = _entry_point()
    discovered = discover_installed_adapters(
        distributions=(
            FakeDistribution(
                name="defined-quant-adapter-dq-native",
                version="1.0.0",
                entry_points=(first, second),
            ),
        )
    )

    assert (
        discovered.installation(adapter, implementation.artifact)
        == InstallationStatus.MISMATCH
    )
    assert discovered.descriptors[0].entry_point_count == 2
    assert first.load_count == second.load_count == 0


def test_duplicate_exact_distributions_fail_closed_as_mismatch() -> None:
    _, _, _, adapter, implementation = _records()
    first = _entry_point()
    discovered = discover_installed_adapters(
        distributions=(
            FakeDistribution(
                name="defined-quant-adapter-dq-native",
                version="1.0.0",
                entry_points=(first,),
            ),
            FakeDistribution(
                name="defined-quant-adapter-dq-native",
                version="1.0.0",
                entry_points=(),
            ),
        )
    )

    assert (
        discovered.installation(adapter, implementation.artifact)
        == InstallationStatus.MISMATCH
    )
    assert first.load_count == 0


def test_availability_is_explicit_and_installation_does_not_imply_artifact_trust() -> None:
    registry, _, _, _, implementation = _records()
    snapshot = _availability_snapshot(
        registry=registry,
        installed=_installed(),
    )
    availability = _implementation_availability(snapshot, implementation)

    assert availability.installation == InstallationStatus.INSTALLED
    assert availability.artifact == RequirementStatus.UNKNOWN
    assert availability.dependencies == RequirementStatus.NOT_REQUIRED
    assert availability.credentials == RequirementStatus.NOT_REQUIRED
    assert availability.licence == RequirementStatus.NOT_REQUIRED
    assert availability.entitlement == RequirementStatus.NOT_REQUIRED
    assert availability.transport == RequirementStatus.SATISFIED
    assert availability.reachability == RequirementStatus.NOT_REQUIRED
    assert availability.status == AvailabilityStatus.UNKNOWN
    assert availability.reasons == (AvailabilityReason.STATUS_UNKNOWN,)


def test_availability_refuses_secret_or_undeclared_host_fields() -> None:
    _, _, _, _, implementation = _records()
    payload = _assessment(implementation).model_dump(mode="python")
    payload["token"] = "must-not-enter-availability"

    with pytest.raises(ValidationError):
        HostAvailabilityAssessment.model_validate(payload)


def test_trusted_catalog_checks_policy_availability_and_attestation_before_loading() -> None:
    registry, _, _, adapter, implementation = _records()
    entry_point = _entry_point()
    installed = _installed(entry_point)
    attestation = _attestation(installed, adapter, implementation)
    available = _implementation_availability(
        _availability_snapshot(
        registry=registry,
        installed=installed,
        attested_adapter=adapter,
        attestation=attestation,
        ),
        implementation,
    )

    denied_catalog = TrustedAdapterCatalog(
        registry=registry,
        installed=installed,
        policy=_policy(admitted=False),
        artifact_attestations=(attestation,),
        runtime=_runtime(),
    )
    with pytest.raises(AdapterCatalogError) as denied:
        denied_catalog.load(
            implementation.ref,
            availability=available,
        )
    assert denied.value.code == "implementation_not_admitted"
    assert entry_point.load_count == 0

    unattested_catalog = TrustedAdapterCatalog(
        registry=registry,
        installed=installed,
        policy=_policy(),
        artifact_attestations=(),
        runtime=_runtime(),
    )
    with pytest.raises(AdapterCatalogError) as mismatched:
        unattested_catalog.load(
            implementation.ref,
            availability=available,
        )
    assert mismatched.value.code == "artifact_attestation_mismatch"
    assert entry_point.load_count == 0

    catalog = TrustedAdapterCatalog(
        registry=registry,
        installed=installed,
        policy=_policy(),
        artifact_attestations=(attestation,),
        runtime=_runtime(),
    )
    unknown = _implementation_availability(_snapshot(attested=False), implementation)
    with pytest.raises(AdapterCatalogError) as unavailable:
        catalog.load(
            implementation.ref,
            availability=unknown,
        )
    assert unavailable.value.code == "implementation_unavailable"
    assert entry_point.load_count == 0

    execute = catalog.load(
        implementation.ref,
        availability=available,
    )
    assert entry_point.load_count == 1
    assert execute({"prices": [100.0, 105.0]}) == {
        "returns": [0.05],
        "return_kind": "simple",
        "return_timestamps": None,
        "ordering_status": "unverified",
    }


def test_policy_backend_transport_mismatch_prevents_loading() -> None:
    registry, _, _, adapter, implementation = _records()
    entry_point = _entry_point()
    installed = _installed(entry_point)
    attestation = _attestation(installed, adapter, implementation)
    catalog = TrustedAdapterCatalog(
        registry=registry,
        installed=installed,
        policy=_policy(allow_transport=False),
        artifact_attestations=(attestation,),
        runtime=_runtime(),
    )
    available = _implementation_availability(
        _availability_snapshot(
        registry=registry,
        installed=installed,
        attested_adapter=adapter,
        attestation=attestation,
        ),
        implementation,
    )

    with pytest.raises(AdapterCatalogError) as denied:
        catalog.load(
            implementation.ref,
            availability=available,
        )

    assert denied.value.code == "implementation_not_admitted"
    assert entry_point.load_count == 0


def test_catalog_reverifies_attested_resources_immediately_before_loading(
    tmp_path: Path,
) -> None:
    registry, _, _, adapter, implementation = _records()
    isolated_source = tmp_path / "src"
    shutil.copytree(ADAPTER_SOURCE, isolated_source)
    entry_point = _entry_point()
    installed = discover_installed_adapters(
        distributions=(
            FakeDistribution(
                name="defined-quant-adapter-dq-native",
                version="1.0.0",
                entry_points=_entry_points(entry_point),
                root=isolated_source,
            ),
        )
    )
    attestation = _attestation(installed, adapter, implementation)
    available = _implementation_availability(
        _availability_snapshot(
        registry=registry,
        installed=installed,
        attested_adapter=adapter,
        attestation=attestation,
        ),
        implementation,
    )
    catalog = TrustedAdapterCatalog(
        registry=registry,
        installed=installed,
        policy=_policy(),
        artifact_attestations=(attestation,),
        runtime=_runtime(),
    )
    target = isolated_source / "defined_quant_adapter_dq_native/simple_return/adapter.py"
    target.write_bytes(target.read_bytes() + b"\n# tampered after availability\n")

    with pytest.raises(AdapterCatalogError) as caught:
        catalog.load(implementation.ref, availability=available)

    assert caught.value.code == "artifact_attestation_mismatch"
    assert entry_point.load_count == 0


def test_invoke_binds_request_and_runtime_and_translates_adapter_failures() -> None:
    registry, _, _, adapter, implementation = _records()
    entry_point = _entry_point()
    installed = _installed(entry_point)
    attestation = _attestation(installed, adapter, implementation)
    available = _implementation_availability(
        _availability_snapshot(
        registry=registry,
        installed=installed,
        attested_adapter=adapter,
        attestation=attestation,
        ),
        implementation,
    )
    catalog = TrustedAdapterCatalog(
        registry=registry,
        installed=installed,
        policy=_policy(),
        artifact_attestations=(attestation,),
        runtime=_runtime(),
    )
    runtime_binding = implementation.backend_bindings[0]

    def request(prices: list[float]) -> AdapterExecutionRequest:
        return AdapterExecutionRequest(
            plan=PlanRef.from_hash("b" * 64),
            execution_request_hash="c" * 64,
            step_id="calculate",
            capability=implementation.capability,
            implementation=implementation.ref,
            adapter=adapter.ref,
            backend_bindings=implementation.backend_bindings,
            transport=runtime_binding.transport,
            locality=runtime_binding.locality,
            execution_availability=available,
            canonical_inputs={"prices": prices, "timestamps": None},
        )

    mismatched_request = request([100.0, 105.0]).model_copy(
        update={
            "capability": implementation.capability.model_copy(update={"contract_hash": "d" * 64})
        }
    )
    mismatched = catalog.invoke(mismatched_request)
    assert isinstance(mismatched, AdapterExecutionFailure)
    assert mismatched.failure.code == ExecutionFailureCode.INTERNAL_FAILURE
    assert entry_point.load_count == 0

    successful_request = request([100.0, 105.0])
    success = catalog.invoke(successful_request)
    assert isinstance(success, AdapterExecutionSuccess)
    assert success.request_hash == successful_request.request_hash
    assert success.adapter_runtime == _runtime()
    assert success.canonical_output["returns"] == [0.05]

    failing_request = request([100.0])
    failure = catalog.invoke(failing_request)
    assert isinstance(failure, AdapterExecutionFailure)
    assert failure.request_hash == failing_request.request_hash
    assert failure.adapter_runtime == _runtime()
    assert failure.failure.code == ExecutionFailureCode.INPUT_MAPPING_FAILED
    assert "insufficient_prices" not in failure.failure.message
