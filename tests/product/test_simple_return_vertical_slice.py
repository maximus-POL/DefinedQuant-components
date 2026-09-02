from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from defined_quant.adapter_artifacts import verify_installed_artifact
from defined_quant.adapter_catalog import (
    HostAvailabilityAssessment,
    TrustedAdapterCatalog,
    build_availability_snapshot,
)
from defined_quant.adapter_discovery import (
    ADAPTER_ENTRY_POINT_GROUP,
    discover_installed_adapters,
)
from defined_quant.method_service import MethodsRuntime
from defined_quant.registry import load_registry
from defined_quant_protocol import (
    BackendRolePolicy,
    CapabilityPolicyRule,
    CompiledPlan,
    DataEgress,
    PlanProposal,
    PolicyImplementation,
    RequirementStatus,
    ResolutionPolicy,
    RunStatus,
    RuntimeIdentity,
)

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_SOURCE = ROOT / "adapters/dq_native/src"
MANIFEST_PATH = (
    ROOT / "registry/artifacts/defined_quant_adapter_dq_native/manifest.json"
)
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class _EntryPoint:
    group = ADAPTER_ENTRY_POINT_GROUP
    name = "dq_native_simple_return"
    value = "defined_quant_adapter_dq_native.simple_return.adapter:execute"

    def __init__(self) -> None:
        self.load_count = 0

    def load(self) -> Callable[[dict[str, object]], dict[str, object]]:
        self.load_count += 1
        from defined_quant_adapter_dq_native.simple_return.adapter import execute

        return execute


class _Distribution:
    def __init__(self, entry_points: tuple[Any, ...], manifest: dict[str, Any]) -> None:
        self.metadata = {"Name": "defined-quant-adapter-dq-native"}
        self.version = "1.0.0"
        self.entry_points = entry_points
        self.files = tuple(
            PurePosixPath(item["path"]) for item in manifest["members"]
        )

    def locate_file(self, path: object) -> Path:
        return ADAPTER_SOURCE / str(path)


def _policy(registry: Any) -> ResolutionPolicy:
    capability = next(item for item in registry.capabilities if item.id == "returns.simple")
    backend = registry.backends[0]
    implementation = next(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )
    binding = implementation.backend_bindings[0]
    return ResolutionPolicy(
        id="local_methods",
        version="1.0.0",
        capability_rules=(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=(
                    PolicyImplementation(
                        implementation=implementation.ref,
                        priority=0,
                    ),
                ),
                backend_roles=(
                    BackendRolePolicy(
                        role=binding.role,
                        allowed_backends=(backend.ref,),
                        allowed_kinds=(backend.kind,),
                        allowed_transports=(binding.transport,),
                        allowed_localities=(binding.locality,),
                        network_allowed=False,
                        allowed_data_egress=(DataEgress.NONE,),
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


def test_simple_return_methods_first_vertical_slice() -> None:
    authored_registry = load_registry(root=ROOT / "registry")
    registry = authored_registry.as_protocol_registry()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    entry_point = _EntryPoint()
    entry_points: list[Any] = []
    for item in manifest["entry_points"]:
        if item["name"] == entry_point.name:
            entry_points.append(entry_point)
        else:
            entry_points.append(
                type(
                    "MetadataEntryPoint",
                    (),
                    {
                        "group": ADAPTER_ENTRY_POINT_GROUP,
                        "name": item["name"],
                        "value": item["value"],
                        "load": lambda self: (_ for _ in ()).throw(
                            AssertionError("unselected adapter was loaded")
                        ),
                    },
                )()
            )
    installed = discover_installed_adapters(
        distributions=(_Distribution(tuple(entry_points), manifest),)
    )
    adapter = next(item for item in registry.adapters if item.id == "dq_native.simple_return")
    implementation = next(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )
    attestation = verify_installed_artifact(
        installed=installed,
        adapter=adapter,
        artifact=implementation.artifact,
        expected_manifest=manifest,
    )
    runtime_identity = RuntimeIdentity(
        name="vertical_slice_host",
        version="1.0.0",
        artifact_hash="a" * 64,
    )
    availability = build_availability_snapshot(
        registry=registry,
        installed=installed,
        assessments=tuple(
            HostAvailabilityAssessment(
                implementation=item.ref,
                enabled=True,
                artifact_attestation=(
                    attestation if item.adapter == adapter.ref else None
                ),
                dependencies=RequirementStatus.NOT_REQUIRED,
                credentials=RequirementStatus.NOT_REQUIRED,
                licence=RequirementStatus.NOT_REQUIRED,
                entitlement=RequirementStatus.NOT_REQUIRED,
                transport=RequirementStatus.SATISFIED,
                reachability=RequirementStatus.NOT_REQUIRED,
            )
            for item in registry.implementations
        ),
        snapshot_id="vertical_slice",
        observed_at=NOW,
        evaluator=runtime_identity,
    )
    policy = _policy(registry)
    catalog = TrustedAdapterCatalog(
        registry=registry,
        installed=installed,
        policy=policy,
        artifact_attestations=(attestation,),
        runtime=runtime_identity,
    )

    with MethodsRuntime(
        registry=authored_registry,
        policy=policy,
        availability=availability,
        adapter_catalog=catalog,
        runtime_identity=runtime_identity,
        clock=lambda: NOW,
    ) as service:
        search = service.search_methods("calculate returns from prices")
        assert "dq.market_data.simple_return" in {
            hit.method.id for hit in search.hits
        }
        inspection = service.inspect_method("dq.market_data.simple_return")
        assert inspection.capabilities[0].id == "returns.simple"
        assert inspection.implementations[0].id == "dq_native.simple_return"

        outcome = service.compile_plan(
            PlanProposal(
                method_id="dq.market_data.simple_return",
                method_version="1.0.0",
                financial_inputs={"prices": [100.0, 110.0, 99.0]},
                conventions={"price_kind": "adjusted"},
            )
        )
        assert isinstance(outcome, CompiledPlan)
        assert outcome.steps[0].implementation == implementation.ref
        assert entry_point.load_count == 0

        run = service.execute_plan(outcome.ref)
        assert run.status == RunStatus.SUCCEEDED
        assert entry_point.load_count == 1
        assert run.canonical_method_output == {
            "declared_frequency": None,
            "gap_check": "not_assessed",
            "ordering_status": "unverified",
            "price_kind": "adjusted",
            "return_kind": "simple",
            "return_timestamps": None,
            "returns": [0.1, -0.1],
        }
        assert service.get_plan(outcome.ref).plan == outcome
        assert service.get_run(run.ref) == run
        assert [warning.code for warning in run.warnings] == ["ordering_unverified"]
