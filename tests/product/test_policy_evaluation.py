from __future__ import annotations

from pathlib import Path

import pytest
from defined_quant.policy_evaluation import evaluate_policy_admission
from defined_quant.registry import load_registry
from defined_quant_protocol import (
    BackendKind,
    BackendRole,
    BackendRolePolicy,
    BackendSpec,
    CapabilityPolicyRule,
    DataEgress,
    ImplementationRef,
    ImplementationSpec,
    PolicyImplementation,
    RegistryTrustDimension,
    ResolutionPolicy,
    RuntimeLocality,
    TransportKind,
)

ROOT = Path(__file__).resolve().parents[2]


def _records() -> tuple[BackendSpec, ImplementationSpec]:
    registry = load_registry(root=ROOT / "registry").as_protocol_registry()
    implementation = next(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )
    backend = next(
        item
        for item in registry.backends
        if item.ref == implementation.backend_bindings[0].backend
    )
    return backend, implementation


def _policy(
    backend: BackendSpec,
    implementation: ImplementationSpec,
    *,
    implementation_ref: ImplementationRef | None = None,
    role: BackendRolePolicy | None = None,
    required_trust_dimensions: tuple[RegistryTrustDimension, ...] = (),
) -> ResolutionPolicy:
    binding = implementation.backend_bindings[0]
    return ResolutionPolicy(
        id="policy_evaluation_test",
        version="1.0.0",
        capability_rules=(
            CapabilityPolicyRule(
                capability=implementation.capability,
                implementations=(
                    PolicyImplementation(
                        implementation=(
                            implementation.ref
                            if implementation_ref is None
                            else implementation_ref
                        ),
                        priority=7,
                    ),
                ),
                backend_roles=(
                    role
                    or BackendRolePolicy(
                        role=BackendRole.RUNTIME,
                        allowed_backends=(backend.ref,),
                        allowed_kinds=(backend.kind,),
                        allowed_transports=(binding.transport,),
                        allowed_localities=(binding.locality,),
                        network_allowed=False,
                        allowed_data_egress=(backend.data_boundary.egress,),
                    ),
                ),
                required_trust_dimensions=required_trust_dimensions,
            ),
        ),
    )


def test_policy_evaluator_returns_one_canonical_admission_fact_tuple() -> None:
    backend, implementation = _records()
    required_trust = (
        RegistryTrustDimension.ARTIFACT_PINNED,
        RegistryTrustDimension.CONFORMANCE_TESTED,
    )

    facts = evaluate_policy_admission(
        implementation,
        _policy(
            backend,
            implementation,
            required_trust_dimensions=required_trust,
        ),
        backends={backend.ref.spec_hash: backend},
    )

    assert facts.priority == 7
    assert facts.satisfied_trust_dimensions == required_trust
    assert facts.missing_trust_dimensions == ()
    assert facts.admission_facts == (True, True, True, True, True, True, True)
    assert facts.admitted is True


def test_policy_evaluator_keeps_each_rejection_fact_independent() -> None:
    backend, implementation = _records()
    role = BackendRolePolicy(
        role=BackendRole.RUNTIME,
        allowed_kinds=(BackendKind.PYTHON_LIBRARY,),
        allowed_transports=(TransportKind.SUBPROCESS,),
        allowed_localities=(RuntimeLocality.REMOTE,),
        network_allowed=False,
        allowed_data_egress=(DataEgress.LOCAL_PROCESS,),
    )

    facts = evaluate_policy_admission(
        implementation,
        _policy(
            backend,
            implementation,
            role=role,
            required_trust_dimensions=(RegistryTrustDimension.SANDBOXED,),
        ),
        backends={backend.ref.spec_hash: backend},
    )

    assert facts.priority == 7
    assert facts.satisfied_trust_dimensions == ()
    assert facts.missing_trust_dimensions == (RegistryTrustDimension.SANDBOXED,)
    assert facts.admission_facts == (True, False, False, False, False, True, False)
    assert facts.admitted is False


