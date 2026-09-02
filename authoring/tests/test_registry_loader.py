from __future__ import annotations

import builtins
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from defined_quant.registry import (
    RegistryError,
    inspect_method,
    load_registry,
    search_methods,
)
from defined_quant_protocol.registry import (
    AdapterSpec,
    BackendSpec,
    CapabilitySpec,
    ImplementationSpec,
    MethodSpec,
    derive_availability_requirements,
)

from authoring.build_registry_bundle import registry_bundle, write_registry_bundle

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "registry"


def _walk_keys(value: Any) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(value) + tuple(
            key for item in value.values() for key in _walk_keys(item)
        )
    if isinstance(value, list):
        return tuple(key for item in value for key in _walk_keys(item))
    return ()


def test_registry_loads_exact_protocol_specs_and_expands_schema_fields() -> None:
    registry = load_registry(root=REGISTRY)
    typed = registry.as_protocol_registry()

    assert all(isinstance(item, MethodSpec) for item in typed.methods)
    assert all(isinstance(item, CapabilitySpec) for item in typed.capabilities)
    assert all(isinstance(item, BackendSpec) for item in typed.backends)
    assert all(isinstance(item, AdapterSpec) for item in typed.adapters)
    assert all(isinstance(item, ImplementationSpec) for item in typed.implementations)

    method = next(
        item for item in typed.methods if item.id == "dq.market_data.simple_return"
    )
    capability = next(item for item in typed.capabilities if item.id == "returns.simple")
    assert method.recipe.steps[0].capability == capability.ref
    assert method.input_schema["properties"]["prices"] == capability.input_schema[
        "properties"
    ]["prices"]
    assert method.output_schema["properties"]["returns"] == capability.output_schema[
        "properties"
    ]["returns"]
    assert "x-defined-quant-capability-field" not in _walk_keys(
        method.model_dump(mode="json")
    )
    assert {binding.capability for binding in method.schema_bindings} == {capability.ref}
    method_record = next(item for item in registry.methods if item.id == method.id)
    implementation = next(
        item
        for item in typed.implementations
        if item.id == "dq_native.simple_return"
    )
    implementation_record = next(
        item for item in registry.implementations if item.id == implementation.id
    )
    assert method_record.ref == method.ref.model_dump(mode="json")
    assert implementation_record.ref == implementation.ref.model_dump(mode="json")


