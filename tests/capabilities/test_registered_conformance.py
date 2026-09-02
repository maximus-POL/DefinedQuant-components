from __future__ import annotations

import importlib
import json
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

import pytest
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
from defined_quant.registry import EvidenceRecord, Registry, load_registry
from defined_quant.schema_validation import schema_matches
from defined_quant_protocol.evidence import (
    CapabilityConformance,
    ConformanceCase,
    ConformanceInvariant,
    ImplementationEvidence,
    NumericalTolerance,
)
from defined_quant_protocol.execution import (
    AdapterExecutionFailure,
    AdapterExecutionRequest,
    AdapterExecutionResult,
    AdapterExecutionSuccess,
    ExecutionFailureCode,
)
from defined_quant_protocol.registry import (
    BackendRole,
    CapabilitySpec,
    ImplementationSpec,
    RuntimeIdentity,
    TrustDimension,
    derive_availability_requirements,
)
from defined_quant_protocol.resolution import (
    BackendRolePolicy,
    CapabilityPolicyRule,
    PlanRef,
    PolicyImplementation,
    RequirementStatus,
    ResolutionPolicy,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = ROOT / "registry"
ADAPTER_SOURCE = ROOT / "adapters/dq_native/src"
MANIFEST_PATH = ROOT / "registry/artifacts/defined_quant_adapter_dq_native/manifest.json"
CASE_TEST_NODE = (
    "tests/capabilities/test_registered_conformance.py::test_registered_conformance_case"
)
INVARIANT_TEST_NODE = (
    "tests/capabilities/test_registered_conformance.py::test_registered_conformance_invariant"
)
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
RUNTIME = RuntimeIdentity(
    name="dq_native_conformance_host",
    version="1.0.0",
    artifact_hash="a" * 64,
)
PLAN = PlanRef.from_hash("b" * 64)
EXECUTION_REQUEST_HASH = "c" * 64


@dataclass(frozen=True, slots=True)
class ConformanceTarget:
    implementation: ImplementationSpec
    capability: CapabilitySpec
    suite: CapabilityConformance
    case: ConformanceCase

    @property
    def test_id(self) -> str:
        return f"{self.implementation.id}-{self.case.id}"


@dataclass(frozen=True, slots=True)
class InvariantTarget:
    implementation: ImplementationSpec
    capability: CapabilitySpec
    invariant: ConformanceInvariant

    @property
    def test_id(self) -> str:
        return f"{self.implementation.id}-{self.invariant.id}"


@dataclass(frozen=True, slots=True)
class TrustedConformanceHarness:
    catalog: TrustedAdapterCatalog
    availability_by_implementation_hash: Mapping[str, Any]

    def invoke(
        self,
        *,
        implementation: ImplementationSpec,
        capability: CapabilitySpec,
        canonical_inputs: Mapping[str, Any],
    ) -> AdapterExecutionResult:
        runtime_binding = next(
            item for item in implementation.backend_bindings if item.role == BackendRole.RUNTIME
        )
        request = AdapterExecutionRequest(
            plan=PLAN,
            execution_request_hash=EXECUTION_REQUEST_HASH,
            step_id="conformance",
            capability=capability.ref,
            implementation=implementation.ref,
            adapter=implementation.adapter,
            backend_bindings=implementation.backend_bindings,
            transport=runtime_binding.transport,
            locality=runtime_binding.locality,
            execution_availability=self.availability_by_implementation_hash[
                implementation.ref.spec_hash
            ],
            canonical_inputs=dict(canonical_inputs),
        )
        result = self.catalog.invoke(request)
        assert result.request_hash == request.request_hash
        return result


class _EntryPoint:
    group = ADAPTER_ENTRY_POINT_GROUP

    def __init__(self, *, name: str, value: str) -> None:
        self.name = name
        self.value = value

    def load(self) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
        module_name, attribute = self.value.split(":", maxsplit=1)
        loaded = getattr(importlib.import_module(module_name), attribute)
        assert callable(loaded)
        return cast(Callable[[Mapping[str, object]], Mapping[str, object]], loaded)


class _Distribution:
    def __init__(
        self,
        *,
        entry_points: tuple[_EntryPoint, ...],
        manifest: Mapping[str, Any],
    ) -> None:
        self.metadata = {"Name": "defined-quant-adapter-dq-native"}
        self.version = "1.0.0"
        self.entry_points = entry_points
        self.files = tuple(PurePosixPath(item["path"]) for item in manifest["members"])

    def locate_file(self, path: object) -> Path:
        return ADAPTER_SOURCE / str(path)


def _materialize(value: Any) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$binary64"}:
            encoded = value["$binary64"]
            assert isinstance(encoded, str)
            result = float.fromhex(encoded)
            assert math.isfinite(result)
            return result
        return {str(key): _materialize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_materialize(item) for item in value]
    return value


def _conformance_record(
    registry: Registry,
    capability: CapabilitySpec,
) -> EvidenceRecord:
    selected = tuple(
        item
        for item in registry.evidence
        if item.subject_kind == "capability"
        and item.subject_id == capability.id
        and item.subject_version == capability.version
        and isinstance(item.spec, CapabilityConformance)
    )
    assert len(selected) == 1, (
        f"{capability.id}@{capability.version} requires one exact conformance suite"
    )
    return selected[0]


def _implementation_evidence(
    registry: Registry,
    implementation: ImplementationSpec,
) -> ImplementationEvidence:
    selected = tuple(
        item.spec
        for item in registry.evidence
        if item.subject_kind == "implementation"
        and item.subject_id == implementation.id
        and item.subject_version == implementation.version
        and isinstance(item.spec, ImplementationEvidence)
    )
    assert len(selected) == 1, (
        f"{implementation.id}@{implementation.version} requires one exact evidence record"
    )
    return selected[0]


def _is_dq_native(implementation: ImplementationSpec) -> bool:
    runtime = next(
        (item for item in implementation.backend_bindings if item.role == BackendRole.RUNTIME),
        None,
    )
    return runtime is not None and runtime.backend.id == "dq_native"


def _claims_conformance(implementation: ImplementationSpec) -> bool:
    return any(item.dimension == TrustDimension.CONFORMANCE_TESTED for item in implementation.trust)


AUTHORED_REGISTRY = load_registry(root=REGISTRY_ROOT)
PROTOCOL_REGISTRY = AUTHORED_REGISTRY.as_protocol_registry()
CAPABILITIES_BY_REF = {item.ref: item for item in PROTOCOL_REGISTRY.capabilities}
DQ_NATIVE_IMPLEMENTATIONS = tuple(
    item for item in PROTOCOL_REGISTRY.implementations if _is_dq_native(item)
)
CONFORMANCE_TARGETS = tuple(
    ConformanceTarget(
        implementation=implementation,
        capability=CAPABILITIES_BY_REF[implementation.capability],
        suite=suite,
        case=case,
    )
    for implementation in DQ_NATIVE_IMPLEMENTATIONS
    for suite in (
        cast(
            CapabilityConformance,
            _conformance_record(
                AUTHORED_REGISTRY,
                CAPABILITIES_BY_REF[implementation.capability],
            ).spec,
        ),
    )
    for case in suite.cases
)
INVARIANT_TARGETS = tuple(
    InvariantTarget(
        implementation=implementation,
        capability=CAPABILITIES_BY_REF[implementation.capability],
        invariant=invariant,
    )
    for implementation in DQ_NATIVE_IMPLEMENTATIONS
    for suite in (
        cast(
            CapabilityConformance,
            _conformance_record(
                AUTHORED_REGISTRY,
                CAPABILITIES_BY_REF[implementation.capability],
            ).spec,
        ),
    )
    for invariant in suite.invariants
)


def _policy() -> ResolutionPolicy:
    backends = {item.ref: item for item in PROTOCOL_REGISTRY.backends}
    rules: list[CapabilityPolicyRule] = []
    for capability in PROTOCOL_REGISTRY.capabilities:
        implementations = tuple(
            item for item in DQ_NATIVE_IMPLEMENTATIONS if item.capability == capability.ref
        )
        if not implementations:
            continue
        bindings = tuple(
            item for implementation in implementations for item in implementation.backend_bindings
        )
        roles = tuple(sorted({item.role for item in bindings}, key=lambda item: item.value))
        role_policies = []
        for role in roles:
            role_bindings = tuple(item for item in bindings if item.role == role)
            role_backends = tuple(
                sorted(
                    {item.backend for item in role_bindings},
                    key=lambda item: (item.id, item.version, item.spec_hash),
                )
            )
            role_policies.append(
                BackendRolePolicy(
                    role=role,
                    allowed_backends=role_backends,
                    allowed_kinds=tuple(
                        sorted(
                            {backends[item].kind for item in role_backends},
                            key=lambda item: item.value,
                        )
                    ),
                    allowed_transports=tuple(
                        sorted(
                            {item.transport for item in role_bindings},
                            key=lambda item: item.value,
                        )
                    ),
                    allowed_localities=tuple(
                        sorted(
                            {item.locality for item in role_bindings},
                            key=lambda item: item.value,
                        )
                    ),
                    network_allowed=any(backends[item].requires_network for item in role_backends),
                    allowed_data_egress=tuple(
                        sorted(
                            {backends[item].data_boundary.egress for item in role_backends},
                            key=lambda item: item.value,
                        )
                    ),
                )
            )
        rules.append(
            CapabilityPolicyRule(
                capability=capability.ref,
                implementations=tuple(
                    PolicyImplementation(implementation=item.ref, priority=index)
                    for index, item in enumerate(
                        sorted(
                            implementations,
                            key=lambda value: (
                                value.id,
                                value.version,
                                value.ref.spec_hash,
                            ),
                        )
                    )
                ),
                backend_roles=tuple(role_policies),
                required_trust_dimensions=(TrustDimension.CONFORMANCE_TESTED,),
            )
        )
    return ResolutionPolicy(
        id="dq_native_conformance",
        version="1.0.0",
        capability_rules=tuple(rules),
    )


@pytest.fixture(scope="module")
def trusted_harness() -> Iterator[TrustedConformanceHarness]:
    raw_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_manifest, dict)
    entry_points = tuple(
        _EntryPoint(name=item["name"], value=item["value"]) for item in raw_manifest["entry_points"]
    )
    installed = discover_installed_adapters(
        distributions=(_Distribution(entry_points=entry_points, manifest=raw_manifest),)
    )
    adapters_by_ref = {item.ref: item for item in PROTOCOL_REGISTRY.adapters}
    artifact_bindings = {
        (implementation.adapter.spec_hash, implementation.artifact.artifact_hash): (
            adapters_by_ref[implementation.adapter],
            implementation.artifact,
        )
        for implementation in DQ_NATIVE_IMPLEMENTATIONS
    }
    attestation_by_binding = {
        key: verify_installed_artifact(
            installed=installed,
            adapter=adapter,
            artifact=artifact,
            expected_manifest=raw_manifest,
        )
        for key, (adapter, artifact) in artifact_bindings.items()
    }
    attestations = tuple(attestation_by_binding.values())
    attestation_by_implementation = {
        implementation.ref.spec_hash: attestation_by_binding[
            (implementation.adapter.spec_hash, implementation.artifact.artifact_hash)
        ]
        for implementation in DQ_NATIVE_IMPLEMENTATIONS
    }

    def requirement(required: bool) -> RequirementStatus:
        return RequirementStatus.UNKNOWN if required else RequirementStatus.NOT_REQUIRED

    requirements_by_implementation = {
        implementation.ref.spec_hash: derive_availability_requirements(
            implementation,
            PROTOCOL_REGISTRY.backends,
        )
        for implementation in PROTOCOL_REGISTRY.implementations
    }

    availability = build_availability_snapshot(
        registry=PROTOCOL_REGISTRY,
        installed=installed,
        assessments=tuple(
            HostAvailabilityAssessment(
                implementation=implementation.ref,
                enabled=_is_dq_native(implementation),
                artifact_attestation=attestation_by_implementation.get(
                    implementation.ref.spec_hash
                ),
                credentials=requirement(
                    requirements_by_implementation[
                        implementation.ref.spec_hash
                    ].credentials_required
                ),
                licence=requirement(
                    requirements_by_implementation[implementation.ref.spec_hash].licence_required
                ),
                entitlement=requirement(
                    requirements_by_implementation[
                        implementation.ref.spec_hash
                    ].entitlement_required
                ),
                transport=(
                    RequirementStatus.SATISFIED
                    if _is_dq_native(implementation)
                    else RequirementStatus.UNKNOWN
                ),
                reachability=requirement(
                    requirements_by_implementation[
                        implementation.ref.spec_hash
                    ].reachability_required
                ),
                dependencies=requirement(
                    requirements_by_implementation[
                        implementation.ref.spec_hash
                    ].dependencies_required
                ),
            )
            for implementation in PROTOCOL_REGISTRY.implementations
        ),
        snapshot_id="dq_native_conformance",
        observed_at=NOW,
        evaluator=RUNTIME,
    )
    availability_by_hash = {
        item.implementation.spec_hash: item for item in availability.implementations
    }
    policy = _policy()
    catalog = TrustedAdapterCatalog(
        registry=PROTOCOL_REGISTRY,
        installed=installed,
        policy=policy,
        artifact_attestations=attestations,
        runtime=RUNTIME,
    )
    yield TrustedConformanceHarness(
        catalog=catalog,
        availability_by_implementation_hash=availability_by_hash,
    )


