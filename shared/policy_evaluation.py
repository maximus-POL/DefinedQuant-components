"""Canonical static policy-admission evaluation for planning and adapter loading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from defined_quant_protocol import (
    BackendSpec,
    ImplementationSpec,
    RegistryTrustDimension,
    ResolutionPolicy,
)


@dataclass(frozen=True, slots=True)
class PolicyAdmissionFacts:
    """Named policy facts shared by candidate explanations and runtime admission."""

    priority: int | None
    policy_allowed: bool
    trust_satisfied: bool
    backend_kind_allowed: bool
    transport_allowed: bool
    locality_allowed: bool
    network_allowed: bool
    data_handling_allowed: bool
    satisfied_trust_dimensions: tuple[RegistryTrustDimension, ...]
    missing_trust_dimensions: tuple[RegistryTrustDimension, ...]

    @property
    def admission_facts(self) -> tuple[bool, ...]:
        """Return the complete static fact tuple in its canonical order."""

        return (
            self.policy_allowed,
            self.trust_satisfied,
            self.backend_kind_allowed,
            self.transport_allowed,
            self.locality_allowed,
            self.network_allowed,
            self.data_handling_allowed,
        )

    @property
    def admitted(self) -> bool:
        """Whether every static policy-admission fact is satisfied."""

        return all(self.admission_facts)


def evaluate_policy_admission(
    implementation: ImplementationSpec,
    policy: ResolutionPolicy,
    *,
    backends: Mapping[str, BackendSpec],
) -> PolicyAdmissionFacts:
    """Evaluate one exact implementation against policy and registered backend facts.

    Input-dependent implementation restrictions, current availability, and user preferences are
    deliberately outside this evaluator. Missing or contradictory backend records fail closed and
    are represented as false facts rather than lookup errors.
    """

    rule = next(
        (
            item
            for item in policy.capability_rules
            if item.capability == implementation.capability
        ),
        None,
    )
    policy_entry = None
    if rule is not None:
        policy_entry = next(
            (
                item
                for item in rule.implementations
                if item.implementation == implementation.ref
            ),
            None,
        )
    policy_allowed = policy_entry is not None
    priority = None if policy_entry is None else policy_entry.priority

    required_trust = set(() if rule is None else rule.required_trust_dimensions)
    asserted_trust = {item.dimension for item in implementation.trust}
    satisfied_trust = tuple(
        sorted(required_trust & asserted_trust, key=lambda item: item.value)
    )
    missing_trust = tuple(
        sorted(required_trust - asserted_trust, key=lambda item: item.value)
    )

    role_rules = {} if rule is None else {item.role: item for item in rule.backend_roles}
    backend_kind_allowed = True
    transport_allowed = True
    locality_allowed = True
    network_allowed = True
    data_handling_allowed = True
    for binding in implementation.backend_bindings:
        backend = backends.get(binding.backend.spec_hash)
        role_rule = role_rules.get(binding.role)
        if backend is None or backend.ref != binding.backend or role_rule is None:
            backend_kind_allowed = False
            transport_allowed = False
            locality_allowed = False
            network_allowed = False
            data_handling_allowed = False
            continue
        exact_allowed = (
            not role_rule.allowed_backends
            or binding.backend in role_rule.allowed_backends
        )
        backend_kind_allowed &= exact_allowed and backend.kind in role_rule.allowed_kinds
        transport_allowed &= binding.transport in role_rule.allowed_transports
        locality_allowed &= binding.locality in role_rule.allowed_localities
        network_allowed &= role_rule.network_allowed or not backend.requires_network
        data_handling_allowed &= (
            backend.data_boundary.egress in role_rule.allowed_data_egress
        )

    return PolicyAdmissionFacts(
        priority=priority,
        policy_allowed=policy_allowed,
        trust_satisfied=not missing_trust,
        backend_kind_allowed=backend_kind_allowed,
        transport_allowed=transport_allowed,
        locality_allowed=locality_allowed,
        network_allowed=network_allowed,
        data_handling_allowed=data_handling_allowed,
        satisfied_trust_dimensions=satisfied_trust,
        missing_trust_dimensions=missing_trust,
    )


__all__ = ["PolicyAdmissionFacts", "evaluate_policy_admission"]