def test_policy_evaluator_does_not_short_circuit_an_unlisted_implementation() -> None:
    backend, implementation = _records()
    unlisted = implementation.ref.model_copy(update={"spec_hash": "f" * 64})

    facts = evaluate_policy_admission(
        implementation,
        _policy(
            backend,
            implementation,
            implementation_ref=unlisted,
        ),
        backends={backend.ref.spec_hash: backend},
    )

    assert facts.priority is None
    assert facts.admission_facts == (False, True, True, True, True, True, True)
    assert facts.admitted is False


def test_policy_evaluator_folds_exact_backend_allowlisting_into_backend_kind() -> None:
    backend, implementation = _records()
    binding = implementation.backend_bindings[0]
    excluded_backend = backend.ref.model_copy(update={"spec_hash": "f" * 64})
    role = BackendRolePolicy(
        role=BackendRole.RUNTIME,
        allowed_backends=(excluded_backend,),
        allowed_kinds=(backend.kind,),
        allowed_transports=(binding.transport,),
        allowed_localities=(binding.locality,),
        network_allowed=False,
        allowed_data_egress=(backend.data_boundary.egress,),
    )

    facts = evaluate_policy_admission(
        implementation,
        _policy(backend, implementation, role=role),
        backends={backend.ref.spec_hash: backend},
    )

    assert facts.admission_facts == (True, True, False, True, True, True, True)
    assert facts.admitted is False


def test_policy_evaluator_reports_network_admission_independently() -> None:
    backend, implementation = _records()
    binding = implementation.backend_bindings[0]
    remote_backend = backend.model_copy(
        update={
            "id": "remote_backend",
            "kind": BackendKind.HTTP_API,
            "locality": RuntimeLocality.REMOTE,
            "transports": (TransportKind.HTTP,),
            "requires_network": True,
            "data_boundary": backend.data_boundary.model_copy(
                update={"egress": DataEgress.REMOTE_SERVICE}
            ),
        }
    )
    remote_implementation = implementation.model_copy(
        update={
            "adapter": implementation.adapter.model_copy(
                update={"backend": remote_backend.ref}
            ),
            "backend_bindings": (
                binding.model_copy(
                    update={
                        "backend": remote_backend.ref,
                        "transport": TransportKind.HTTP,
                        "locality": RuntimeLocality.REMOTE,
                    }
                ),
            ),
        }
    )
    role = BackendRolePolicy(
        role=BackendRole.RUNTIME,
        allowed_backends=(remote_backend.ref,),
        allowed_kinds=(remote_backend.kind,),
        allowed_transports=(TransportKind.HTTP,),
        allowed_localities=(RuntimeLocality.REMOTE,),
        network_allowed=False,
        allowed_data_egress=(DataEgress.REMOTE_SERVICE,),
    )

    facts = evaluate_policy_admission(
        remote_implementation,
        _policy(remote_backend, remote_implementation, role=role),
        backends={remote_backend.ref.spec_hash: remote_backend},
    )

    assert facts.admission_facts == (True, True, True, True, True, False, True)
    assert facts.admitted is False


@pytest.mark.parametrize("backend_case", ("missing", "mismatched"))
def test_policy_evaluator_fails_closed_for_an_unavailable_exact_backend(
    backend_case: str,
) -> None:
    backend, implementation = _records()
    backends: dict[str, BackendSpec] = {}
    if backend_case == "mismatched":
        mismatched = backend.model_copy(update={"id": "mismatched_backend"})
        backends[backend.ref.spec_hash] = mismatched

    facts = evaluate_policy_admission(
        implementation,
        _policy(backend, implementation),
        backends=backends,
    )

    assert facts.priority == 7
    assert facts.admission_facts == (True, True, False, False, False, False, False)
    assert facts.admitted is False