def _assert_equivalent(
    actual: Any,
    expected: Any,
    *,
    tolerance: NumericalTolerance,
    path: str = "$",
) -> None:
    if isinstance(expected, Mapping):
        assert isinstance(actual, Mapping), f"{path} must be an object"
        assert set(actual) == set(expected), f"{path} object fields differ"
        for key in expected:
            _assert_equivalent(
                actual[key],
                expected[key],
                tolerance=tolerance,
                path=f"{path}.{key}",
            )
        return
    if isinstance(expected, Sequence) and not isinstance(expected, str | bytes | bytearray):
        assert isinstance(actual, Sequence) and not isinstance(actual, str | bytes | bytearray), (
            f"{path} must be an array"
        )
        assert len(actual) == len(expected), f"{path} array length differs"
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=True)):
            _assert_equivalent(
                actual_item,
                expected_item,
                tolerance=tolerance,
                path=f"{path}[{index}]",
            )
        return
    if isinstance(expected, float):
        assert isinstance(actual, int | float) and not isinstance(actual, bool), (
            f"{path} must be numeric"
        )
        assert math.isclose(
            float(actual),
            expected,
            rel_tol=tolerance.relative,
            abs_tol=tolerance.absolute,
        ), f"{path}: {actual!r} != {expected!r} within {tolerance!r}"
        return
    assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def _successful_output(
    harness: TrustedConformanceHarness,
    target: InvariantTarget,
    canonical_inputs: Mapping[str, Any],
) -> Mapping[str, Any]:
    result = harness.invoke(
        implementation=target.implementation,
        capability=target.capability,
        canonical_inputs=canonical_inputs,
    )
    assert isinstance(result, AdapterExecutionSuccess), (
        f"{target.test_id} unexpectedly failed: {result!r}"
    )
    assert schema_matches(result.canonical_output, target.capability.output_schema)
    return result.canonical_output


