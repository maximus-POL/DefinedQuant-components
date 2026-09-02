from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from defined_quant.adapter_artifacts import verify_installed_artifact
from defined_quant.adapter_catalog import (
    HostAvailabilityAssessment,
    TrustedAdapterCatalog,
    build_availability_snapshot,
)
from defined_quant.adapter_discovery import ADAPTER_ENTRY_POINT_GROUP, discover_installed_adapters
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
MANIFEST_PATH = ROOT / "registry/artifacts/defined_quant_adapter_dq_native/manifest.json"
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class _EntryPoint:
    group = ADAPTER_ENTRY_POINT_GROUP

    def __init__(self, *, name: str, value: str) -> None:
        self.name = name
        self.value = value
        self.load_count = 0

    def load(self) -> Callable[[dict[str, object]], dict[str, object]]:
        self.load_count += 1
        module_name, attribute = self.value.split(":", maxsplit=1)
        return getattr(importlib.import_module(module_name), attribute)


class _Distribution:
    def __init__(self, entry_points: tuple[_EntryPoint, ...], manifest: dict[str, Any]) -> None:
        self.metadata = {"Name": "defined-quant-adapter-dq-native"}
        self.version = "1.0.0"
        self.entry_points = entry_points
        self.files = tuple(PurePosixPath(item["path"]) for item in manifest["members"])

    def locate_file(self, path: object) -> Path:
        return ADAPTER_SOURCE / str(path)


def _policy(registry: Any) -> ResolutionPolicy:
    backend = registry.backends[0]
    rules = []
    for capability in registry.capabilities:
        implementation = next(
            item for item in registry.implementations if item.capability == capability.ref
        )
        binding = implementation.backend_bindings[0]
        rules.append(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=(
                    PolicyImplementation(implementation=implementation.ref, priority=0),
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
            )
        )
    return ResolutionPolicy(
        id="all_dq_native_methods",
        version="1.0.0",
        capability_rules=tuple(rules),
    )


@pytest.fixture(scope="module")
def runtime() -> tuple[MethodsRuntime, dict[str, _EntryPoint]]:
    authored = load_registry(root=ROOT / "registry")
    registry = authored.as_protocol_registry()
    raw_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_manifest, dict)
    entry_points = tuple(
        _EntryPoint(name=item["name"], value=item["value"])
        for item in raw_manifest["entry_points"]
    )
    installed = discover_installed_adapters(
        distributions=(_Distribution(entry_points, raw_manifest),)
    )
    attestations = tuple(
        verify_installed_artifact(
            installed=installed,
            adapter=adapter,
            artifact=next(
                item.artifact
                for item in registry.implementations
                if item.adapter == adapter.ref
            ),
            expected_manifest=raw_manifest,
        )
        for adapter in registry.adapters
    )
    by_adapter = {item.adapter: item for item in attestations}
    identity = RuntimeIdentity(
        name="all_native_test_host",
        version="1.0.0",
        artifact_hash="a" * 64,
    )
    availability = build_availability_snapshot(
        registry=registry,
        installed=installed,
        assessments=tuple(
            HostAvailabilityAssessment(
                implementation=implementation.ref,
                enabled=True,
                artifact_attestation=by_adapter[implementation.adapter],
                dependencies=RequirementStatus.NOT_REQUIRED,
                credentials=RequirementStatus.NOT_REQUIRED,
                licence=RequirementStatus.NOT_REQUIRED,
                entitlement=RequirementStatus.NOT_REQUIRED,
                transport=RequirementStatus.SATISFIED,
                reachability=RequirementStatus.NOT_REQUIRED,
            )
            for implementation in registry.implementations
        ),
        snapshot_id="all_native",
        observed_at=NOW,
        evaluator=identity,
    )
    policy = _policy(registry)
    service = MethodsRuntime(
        registry=authored,
        policy=policy,
        availability=availability,
        adapter_catalog=TrustedAdapterCatalog(
            registry=registry,
            installed=installed,
            policy=policy,
            artifact_attestations=attestations,
            runtime=identity,
        ),
        runtime_identity=identity,
        clock=lambda: NOW,
    )
    try:
        yield service, {item.name: item for item in entry_points}
    finally:
        service.close()


@pytest.mark.parametrize(
    ("method_id", "financial_inputs", "conventions", "step_count", "expected"),
    [
        (
            "dq.market_data.log_return",
            {"prices": [100.0, 105.0, 102.9]},
            {"price_kind": "adjusted"},
            1,
            {"return_kind": "log", "ordering_status": "unverified"},
        ),
        (
            "dq.market_data.monthly_return_matrix",
            {
                "prices": [100.0, 80.0, 88.0, 79.2],
                "months": ["2022-11", "2022-12", "2023-01", "2023-02"],
            },
            {"price_kind": "adjusted", "observation_kind": "completed_month_end"},
            2,
            {
                "monthly_returns": pytest.approx([-0.2, 0.1, -0.1]),
                "return_months": ["2022-12", "2023-01", "2023-02"],
            },
        ),
        (
            "dq.market_data.rebased_price_index",
            {"prices": [80.0, 100.0, 60.0, 120.0]},
            {"price_kind": "adjusted", "base_index": 1, "base_value": 100.0},
            1,
            {"index_values": pytest.approx([80.0, 100.0, 60.0, 120.0])},
        ),
        (
            "dq.performance.drawdown",
            {"prices": [100.0, 120.0, 90.0, 84.0, 108.0, 120.0]},
            {"price_kind": "adjusted"},
            1,
            {"maximum_drawdown": pytest.approx(-0.3), "recovery_index": 5},
        ),
        (
            "dq.volatility.historical_volatility",
            {"returns": [-0.01, 0.01]},
            {"annualization_factor": 4.0, "return_kind": "log"},
            2,
            {
                "periodic_volatility": pytest.approx(0.01414213562373095),
                "annualized_volatility": pytest.approx(0.0282842712474619),
            },
        ),
        (
            "dq.volatility.rolling_historical_volatility",
            {"returns": [0.01, -0.01, 0.03, 0.01]},
            {"window_length": 3, "annualization_factor": 12.0, "return_kind": "log"},
            2,
            {
                "periodic_volatility": pytest.approx([0.02, 0.02]),
                "annualized_volatility": pytest.approx([0.06928203230275509] * 2),
            },
        ),
    ],
)
def test_remaining_methods_compile_and_execute_only_exact_native_bindings(
    runtime: tuple[MethodsRuntime, dict[str, _EntryPoint]],
    method_id: str,
    financial_inputs: dict[str, Any],
    conventions: dict[str, Any],
    step_count: int,
    expected: dict[str, Any],
) -> None:
    service, entry_points = runtime
    outcome = service.compile_plan(
        PlanProposal(
            method_id=method_id,
            method_version="1.0.0",
            financial_inputs=financial_inputs,
            conventions=conventions,
        )
    )
    assert isinstance(outcome, CompiledPlan)
    assert len(outcome.steps) == step_count
    selected_dispatch = set()
    for step in outcome.steps:
        record = service.registry.get("adapter", step.adapter.id, step.adapter.version)
        assert record.spec is not None
        selected_dispatch.add(getattr(record.spec, "dispatch_key"))
    before = {name: item.load_count for name, item in entry_points.items()}

    run = service.execute_plan(outcome.ref)

    assert run.status == RunStatus.SUCCEEDED
    assert len(run.steps) == step_count
    assert run.canonical_method_output is not None
    for field, value in expected.items():
        assert run.canonical_method_output[field] == value
    for name, item in entry_points.items():
        assert item.load_count - before[name] == (1 if name in selected_dispatch else 0)