def test_registry_discovery_and_inspection_are_import_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "adapters" or name.startswith("adapters."):
            raise AssertionError(f"adapter code import attempted: {name}")
        if name == "implementations" or name.startswith("implementations."):
            raise AssertionError(f"implementation code import attempted: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    registry = load_registry(root=REGISTRY)
    results = search_methods("simple price returns", registry=registry)
    inspection = inspect_method(
        "dq.market_data.simple_return",
        version="1.0.0",
        registry=registry,
    )

    assert results.hits[0].method.id == "dq.market_data.simple_return"
    assert [item.id for item in inspection.capabilities] == ["returns.simple"]
    assert [item.id for item in inspection.implementations] == [
        "dq_native.simple_return"
    ]
    assert {item.subject_kind for item in inspection.evidence} == {
        "adapter",
        "capability",
        "implementation",
        "method",
    }


def test_registry_rejects_duplicate_exact_identity(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    duplicate = target / "methods" / "performance" / "duplicate"
    duplicate.mkdir(parents=True)
    shutil.copy2(REGISTRY / "methods/market_data/simple_return/method.yaml", duplicate)

    with pytest.raises(RegistryError, match="duplicate method identity"):
        load_registry(root=target)


def test_registry_rejects_method_category_missing_from_taxonomy(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    taxonomy_path = target / "taxonomy/categories.yaml"
    taxonomy = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
    assert isinstance(taxonomy, dict)
    taxonomy["categories"] = [
        item for item in taxonomy["categories"] if item["id"] != "market_data"
    ]
    taxonomy_path.write_text(yaml.safe_dump(taxonomy, sort_keys=False), encoding="utf-8")

    with pytest.raises(RegistryError, match="unregistered taxonomy category"):
        load_registry(root=target)


def test_registry_rejects_missing_exact_capability_reference(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    method_path = target / "methods/market_data/simple_return/method.yaml"
    method = yaml.safe_load(method_path.read_text(encoding="utf-8"))
    assert isinstance(method, dict)
    method["recipe"]["steps"][0]["capability"]["id"] = "returns.missing"
    method_path.write_text(yaml.safe_dump(method, sort_keys=False), encoding="utf-8")

    with pytest.raises(RegistryError, match="unregistered capability returns.missing"):
        load_registry(root=target)


def test_registry_rejects_adapter_level_artifact_authority(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    adapter_path = target / "adapters/dq_native/simple_return.yaml"
    implementation_path = target / "implementations/dq_native/simple_return.yaml"
    adapter = yaml.safe_load(adapter_path.read_text(encoding="utf-8"))
    implementation = yaml.safe_load(implementation_path.read_text(encoding="utf-8"))
    assert isinstance(adapter, dict)
    assert isinstance(implementation, dict)
    adapter["artifact_requirement"] = implementation["artifact"]
    adapter_path.write_text(yaml.safe_dump(adapter, sort_keys=False), encoding="utf-8")

    with pytest.raises(RegistryError, match="not a valid AdapterSpec"):
        load_registry(root=target)


def test_registry_rejects_duplicate_authored_backend_availability_facts(
    tmp_path: Path,
) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    implementation_path = target / "implementations/dq_native/simple_return.yaml"
    implementation = yaml.safe_load(implementation_path.read_text(encoding="utf-8"))
    assert isinstance(implementation, dict)
    implementation["availability_requirements"] = {
        "installation_required": True,
        "credentials_required": False,
        "licence_required": False,
        "entitlement_required": False,
        "reachability_required": False,
    }
    implementation_path.write_text(
        yaml.safe_dump(implementation, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="not a valid ImplementationSpec"):
        load_registry(root=target)


def test_registry_rejects_implementation_artifact_for_another_distribution(
    tmp_path: Path,
) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    implementation_path = target / "implementations/dq_native/simple_return.yaml"
    implementation = yaml.safe_load(implementation_path.read_text(encoding="utf-8"))
    assert isinstance(implementation, dict)
    implementation["artifact"]["distribution"] = "another-adapter-distribution"
    implementation_path.write_text(
        yaml.safe_dump(implementation, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="artifact distribution contradicts adapter"):
        load_registry(root=target)


def test_registry_rejects_backend_locality_contradiction(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    implementation_path = target / "implementations/dq_native/simple_return.yaml"
    implementation = yaml.safe_load(implementation_path.read_text(encoding="utf-8"))
    assert isinstance(implementation, dict)
    implementation["backend_bindings"][0]["locality"] = "remote"
    implementation_path.write_text(
        yaml.safe_dump(implementation, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="binding locality contradicts backend"):
        load_registry(root=target)


def test_availability_requirements_are_derived_from_backend_boundaries(
    tmp_path: Path,
) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    backend_path = target / "backends/dq_native.yaml"
    backend = yaml.safe_load(backend_path.read_text(encoding="utf-8"))
    assert isinstance(backend, dict)
    backend.update(
        {
            "kind": "data_provider",
            "requires_network": True,
            "credential_requirement": "host_capability",
            "licensing": {"kind": "commercial", "identifier": "test-license"},
            "entitlement": "required",
            "data_boundary": {
                "egress": "remote_service",
                "accepts_restricted_data": False,
            },
        }
    )
    backend_path.write_text(yaml.safe_dump(backend, sort_keys=False), encoding="utf-8")
    implementation_path = target / "implementations/dq_native/simple_return.yaml"
    implementation = yaml.safe_load(implementation_path.read_text(encoding="utf-8"))
    assert isinstance(implementation, dict)
    implementation["dependency_requirements"] = [
        {
            "id": "native_runtime",
            "description": "A test-only exact runtime dependency probe.",
        }
    ]
    implementation_path.write_text(
        yaml.safe_dump(implementation, sort_keys=False),
        encoding="utf-8",
    )

    registry = load_registry(root=target).as_protocol_registry()
    selected = next(
        item for item in registry.implementations if item.id == "dq_native.simple_return"
    )
    requirements = derive_availability_requirements(selected, registry.backends)

    assert requirements.installation_required is True
    assert requirements.dependencies_required is True
    assert requirements.credentials_required is True
    assert requirements.licence_required is True
    assert requirements.entitlement_required is True
    assert requirements.reachability_required is True


def test_registry_rejects_incompatible_method_input_result_passthrough(
    tmp_path: Path,
) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    method_path = target / "methods/market_data/simple_return/method.yaml"
    method = yaml.safe_load(method_path.read_text(encoding="utf-8"))
    assert isinstance(method, dict)
    method["output_schema"]["properties"]["declared_frequency"]["anyOf"][0]["enum"] = [
        "daily"
    ]
    method_path.write_text(yaml.safe_dump(method, sort_keys=False), encoding="utf-8")

    with pytest.raises(RegistryError, match="passes input declared_frequency.*incompatible"):
        load_registry(root=target)


def test_registry_rejects_unknown_and_secret_evidence_fields(tmp_path: Path) -> None:
    unknown_root = tmp_path / "unknown" / "registry"
    shutil.copytree(REGISTRY, unknown_root)
    evidence_path = unknown_root / "evidence/adapters/dq_native/simple_return.yaml"
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    assert isinstance(evidence, dict)
    evidence["unreviewed_claim"] = True
    evidence_path.write_text(
        yaml.safe_dump(evidence, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="invalid closed evidence record"):
        load_registry(root=unknown_root)

    secret_root = tmp_path / "secret" / "registry"
    shutil.copytree(REGISTRY, secret_root)
    evidence_path = secret_root / "evidence/adapters/dq_native/simple_return.yaml"
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    assert isinstance(evidence, dict)
    evidence["evidence"][0]["refresh_token"] = "not-allowed"
    evidence_path.write_text(
        yaml.safe_dump(evidence, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="prohibited secret-bearing field"):
        load_registry(root=secret_root)


def test_registry_rejects_cross_subject_adapter_evidence_reference(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    adapter_path = target / "adapters/dq_native/simple_return.yaml"
    adapter = yaml.safe_load(adapter_path.read_text(encoding="utf-8"))
    assert isinstance(adapter, dict)
    adapter["evidence_refs"] = [
        {
            "id": "evidence.method.dq.market_data.simple_return",
            "version": "1.0.0",
        }
    ]
    adapter_path.write_text(yaml.safe_dump(adapter, sort_keys=False), encoding="utf-8")

    with pytest.raises(RegistryError, match="must bind exact adapter"):
        load_registry(root=target)


def test_registry_rejects_unknown_conformance_fields(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    conformance_path = target / "capabilities/returns/simple/conformance.yaml"
    conformance = yaml.safe_load(conformance_path.read_text(encoding="utf-8"))
    assert isinstance(conformance, dict)
    conformance["provider_override"] = "untrusted"
    conformance_path.write_text(
        yaml.safe_dump(conformance, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="invalid closed evidence record"):
        load_registry(root=target)


def test_registry_rejects_cross_subject_implementation_trust(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    implementation_path = target / "implementations/dq_native/simple_return.yaml"
    implementation = yaml.safe_load(implementation_path.read_text(encoding="utf-8"))
    assert isinstance(implementation, dict)
    implementation["trust"][0]["evidence"] = {
        "id": "evidence.adapter.dq_native.simple_return",
        "version": "1.0.0",
    }
    implementation_path.write_text(
        yaml.safe_dump(implementation, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="must bind exact implementation"):
        load_registry(root=target)


def test_registry_rejects_conformance_bound_to_another_capability(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    conformance_path = target / "capabilities/returns/simple/conformance.yaml"
    conformance = yaml.safe_load(conformance_path.read_text(encoding="utf-8"))
    assert isinstance(conformance, dict)
    conformance["capability"] = {"id": "returns.log", "version": "1.0.0"}
    conformance_path.write_text(
        yaml.safe_dump(conformance, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="exact capability in its directory"):
        load_registry(root=target)


def test_registry_rejects_implementation_evidence_for_another_capability(
    tmp_path: Path,
) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    evidence_path = target / "evidence/implementations/dq_native/simple_return.yaml"
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    assert isinstance(evidence, dict)
    evidence["conformance"]["capability"] = {
        "id": "returns.log",
        "version": "1.0.0",
    }
    evidence["conformance"]["suite"] = (
        "registry/capabilities/returns/log/conformance.yaml"
    )
    evidence_path.write_text(
        yaml.safe_dump(evidence, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="exact implementation capability"):
        load_registry(root=target)


def test_compiled_registry_loads_without_pyyaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled_root = tmp_path / "registry_data"
    output = compiled_root / "registry.json"
    value = registry_bundle(REGISTRY)
    write_registry_bundle(value, output)
    first_bytes = output.read_bytes()
    write_registry_bundle(registry_bundle(REGISTRY), output)
    assert output.read_bytes() == first_bytes

    authored = load_registry(root=REGISTRY)
    original_import = builtins.__import__

    def without_yaml(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml" or name.startswith("yaml."):
            raise AssertionError("compiled registry attempted to import PyYAML")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_yaml)
    compiled = load_registry(root=compiled_root)

    assert compiled.registry_hash == authored.registry_hash
    assert compiled.methods[0].ref == authored.methods[0].ref
    assert compiled.implementations[0].ref == authored.implementations[0].ref


def test_bundle_build_refuses_cross_record_invalid_registry(tmp_path: Path) -> None:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    implementation_path = target / "implementations/dq_native/simple_return.yaml"
    implementation = yaml.safe_load(implementation_path.read_text(encoding="utf-8"))
    assert isinstance(implementation, dict)
    implementation["capability"] = {"id": "returns.log", "version": "1.0.0"}
    implementation_path.write_text(
        yaml.safe_dump(implementation, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError):
        registry_bundle(target)