def _assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=1.0e-12, abs_tol=1.0e-12)


def _assert_sequence_close(actual: Sequence[Any], expected: Sequence[float]) -> None:
    assert len(actual) == len(expected)
    for actual_item, expected_item in zip(actual, expected, strict=True):
        assert isinstance(actual_item, int | float) and not isinstance(actual_item, bool)
        _assert_close(float(actual_item), expected_item)


def _simple_return_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    prices = [80.0, 84.0, 79.8, 91.77]
    output = _successful_output(harness, target, {"prices": prices, "timestamps": None})
    returns = cast(Sequence[float], output["returns"])
    if target.invariant.id == "terminal_ratio":
        _assert_close(math.prod(1.0 + item for item in returns), prices[-1] / prices[0])
    elif target.invariant.id == "positive_scale_invariance":
        scaled = _successful_output(
            harness,
            target,
            {"prices": [item * 137.0 for item in prices], "timestamps": None},
        )
        _assert_sequence_close(cast(Sequence[Any], scaled["returns"]), returns)
    elif target.invariant.id == "constant_series_zero":
        constant = _successful_output(
            harness,
            target,
            {"prices": [42.0, 42.0, 42.0], "timestamps": None},
        )
        assert list(cast(Sequence[Any], constant["returns"])) == [0.0, 0.0]
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _log_return_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    prices = [80.0, 84.0, 79.8, 91.77]
    output = _successful_output(harness, target, {"prices": prices, "timestamps": None})
    returns = cast(Sequence[float], output["returns"])
    if target.invariant.id == "terminal_log_difference":
        _assert_close(sum(returns), math.log(prices[-1]) - math.log(prices[0]))
    elif target.invariant.id == "positive_scale_invariance":
        scaled = _successful_output(
            harness,
            target,
            {"prices": [item * 137.0 for item in prices], "timestamps": None},
        )
        _assert_sequence_close(cast(Sequence[Any], scaled["returns"]), returns)
    elif target.invariant.id == "constant_series_zero":
        constant = _successful_output(
            harness,
            target,
            {"prices": [42.0, 42.0, 42.0], "timestamps": None},
        )
        assert list(cast(Sequence[Any], constant["returns"])) == [0.0, 0.0]
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _monthly_calendar_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    source_returns = [-0.2, 0.1, -0.1]
    source_months = ["2022-11", "2022-12", "2023-01", "2023-02"]
    output = _successful_output(
        harness,
        target,
        {"returns": source_returns, "observation_months": source_months},
    )
    if target.invariant.id == "values_preserved":
        assert list(cast(Sequence[Any], output["monthly_returns"])) == source_returns
    elif target.invariant.id == "interval_end_alignment":
        assert list(cast(Sequence[Any], output["return_months"])) == source_months[1:]
    elif target.invariant.id == "consecutive_calendar":
        serial_months = tuple(
            int(item[:4]) * 12 + int(item[5:])
            for item in cast(Sequence[str], output["return_months"])
        )
        assert all(
            current == previous + 1
            for previous, current in zip(serial_months, serial_months[1:], strict=False)
        )
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _rebase_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    prices = [80.0, 100.0, 60.0, 120.0]
    canonical_inputs = {
        "prices": prices,
        "base_index": 1,
        "base_value": 37.0,
        "timestamps": None,
    }
    output = _successful_output(harness, target, canonical_inputs)
    index_values = cast(Sequence[float], output["index_values"])
    if target.invariant.id == "base_identity":
        assert index_values[1] == 37.0
    elif target.invariant.id == "positive_scale_invariance":
        scaled_inputs = dict(canonical_inputs)
        scaled_inputs["prices"] = [item * 137.0 for item in prices]
        scaled = _successful_output(harness, target, scaled_inputs)
        _assert_sequence_close(cast(Sequence[Any], scaled["index_values"]), index_values)
    elif target.invariant.id == "relative_path_preserved":
        for left in range(len(prices)):
            for right in range(len(prices)):
                _assert_close(
                    index_values[left] / index_values[right],
                    prices[left] / prices[right],
                )
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _drawdown_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    prices = [100.0, 120.0, 90.0, 84.0, 108.0, 120.0]
    output = _successful_output(harness, target, {"prices": prices, "timestamps": None})
    if target.invariant.id == "scale_invariance":
        scaled = _successful_output(
            harness,
            target,
            {"prices": [item * 137.0 for item in prices], "timestamps": None},
        )
        for field in (
            "drawdowns",
            "maximum_drawdown",
            "running_peak_indices",
            "peak_index",
            "trough_index",
            "recovery_index",
            "recovered",
        ):
            if field in {"drawdowns", "maximum_drawdown"}:
                expected = (
                    cast(Sequence[float], output[field])
                    if field == "drawdowns"
                    else [cast(float, output[field])]
                )
                actual = (
                    cast(Sequence[Any], scaled[field]) if field == "drawdowns" else [scaled[field]]
                )
                _assert_sequence_close(actual, expected)
            else:
                assert scaled[field] == output[field]
    elif target.invariant.id == "non_decreasing_zero":
        non_decreasing = _successful_output(
            harness,
            target,
            {"prices": [1.0, 2.0, 2.0, 3.0], "timestamps": None},
        )
        assert list(cast(Sequence[Any], non_decreasing["drawdowns"])) == [
            0.0,
            0.0,
            0.0,
            0.0,
        ]
        assert non_decreasing["maximum_drawdown"] == 0.0
    elif target.invariant.id == "earliest_deepest_trough":
        tied = _successful_output(
            harness,
            target,
            {"prices": [100.0, 80.0, 100.0, 80.0], "timestamps": None},
        )
        assert tied["trough_index"] == 1
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _sample_deviation_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    values = [-0.02, 0.0, 0.03]
    output = _successful_output(harness, target, {"values": values})
    standard_deviation = cast(float, output["standard_deviation"])
    if target.invariant.id == "translation_invariance":
        translated = _successful_output(
            harness,
            target,
            {"values": [item + 17.0 for item in values]},
        )
        _assert_close(cast(float, translated["standard_deviation"]), standard_deviation)
    elif target.invariant.id == "positive_scale_equivariance":
        scaled = _successful_output(
            harness,
            target,
            {"values": [item * 137.0 for item in values]},
        )
        _assert_close(cast(float, scaled["standard_deviation"]), standard_deviation * 137.0)
    elif target.invariant.id == "constant_sample_zero":
        constant = _successful_output(harness, target, {"values": [42.0, 42.0, 42.0]})
        assert constant["standard_deviation"] == 0.0
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _rolling_deviation_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    values = [0.01, -0.01, 0.03, 0.01, 0.02]
    output = _successful_output(
        harness,
        target,
        {"values": values, "window_length": 3, "timestamps": None},
    )
    if target.invariant.id == "output_count":
        assert len(cast(Sequence[Any], output["standard_deviations"])) == len(values) - 3 + 1
    elif target.invariant.id == "constant_windows_zero":
        constant = _successful_output(
            harness,
            target,
            {
                "values": [42.0, 42.0, 42.0, 42.0],
                "window_length": 3,
                "timestamps": None,
            },
        )
        assert list(cast(Sequence[Any], constant["standard_deviations"])) == [0.0, 0.0]
    elif target.invariant.id == "window_end_alignment":
        timestamps = [
            "2026-07-24T00:00:00Z",
            "2026-07-25T00:00:00Z",
            "2026-07-26T00:00:00Z",
            "2026-07-27T00:00:00Z",
            "2026-07-28T00:00:00Z",
        ]
        timestamped = _successful_output(
            harness,
            target,
            {"values": values, "window_length": 3, "timestamps": timestamps},
        )
        assert list(cast(Sequence[Any], timestamped["window_end_timestamps"])) == timestamps[2:]
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _scalar_annualization_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    if target.invariant.id == "factor_one_identity":
        output = _successful_output(
            harness,
            target,
            {"periodic_value": 0.123, "annualization_factor": 1.0},
        )
        assert output["annualized_value"] == 0.123
    elif target.invariant.id == "true_zero_preserved":
        for factor in (5.0e-324, 1.0, 252.0):
            output = _successful_output(
                harness,
                target,
                {"periodic_value": 0.0, "annualization_factor": factor},
            )
            assert output["annualized_value"] == 0.0
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


def _series_annualization_invariant(
    target: InvariantTarget,
    harness: TrustedConformanceHarness,
) -> None:
    periodic_values = [0.03, 0.01, 0.02]
    if target.invariant.id == "length_preserved":
        output = _successful_output(
            harness,
            target,
            {"periodic_values": periodic_values, "annualization_factor": 4.0},
        )
        annualized = cast(Sequence[Any], output["annualized_values"])
        _assert_sequence_close(annualized, [0.06, 0.02, 0.04])
        assert len(annualized) == len(periodic_values)
    elif target.invariant.id == "factor_one_identity":
        output = _successful_output(
            harness,
            target,
            {"periodic_values": periodic_values, "annualization_factor": 1.0},
        )
        assert list(cast(Sequence[Any], output["annualized_values"])) == periodic_values
    else:
        raise AssertionError(f"unsupported asserted invariant: {target.test_id}")


INVARIANT_CHECKS: Mapping[
    str,
    Callable[[InvariantTarget, TrustedConformanceHarness], None],
] = {
    "performance.drawdown": _drawdown_invariant,
    "prices.rebase": _rebase_invariant,
    "returns.log": _log_return_invariant,
    "returns.monthly_calendar_matrix": _monthly_calendar_invariant,
    "returns.simple": _simple_return_invariant,
    "statistics.rolling_sample_standard_deviation": _rolling_deviation_invariant,
    "statistics.sample_standard_deviation": _sample_deviation_invariant,
    "statistics.square_root_annualize": _scalar_annualization_invariant,
    "statistics.square_root_annualize_series": _series_annualization_invariant,
}


def test_conformance_tested_claims_have_complete_harness() -> None:
    claimed = {(item.id, item.version, item.capability) for item in DQ_NATIVE_IMPLEMENTATIONS}
    exercised_cases = {
        (
            item.implementation.id,
            item.implementation.version,
            item.capability.ref,
        )
        for item in CONFORMANCE_TARGETS
    }
    exercised_invariants = {
        (
            item.implementation.id,
            item.implementation.version,
            item.capability.ref,
        )
        for item in INVARIANT_TARGETS
    }
    assert claimed
    assert exercised_cases == claimed
    assert exercised_invariants == claimed
    for implementation in DQ_NATIVE_IMPLEMENTATIONS:
        assert _claims_conformance(implementation), (
            f"{implementation.id} is registered as DQ-native without executable conformance"
        )
        evidence = _implementation_evidence(AUTHORED_REGISTRY, implementation)
        assert set(evidence.conformance.tests) == {
            CASE_TEST_NODE,
            INVARIANT_TEST_NODE,
        }, f"{implementation.id} has an unsupported conformance_tested claim"
        assert implementation.capability.id in INVARIANT_CHECKS, (
            f"{implementation.id} has no executable invariant harness"
        )


@pytest.mark.parametrize(
    "target",
    CONFORMANCE_TARGETS,
    ids=lambda target: target.test_id,
)
def test_registered_conformance_case(
    trusted_harness: TrustedConformanceHarness,
    target: ConformanceTarget,
) -> None:
    canonical_inputs = _materialize(target.case.input)
    assert isinstance(canonical_inputs, dict)
    result = trusted_harness.invoke(
        implementation=target.implementation,
        capability=target.capability,
        canonical_inputs=canonical_inputs,
    )
    expected = target.case.expected
    if expected.outcome == "failure":
        assert isinstance(result, AdapterExecutionFailure), target.test_id
        adapter_code = next(
            (item.value for item in result.failure.facts if item.key == "adapter_code"),
            None,
        )
        assert adapter_code == expected.code, target.test_id
        expected_boundary_code = (
            ExecutionFailureCode.OUTPUT_MAPPING_FAILED
            if expected.code == "non_finite_result"
            else ExecutionFailureCode.INPUT_MAPPING_FAILED
        )
        assert result.failure.code == expected_boundary_code, target.test_id
        return

    assert isinstance(result, AdapterExecutionSuccess), target.test_id
    assert schema_matches(result.canonical_output, target.capability.output_schema), target.test_id
    expected_output = _materialize(expected.output)
    _assert_equivalent(
        result.canonical_output,
        expected_output,
        tolerance=expected.tolerance,
    )


@pytest.mark.parametrize(
    "target",
    INVARIANT_TARGETS,
    ids=lambda target: target.test_id,
)
def test_registered_conformance_invariant(
    trusted_harness: TrustedConformanceHarness,
    target: InvariantTarget,
) -> None:
    checker = INVARIANT_CHECKS.get(target.capability.id)
    assert checker is not None, f"unsupported asserted invariant: {target.test_id}"
    checker(target, trusted_harness)
