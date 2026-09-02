"""Authored, metadata-only registry loading, discovery, and inspection.

The registry is deliberately data-only.  Loading, searching, and inspecting records never imports
an adapter, implementation, dispatch module, or provider SDK.  Executable dispatch metadata may be
present in a trusted AdapterSpec, but this module treats it as inert JSON.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, TypeVar, cast

from defined_quant.schema_validation import schema_is_supported, schema_matches
from defined_quant_protocol import canonical_hash, canonical_json_bytes
from defined_quant_protocol.evidence import (
    AdapterEvidence,
    CapabilityConformance,
    EvidenceSpec,
    ImplementationEvidence,
    MethodEvidence,
)
from defined_quant_protocol.ports import (
    PORT_SCHEMA_KEY,
    PortDirection,
    PortProvenanceRequirement,
    SemanticPort,
)
from defined_quant_protocol.registry import (
    AdapterSpec,
    BackendSpec,
    CapabilitySpec,
    ImplementationSpec,
    MethodSpec,
    derive_availability_requirements,
)
from pydantic import ValidationError

EntityKind: TypeAlias = Literal[
    "method",
    "capability",
    "backend",
    "adapter",
    "implementation",
]
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
EntityKey: TypeAlias = tuple[str, str]
RegistrySpec: TypeAlias = (
    MethodSpec | CapabilitySpec | BackendSpec | AdapterSpec | ImplementationSpec
)
EvidenceProtocolSpec: TypeAlias = EvidenceSpec | CapabilityConformance
RegistrySpecT = TypeVar("RegistrySpecT", bound=RegistrySpec)

_ENTITY_KINDS: tuple[EntityKind, ...] = (
    "method",
    "capability",
    "backend",
    "adapter",
    "implementation",
)
_ENTITY_GLOBS: Mapping[EntityKind, str] = MappingProxyType(
    {
        "method": "methods/*/*/method.yaml",
        "capability": "capabilities/*/*/capability.yaml",
        "backend": "backends/**/*.yaml",
        "adapter": "adapters/**/*.yaml",
        "implementation": "implementations/**/*.yaml",
    }
)
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,7}$")
_METHOD_ID = re.compile(r"^dq\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_RESERVED_DERIVED_FIELDS = frozenset(
    {"contract_hash", "record_hash", "schema_bindings", "spec_hash"}
)
_CAPABILITY_FIELD_DIRECTIVE = "x-defined-quant-capability-field"
_SECRET_VALUE_FIELDS = frozenset(
    {
        "api_key",
        "connection_string",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "calculate",
        "compute",
        "for",
        "from",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "that",
        "the",
        "this",
        "to",
        "use",
        "want",
        "with",
    }
)
_POSITIVE_FIELDS: tuple[tuple[str, int], ...] = (
    ("id", 14),
    ("title", 14),
    ("discovery.aliases", 13),
    ("discovery.intents", 12),
    ("discovery.output_concepts", 11),
    ("discovery.input_concepts", 10),
    ("discovery.tags", 8),
    ("tags", 8),
    ("summary", 6),
    ("discovery.use_when", 4),
    ("category", 3),
)
_BOUNDARY_FIELDS = (
    "discovery.do_not_use_when",
    "discovery.unsupported_scope",
    "limitations",
)


class RegistryError(ValueError):
    """A deterministic authored-registry validation failure."""


def _json_value(value: Any, *, path: str = "$") -> JsonValue:
    if value is None or isinstance(value, str | bool | int):
        return cast(JsonValue, value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RegistryError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RegistryError(f"{path} contains a non-string object key")
            result[key] = _json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise RegistryError(f"{path} contains non-JSON value {type(value).__name__}")


def _freeze(value: JsonValue) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return cast(JsonValue, value)


def _load_mapping(path: Path) -> dict[str, JsonValue]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RegistryError(f"cannot read registry record: {path}") from exc
    try:
        loaded: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RegistryError(
                f"{path} is not JSON-compatible YAML; install PyYAML for authored YAML"
            ) from exc
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RegistryError(f"malformed YAML registry record: {path}") from exc
    normalized = _json_value(loaded)
    if not isinstance(normalized, dict):
        raise RegistryError(f"registry record must contain one object: {path}")
    return normalized


def _validate_no_secret_values(value: JsonValue, *, path: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in _SECRET_VALUE_FIELDS:
                raise RegistryError(f"{path} contains prohibited secret-bearing field {key!r}")
            _validate_no_secret_values(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_no_secret_values(nested, path=f"{path}[{index}]")


def _record_order(record: RegistryRecord) -> tuple[bytes, str, bytes]:
    return (record.id.encode("utf-8"), record.version, record.record_hash.encode("ascii"))


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    """One immutable, protocol-validated registry entity.

    ``data`` is the canonical protocol model dump after loader-only reference and schema-field
    expansion.  ``spec`` is the corresponding closed protocol model.  During loading, private
    intermediate records temporarily have no spec; no such record is returned to callers.
    """

    kind: EntityKind
    id: str
    version: str
    data: Mapping[str, Any]
    record_hash: str
    source_path: Path = field(compare=False, repr=False)
    spec: RegistrySpec | None = field(default=None, compare=False)

    @property
    def key(self) -> EntityKey:
        return (self.id, self.version)

    @property
    def ref(self) -> dict[str, str]:
        if self.spec is None:
            raise RegistryError(f"{self.kind} {self.id} has not been protocol validated")
        value = self.spec.ref.model_dump(mode="json")
        return cast(dict[str, str], value)

    @property
    def identity_hash(self) -> str:
        reference = self.ref
        if "contract_hash" in reference:
            return reference["contract_hash"]
        return reference["spec_hash"]

    def as_dict(self) -> dict[str, JsonValue]:
        value = _thaw(self.data)
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One immutable evidence or conformance record bound to an exact entity."""

    subject_kind: EntityKind
    subject_id: str
    subject_version: str
    data: Mapping[str, Any]
    evidence_hash: str
    source_path: Path = field(compare=False, repr=False)
    id: str | None = None
    version: str | None = None
    spec: EvidenceProtocolSpec | None = field(default=None, compare=False)

    @property
    def subject_key(self) -> EntityKey:
        return (self.subject_id, self.subject_version)

    @property
    def ref(self) -> dict[str, Any]:
        if self.id is None or self.version is None:
            raise RegistryError("capability conformance does not have an EvidenceRef identity")
        return {
            "id": self.id,
            "version": self.version,
            "evidence_hash": self.evidence_hash,
            "subject": {
                "kind": self.subject_kind,
                "id": self.subject_id,
                "version": self.subject_version,
            },
        }

    def as_dict(self) -> dict[str, JsonValue]:
        value = _thaw(self.data)
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class MethodFilters:
    """Exact metadata filters for method discovery."""

    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    input_concepts: tuple[str, ...] = ()
    output_concepts: tuple[str, ...] = ()
    lifecycles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "categories",
            "tags",
            "intents",
            "input_concepts",
            "output_concepts",
            "lifecycles",
        ):
            raw = getattr(self, name)
            if len(raw) > 64:
                raise ValueError(f"{name} accepts at most 64 values")
            normalized = tuple(sorted({_normalize_filter(item) for item in raw}))
            object.__setattr__(self, name, normalized)


@dataclass(frozen=True, slots=True)
class MethodFieldMatch:
    field: str
    terms: tuple[str, ...]
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MethodSearchHit:
    method: RegistryRecord
    score: int
    positive_matches: tuple[MethodFieldMatch, ...]
    boundary_matches: tuple[MethodFieldMatch, ...]
    matched_terms: tuple[str, ...]
    unmatched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MethodSearchResults:
    query: str
    terms: tuple[str, ...]
    filters: MethodFilters
    total_matches: int
    hits: tuple[MethodSearchHit, ...]


@dataclass(frozen=True, slots=True)
class MethodInspection:
    """A registry-only join for one exact method and its registered support."""

    method: RegistryRecord
    capabilities: tuple[RegistryRecord, ...]
    implementations: tuple[RegistryRecord, ...]
    adapters: tuple[RegistryRecord, ...]
    backends: tuple[RegistryRecord, ...]
    evidence: tuple[EvidenceRecord, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": _record_projection(self.method),
            "capabilities": [_record_projection(item) for item in self.capabilities],
            "implementations": [
                _record_projection(item) for item in self.implementations
            ],
            "adapters": [_record_projection(item) for item in self.adapters],
            "backends": [_record_projection(item) for item in self.backends],
            "evidence": [
                {
                    "subject_kind": item.subject_kind,
                    "subject": {
                        "id": item.subject_id,
                        "version": item.subject_version,
                    },
                    "evidence_hash": item.evidence_hash,
                    "record": item.as_dict(),
                }
                for item in self.evidence
            ],
        }


@dataclass(frozen=True, slots=True)
class ProtocolRegistry:
    """The exact typed registry consumed by planning and execution code."""

    methods: tuple[MethodSpec, ...]
    capabilities: tuple[CapabilitySpec, ...]
    backends: tuple[BackendSpec, ...]
    adapters: tuple[AdapterSpec, ...]
    implementations: tuple[ImplementationSpec, ...]


@dataclass(frozen=True, slots=True)
class Registry:
    """One deterministic, immutable snapshot of authored registry metadata."""

    root: Path = field(compare=False, repr=False)
    taxonomy: Mapping[str, Any]
    methods: tuple[RegistryRecord, ...]
    capabilities: tuple[RegistryRecord, ...]
    backends: tuple[RegistryRecord, ...]
    adapters: tuple[RegistryRecord, ...]
    implementations: tuple[RegistryRecord, ...]
    evidence: tuple[EvidenceRecord, ...]
    registry_hash: str

    def records(self, kind: EntityKind) -> tuple[RegistryRecord, ...]:
        by_kind = {
            "method": self.methods,
            "capability": self.capabilities,
            "backend": self.backends,
            "adapter": self.adapters,
            "implementation": self.implementations,
        }
        return by_kind[kind]

    def get(
        self,
        kind: EntityKind,
        identifier: str,
        version: str | None = None,
    ) -> RegistryRecord:
        matches = tuple(
            item
            for item in self.records(kind)
            if item.id == identifier and (version is None or item.version == version)
        )
        if not matches:
            suffix = f" at version {version}" if version is not None else ""
            raise RegistryError(f"{kind} is not registered: {identifier}{suffix}")
        if len(matches) != 1:
            raise RegistryError(f"{kind} version is ambiguous: {identifier}")
        return matches[0]

    def as_protocol_registry(self) -> ProtocolRegistry:
        """Return only closed protocol models, preserving canonical registry order."""

        return ProtocolRegistry(
            methods=tuple(_validated_spec(item, MethodSpec) for item in self.methods),
            capabilities=tuple(
                _validated_spec(item, CapabilitySpec) for item in self.capabilities
            ),
            backends=tuple(_validated_spec(item, BackendSpec) for item in self.backends),
            adapters=tuple(_validated_spec(item, AdapterSpec) for item in self.adapters),
            implementations=tuple(
                _validated_spec(item, ImplementationSpec)
                for item in self.implementations
            ),
        )


def _validated_spec(
    record: RegistryRecord,
    expected: type[RegistrySpecT],
) -> RegistrySpecT:
    if not isinstance(record.spec, expected):
        raise RegistryError("registry contains an unvalidated protocol record")
    return record.spec


def _record_projection(record: RegistryRecord) -> dict[str, JsonValue]:
    result = record.as_dict()
    result["record_hash"] = record.record_hash
    for name, value in record.ref.items():
        if name not in {"id", "version"}:
            result[name] = value
    return result


def _registry_root(root: str | Path | None) -> Path:
    if root is not None:
        candidate = Path(root).resolve()
        nested = candidate / "registry"
        selected = nested if nested.is_dir() else candidate
        if not selected.is_dir():
            raise RegistryError(f"registry root is not a directory: {selected}")
        return selected

    source = Path(__file__).resolve().parents[2] / "registry"
    if source.is_dir():
        return source
    installed = Path(__file__).resolve().parent.parent / "registry_data"
    if installed.is_dir():
        return installed
    raise RegistryError("authored registry is not installed")


def _sorted_paths(paths: Iterable[Path], root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            paths,
            key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
        )
    )


def _load_entity(kind: EntityKind, path: Path) -> RegistryRecord:
    data = _load_mapping(path)
    return _load_entity_data(kind, data, path)


def _load_entity_data(
    kind: EntityKind,
    data: dict[str, JsonValue],
    source_path: Path,
) -> RegistryRecord:
    if set(data) & _RESERVED_DERIVED_FIELDS:
        raise RegistryError(f"{source_path} authors a derived hash field")
    _validate_no_secret_values(data, path=source_path.as_posix())
    schema_version = data.get("schema_version")
    identifier = data.get("id")
    version = data.get("version")
    expected_schema_version = 2 if kind in {"method", "capability", "implementation"} else 1
    if schema_version != expected_schema_version or isinstance(schema_version, bool):
        raise RegistryError(
            f"{source_path} must declare schema_version: {expected_schema_version}"
        )
    if not isinstance(identifier, str) or _ENTITY_ID.fullmatch(identifier) is None:
        raise RegistryError(f"{source_path} has an invalid {kind} id")
    if kind == "method" and _METHOD_ID.fullmatch(identifier) is None:
        raise RegistryError(f"{source_path} has an invalid method id")
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        raise RegistryError(f"{source_path} has an invalid semantic version")
    record_hash = canonical_hash(data, domain=f"registry.{kind}.spec")
    frozen = _freeze(data)
    assert isinstance(frozen, Mapping)
    return RegistryRecord(
        kind=kind,
        id=identifier,
        version=version,
        data=cast(Mapping[str, Any], frozen),
        record_hash=record_hash,
        source_path=source_path,
    )


def _protocol_record(record: RegistryRecord, data: dict[str, JsonValue]) -> RegistryRecord:
    """Validate one expanded record and derive identity through the protocol model."""

    if record.kind in {"method", "capability"}:
        for field_name in ("input_schema", "output_schema"):
            schema = data.get(field_name)
            if not isinstance(schema, Mapping) or not schema_is_supported(schema):
                raise RegistryError(
                    f"{record.source_path} {field_name} is outside the executable schema subset"
                )

    model_type: type[RegistrySpec]
    if record.kind == "method":
        model_type = MethodSpec
    elif record.kind == "capability":
        model_type = CapabilitySpec
    elif record.kind == "backend":
        model_type = BackendSpec
    elif record.kind == "adapter":
        model_type = AdapterSpec
    else:
        model_type = ImplementationSpec
    try:
        spec = model_type.model_validate(data)
    except ValidationError as exc:
        raise RegistryError(
            f"{record.source_path} is not a valid {model_type.__name__}: {exc}"
        ) from exc
    if isinstance(spec, MethodSpec | CapabilitySpec):
        record_hash = spec.record_hash
    else:
        record_hash = spec.spec_hash
    canonical = _json_value(spec.model_dump(mode="json"))
    assert isinstance(canonical, dict)
    frozen = _freeze(canonical)
    assert isinstance(frozen, Mapping)
    return RegistryRecord(
        kind=record.kind,
        id=record.id,
        version=record.version,
        data=cast(Mapping[str, Any], frozen),
        record_hash=record_hash,
        source_path=record.source_path,
        spec=spec,
    )


def _ref(value: Any, *, name: str) -> EntityKey:
    if not isinstance(value, Mapping):
        raise RegistryError(f"{name} must be an exact object reference")
    identifier = value.get("id")
    version = value.get("version")
    if not isinstance(identifier, str) or not isinstance(version, str):
        raise RegistryError(f"{name} must contain string id and version")
    return (identifier, version)


def _merge_capability_definitions(
    target_schema: dict[str, JsonValue],
    capability_schema: Mapping[str, Any],
    capability: RegistryRecord,
) -> None:
    definitions = capability_schema.get("$defs")
    if definitions is None:
        return
    if not isinstance(definitions, Mapping):
        raise RegistryError(f"capability {capability.id} schema $defs must be an object")
    target = target_schema.setdefault("$defs", {})
    if not isinstance(target, dict):
        raise RegistryError("method schema $defs must be an object")
    for name, value in definitions.items():
        if not isinstance(name, str):
            raise RegistryError(f"capability {capability.id} has a non-string $defs key")
        normalized = _json_value(value)
        prior = target.get(name)
        if prior is not None and prior != normalized:
            raise RegistryError(
                f"method schema has conflicting capability definition {name}"
            )
        target[name] = normalized


def _expanded_method_schema(
    method: RegistryRecord,
    *,
    schema_field: Literal["input_schema", "output_schema"],
    direction: Literal["input", "output"],
    capability_index: Mapping[EntityKey, RegistryRecord],
    recipe_capabilities: frozenset[EntityKey],
    bindings: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    raw_schema = _thaw(method.data.get(schema_field))
    if not isinstance(raw_schema, dict):
        raise RegistryError(f"method {method.id} {schema_field} must be an object")
    properties = raw_schema.get("properties")
    if not isinstance(properties, dict):
        raise RegistryError(f"method {method.id} {schema_field} must declare properties")
    expanded_properties: dict[str, JsonValue] = {}
    for property_name, property_schema in properties.items():
        if not isinstance(property_name, str) or not isinstance(property_schema, dict):
            raise RegistryError(
                f"method {method.id} {schema_field} properties must be schema objects"
            )
        directive = property_schema.get(_CAPABILITY_FIELD_DIRECTIVE)
        if directive is None:
            expanded_properties[property_name] = property_schema
            continue
        if set(property_schema) != {_CAPABILITY_FIELD_DIRECTIVE}:
            raise RegistryError(
                f"method {method.id} field {property_name} cannot override a capability field"
            )
        if not isinstance(directive, dict):
            raise RegistryError(
                f"method {method.id} field {property_name} capability directive must be an object"
            )
        if set(directive) != {"capability", "direction", "field"}:
            raise RegistryError(
                f"method {method.id} field {property_name} has a malformed capability directive"
            )
        capability_key = _ref(
            directive.get("capability"),
            name=f"method {method.id} field {property_name} capability",
        )
        if capability_key not in recipe_capabilities:
            raise RegistryError(
                f"method {method.id} field {property_name} references a capability "
                "outside its recipe"
            )
        capability = capability_index.get(capability_key)
        if capability is None:
            raise RegistryError(
                f"method {method.id} field {property_name} references unregistered capability "
                f"{capability_key[0]}@{capability_key[1]}"
            )
        declared_direction = directive.get("direction")
        capability_field = directive.get("field")
        if declared_direction != direction or not isinstance(capability_field, str):
            raise RegistryError(
                f"method {method.id} field {property_name} capability direction or field is invalid"
            )
        capability_schema = capability.data.get(f"{direction}_schema")
        if not isinstance(capability_schema, Mapping):
            raise RegistryError(
                f"capability {capability.id} has no {direction} schema"
            )
        capability_properties = capability_schema.get("properties")
        selected_property = (
            capability_properties.get(capability_field)
            if isinstance(capability_properties, Mapping)
            else None
        )
        if not isinstance(selected_property, Mapping):
            raise RegistryError(
                f"capability {capability.id} has no {direction} field {capability_field}"
            )
        selected_json = _json_value(selected_property)
        assert isinstance(selected_json, dict)
        expanded_properties[property_name] = selected_json
        capability_ref = _json_value(capability.ref)
        assert isinstance(capability_ref, dict)
        bindings.append(
            {
                "method_field": property_name,
                "direction": direction,
                "capability": capability_ref,
                "capability_field": capability_field,
            }
        )
        _merge_capability_definitions(raw_schema, capability_schema, capability)
    raw_schema["properties"] = expanded_properties
    return raw_schema


def _expand_method(
    method: RegistryRecord,
    capability_index: Mapping[EntityKey, RegistryRecord],
) -> RegistryRecord:
    recipe_capabilities = frozenset(_recipe_capabilities(method))
    data = method.as_dict()
    recipe = data.get("recipe")
    assert isinstance(recipe, dict)
    steps = recipe.get("steps")
    assert isinstance(steps, list)
    expanded_steps: list[JsonValue] = []
    for step in steps:
        assert isinstance(step, dict)
        capability_key = _ref(
            step.get("capability"),
            name=f"method {method.id} recipe capability",
        )
        capability = capability_index.get(capability_key)
        if capability is None:
            raise RegistryError(
                f"method {method.id} references unregistered capability "
                f"{capability_key[0]}@{capability_key[1]}"
            )
        expanded_step = dict(step)
        capability_ref = _json_value(capability.ref)
        expanded_step["capability"] = capability_ref
        expanded_steps.append(expanded_step)
    recipe["steps"] = expanded_steps
    data["recipe"] = recipe
    bindings: list[dict[str, JsonValue]] = []
    data["input_schema"] = _expanded_method_schema(
        method,
        schema_field="input_schema",
        direction="input",
        capability_index=capability_index,
        recipe_capabilities=recipe_capabilities,
        bindings=bindings,
    )
    data["output_schema"] = _expanded_method_schema(
        method,
        schema_field="output_schema",
        direction="output",
        capability_index=capability_index,
        recipe_capabilities=recipe_capabilities,
        bindings=bindings,
    )
    bindings.sort(
        key=lambda item: (
            str(item.get("direction")),
            str(item.get("method_field")),
        )
    )
    data["schema_bindings"] = cast(JsonValue, bindings)
    return _protocol_record(method, data)


def _expand_record_references(
    record: RegistryRecord,
    references: Mapping[str, tuple[EntityKind, Mapping[EntityKey, RegistryRecord]]],
) -> RegistryRecord:
    data = record.as_dict()
    for field_name, (kind, index) in references.items():
        value = data.get(field_name)
        if value is None:
            continue
        key = _ref(value, name=f"{record.kind} {record.id} {field_name}")
        target = index.get(key)
        if target is None:
            raise RegistryError(
                f"{record.id} references unregistered {kind} {key[0]}@{key[1]}"
            )
        assert isinstance(value, dict)
        exact_ref = target.ref
        if not set(value) <= set(exact_ref):
            raise RegistryError(f"{record.id} {field_name} reference has unknown fields")
        for hash_field in ("contract_hash", "spec_hash"):
            authored_hash = value.get(hash_field)
            if authored_hash is not None and authored_hash != exact_ref.get(hash_field):
                raise RegistryError(
                    f"{record.id} {field_name} {hash_field} does not match the registry"
                )
        data[field_name] = _json_value(exact_ref)
    return RegistryRecord(
        kind=record.kind,
        id=record.id,
        version=record.version,
        data=cast(Mapping[str, Any], _freeze(data)),
        record_hash=record.record_hash,
        source_path=record.source_path,
    )


def _expand_backend_bindings(
    record: RegistryRecord,
    backends: Mapping[EntityKey, RegistryRecord],
) -> RegistryRecord:
    data = record.as_dict()
    bindings = data.get("backend_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise RegistryError(f"implementation {record.id} requires backend_bindings")
    expanded_bindings: list[JsonValue] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            raise RegistryError(f"implementation {record.id} backend bindings must be objects")
        backend_value = binding.get("backend")
        key = _ref(
            backend_value,
            name=f"implementation {record.id} backend binding",
        )
        backend = backends.get(key)
        if backend is None:
            raise RegistryError(
                f"{record.id} references unregistered backend {key[0]}@{key[1]}"
            )
        if not isinstance(backend_value, Mapping) or not set(backend_value) <= set(backend.ref):
            raise RegistryError(f"{record.id} backend reference has unknown fields")
        authored_hash = backend_value.get("spec_hash")
        if authored_hash is not None and authored_hash != backend.ref["spec_hash"]:
            raise RegistryError(f"{record.id} backend spec hash does not match the registry")
        expanded_binding = dict(binding)
        expanded_binding["backend"] = _json_value(backend.ref)
        expanded_bindings.append(expanded_binding)
    data["backend_bindings"] = expanded_bindings
    frozen = _freeze(data)
    assert isinstance(frozen, Mapping)
    return RegistryRecord(
        kind=record.kind,
        id=record.id,
        version=record.version,
        data=cast(Mapping[str, Any], frozen),
        record_hash=record.record_hash,
        source_path=record.source_path,
    )


def _recipe_capabilities(method: RegistryRecord) -> tuple[EntityKey, ...]:
    recipe = method.data.get("recipe")
    if not isinstance(recipe, Mapping):
        raise RegistryError(f"method {method.id} has no recipe object")
    steps = recipe.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, str | bytes) or not steps:
        raise RegistryError(f"method {method.id} recipe must contain steps")
    result: list[EntityKey] = []
    step_ids: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise RegistryError(f"method {method.id} recipe step {index} must be an object")
        step_id = step.get("step_id", step.get("id"))
        if not isinstance(step_id, str) or not step_id:
            raise RegistryError(f"method {method.id} recipe step {index} has no id")
        if step_id in step_ids:
            raise RegistryError(f"method {method.id} has duplicate recipe step {step_id}")
        step_ids.add(step_id)
        result.append(
            _ref(step.get("capability"), name=f"method {method.id} step {step_id} capability")
        )
    return tuple(result)


def _schema_properties(
    schema: Mapping[str, Any],
    *,
    subject: str,
) -> Mapping[str, Mapping[str, Any]]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or any(
        not isinstance(field, str) or not isinstance(value, Mapping)
        for field, value in properties.items()
    ):
        raise RegistryError(f"{subject} schema properties are malformed")
    return cast(Mapping[str, Mapping[str, Any]], properties)


def _field_port(
    field_schema: Mapping[str, Any],
    *,
    subject: str,
) -> SemanticPort | None:
    raw = field_schema.get(PORT_SCHEMA_KEY)
    if raw is None:
        return None
    try:
        return SemanticPort.model_validate(raw)
    except ValidationError as exc:
        raise RegistryError(f"{subject} has invalid semantic-port metadata") from exc


def _ports_are_compatible(producer: SemanticPort, consumer: SemanticPort) -> bool:
    if (
        producer.direction != PortDirection.OUTPUT
        or consumer.direction != PortDirection.INPUT
    ):
        return False
    for dimension in (
        "concept",
        "unit",
        "shape",
        "cardinality",
        "convention",
        "ordering",
        "frequency",
    ):
        if getattr(producer, dimension) != getattr(consumer, dimension):
            return False
    if consumer.provenance_requirement == PortProvenanceRequirement.NOT_REQUIRED:
        return True
    if (
        consumer.provenance_requirement
        == PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND
    ):
        return producer.provenance_requirement in {
            PortProvenanceRequirement.SOURCE_BOUND,
            PortProvenanceRequirement.COMPONENT_BOUND,
            PortProvenanceRequirement.SOURCE_OR_COMPONENT_BOUND,
        }
    return producer.provenance_requirement == consumer.provenance_requirement


def _require_step_field_compatibility(
    *,
    method: MethodSpec,
    producer_step: Any,
    producer_capability: CapabilitySpec,
    producer_field: str,
    consumer_step: Any,
    consumer_capability: CapabilitySpec,
    consumer_field: str,
) -> None:
    output_properties = _schema_properties(
        producer_capability.output_schema,
        subject=f"capability {producer_capability.id} output",
    )
    input_properties = _schema_properties(
        consumer_capability.input_schema,
        subject=f"capability {consumer_capability.id} input",
    )
    source_schema = output_properties.get(producer_field)
    target_schema = input_properties.get(consumer_field)
    if source_schema is None:
        raise RegistryError(
            f"method {method.id} step {consumer_step.step_id} references unknown output "
            f"{producer_step.step_id}.{producer_field}"
        )
    if target_schema is None:
        raise RegistryError(
            f"method {method.id} step {consumer_step.step_id} binds unknown capability input "
            f"{consumer_field}"
        )
    producer_port = _field_port(
        source_schema,
        subject=f"{producer_capability.id}.{producer_field}",
    )
    consumer_port = _field_port(
        target_schema,
        subject=f"{consumer_capability.id}.{consumer_field}",
    )
    if producer_port is not None or consumer_port is not None:
        if (
            producer_port is None
            or consumer_port is None
            or not _ports_are_compatible(producer_port, consumer_port)
        ):
            raise RegistryError(
                f"method {method.id} step binding {producer_step.step_id}.{producer_field} "
                f"to {consumer_step.step_id}.{consumer_field} has incompatible semantic ports"
            )
        return
    if canonical_json_bytes(source_schema) != canonical_json_bytes(target_schema):
        raise RegistryError(
            f"method {method.id} step binding {producer_step.step_id}.{producer_field} "
            f"to {consumer_step.step_id}.{consumer_field} has no compatible typed contract"
        )


def _schema_contract_projection(value: Any, *, in_port: bool = False) -> Any:
    """Project a field schema to its executable data contract.

    Human descriptions and titles cannot affect compatibility. Semantic-port metadata remains
    binding except for its input/output direction, which necessarily changes when a Method
    publishes an input value as an output.
    """

    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, nested in value.items():
            if key in {"description", "title"}:
                continue
            if in_port and key == "direction":
                continue
            projected[key] = _schema_contract_projection(
                nested,
                in_port=key == PORT_SCHEMA_KEY,
            )
        return projected
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_schema_contract_projection(item, in_port=in_port) for item in value]
    return value


def _schemas_are_passthrough_compatible(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
) -> bool:
    return canonical_json_bytes(_schema_contract_projection(source)) == canonical_json_bytes(
        _schema_contract_projection(target)
    )


def _validate_method_recipe_contract(
    method: MethodSpec,
    capabilities: Mapping[EntityKey, RegistryRecord],
) -> None:
    capability_by_step: dict[str, CapabilitySpec] = {}
    step_by_id = {step.step_id: step for step in method.recipe.steps}
    for step in method.recipe.steps:
        capability_record = capabilities.get((step.capability.id, step.capability.version))
        if capability_record is None or capability_record.ref != step.capability.model_dump(
            mode="json"
        ):
            raise RegistryError(
                f"method {method.id} step {step.step_id} does not bind an exact capability"
            )
        capability_by_step[step.step_id] = _validated_spec(
            capability_record,
            CapabilitySpec,
        )

    schema_bindings = {
        (
            binding.direction,
            binding.method_field,
            binding.capability,
            binding.capability_field,
        )
        for binding in method.schema_bindings
    }
    used_schema_bindings: set[tuple[Any, ...]] = set()
    method_inputs = _schema_properties(
        method.input_schema,
        subject=f"method {method.id} input",
    )
    method_outputs = _schema_properties(
        method.output_schema,
        subject=f"method {method.id} output",
    )

    for default in method.defaults:
        if not schema_matches(
            default.value,
            method_inputs[default.field],
            method.input_schema,
        ):
            raise RegistryError(
                f"method {method.id} default for {default.field} does not match its schema"
            )
    for convention in method.conventions:
        field_schema = method_inputs[convention.field]
        if any(
            not schema_matches(value, field_schema, method.input_schema)
            for value in convention.allowed_values
        ):
            raise RegistryError(
                f"method {method.id} convention {convention.id} contains a value "
                "outside its field schema"
            )

    for step in method.recipe.steps:
        capability = capability_by_step[step.step_id]
        capability_inputs = _schema_properties(
            capability.input_schema,
            subject=f"capability {capability.id} input",
        )
        targets = {binding.target_field for binding in step.input_bindings}
        unknown_targets = targets - set(capability_inputs)
        required = capability.input_schema.get("required", [])
        required_fields = {
            field for field in required if isinstance(field, str)
        } if isinstance(required, Sequence) and not isinstance(required, str | bytes) else set()
        if unknown_targets:
            raise RegistryError(
                f"method {method.id} step {step.step_id} binds unknown capability inputs "
                f"{sorted(unknown_targets)}"
            )
        if not required_fields <= targets:
            raise RegistryError(
                f"method {method.id} step {step.step_id} omits required capability inputs "
                f"{sorted(required_fields - targets)}"
            )
        for binding in step.input_bindings:
            target_schema = capability_inputs[binding.target_field]
            source = binding.source
            if source.kind == "authored_constant":
                if not schema_matches(
                    source.value,
                    target_schema,
                    capability.input_schema,
                ):
                    raise RegistryError(
                        f"method {method.id} step {step.step_id} constant does not match "
                        f"capability input {binding.target_field}"
                    )
                continue
            assert source.field is not None
            if source.kind == "method_input":
                if source.field not in method_inputs:
                    raise RegistryError(
                        f"method {method.id} recipe references unknown input {source.field}"
                    )
                authority = (
                    PortDirection.INPUT,
                    source.field,
                    step.capability,
                    binding.target_field,
                )
                if authority not in schema_bindings:
                    raise RegistryError(
                        f"method {method.id} input {source.field} must derive from exact "
                        f"capability field {capability.id}.{binding.target_field}"
                    )
                used_schema_bindings.add(authority)
                continue
            assert source.step_id is not None
            producer_step = step_by_id[source.step_id]
            _require_step_field_compatibility(
                method=method,
                producer_step=producer_step,
                producer_capability=capability_by_step[source.step_id],
                producer_field=source.field,
                consumer_step=step,
                consumer_capability=capability,
                consumer_field=binding.target_field,
            )

    for result_binding in method.recipe.result_bindings:
        source = result_binding.source
        target_schema = method_outputs[result_binding.target_field]
        if source.kind == "authored_constant":
            if not schema_matches(source.value, target_schema, method.output_schema):
                raise RegistryError(
                    f"method {method.id} result constant does not match output "
                    f"{result_binding.target_field}"
                )
            continue
        assert source.field is not None
        if source.kind == "method_input":
            if source.field not in method_inputs:
                raise RegistryError(
                    f"method {method.id} result references unknown input {source.field}"
                )
            if not _schemas_are_passthrough_compatible(
                method_inputs[source.field],
                target_schema,
            ):
                raise RegistryError(
                    f"method {method.id} result passes input {source.field} to output "
                    f"{result_binding.target_field} with incompatible schemas"
                )
            continue
        assert source.step_id is not None
        producer = capability_by_step[source.step_id]
        producer_outputs = _schema_properties(
            producer.output_schema,
            subject=f"capability {producer.id} output",
        )
        if source.field not in producer_outputs:
            raise RegistryError(
                f"method {method.id} result references unknown output "
                f"{source.step_id}.{source.field}"
            )
        authority = (
            PortDirection.OUTPUT,
            result_binding.target_field,
            step_by_id[source.step_id].capability,
            source.field,
        )
        if authority not in schema_bindings:
            raise RegistryError(
                f"method {method.id} output {result_binding.target_field} must derive from exact "
                f"capability field {producer.id}.{source.field}"
            )
        used_schema_bindings.add(authority)

    unused = schema_bindings - used_schema_bindings
    if unused:
        raise RegistryError(
            f"method {method.id} has schema bindings that do not match recipe dataflow"
        )


def _entity_index(
    records: Mapping[EntityKind, tuple[RegistryRecord, ...]],
) -> dict[EntityKind, dict[EntityKey, RegistryRecord]]:
    result: dict[EntityKind, dict[EntityKey, RegistryRecord]] = {}
    for kind in _ENTITY_KINDS:
        selected: dict[EntityKey, RegistryRecord] = {}
        for record in records[kind]:
            prior = selected.get(record.key)
            if prior is not None:
                raise RegistryError(
                    f"duplicate {kind} identity {record.id}@{record.version}: "
                    f"{prior.source_path} and {record.source_path}"
                )
            selected[record.key] = record
        result[kind] = selected
    return result


def _require_registered(
    indexes: Mapping[EntityKind, Mapping[EntityKey, RegistryRecord]],
    kind: EntityKind,
    key: EntityKey,
    *,
    source: RegistryRecord | Path,
) -> RegistryRecord:
    selected = indexes[kind].get(key)
    if selected is None:
        label = source.id if isinstance(source, RegistryRecord) else source.as_posix()
        raise RegistryError(
            f"{label} references unregistered {kind} {key[0]}@{key[1]}"
        )
    return selected


def _validate_cross_references(
    records: Mapping[EntityKind, tuple[RegistryRecord, ...]],
    indexes: Mapping[EntityKind, Mapping[EntityKey, RegistryRecord]],
) -> None:
    for method in records["method"]:
        for capability in _recipe_capabilities(method):
            _require_registered(indexes, "capability", capability, source=method)
        _validate_method_recipe_contract(
            _validated_spec(method, MethodSpec),
            indexes["capability"],
        )

    for adapter in records["adapter"]:
        backend_value = adapter.data.get("backend")
        if backend_value is not None:
            backend_record = _require_registered(
                indexes,
                "backend",
                _ref(backend_value, name=f"adapter {adapter.id} backend"),
                source=adapter,
            )
            backend_spec = _validated_spec(backend_record, BackendSpec)
            adapter_spec = _validated_spec(adapter, AdapterSpec)
            if not set(adapter_spec.transports) <= set(backend_spec.transports):
                raise RegistryError(
                    f"adapter {adapter.id} declares a transport unsupported by its backend"
                )

    for implementation in records["implementation"]:
        implementation_spec = _validated_spec(implementation, ImplementationSpec)
        capability = _ref(
            implementation.data.get("capability"),
            name=f"implementation {implementation.id} capability",
        )
        adapter_key = _ref(
            implementation.data.get("adapter"),
            name=f"implementation {implementation.id} adapter",
        )
        _require_registered(indexes, "capability", capability, source=implementation)
        bindings = implementation.data.get("backend_bindings")
        if not isinstance(bindings, Sequence) or isinstance(bindings, str | bytes):
            raise RegistryError(f"implementation {implementation.id} has no backend bindings")
        backend_by_role: dict[str, EntityKey] = {}
        for binding in bindings:
            if not isinstance(binding, Mapping) or not isinstance(binding.get("role"), str):
                raise RegistryError(
                    f"implementation {implementation.id} has a malformed backend binding"
                )
            backend = _ref(
                binding.get("backend"),
                name=f"implementation {implementation.id} backend",
            )
            backend_record = _require_registered(
                indexes,
                "backend",
                backend,
                source=implementation,
            )
            backend_spec = _validated_spec(backend_record, BackendSpec)
            transport = binding.get("transport")
            locality = binding.get("locality")
            if transport not in {item.value for item in backend_spec.transports}:
                raise RegistryError(
                    f"implementation {implementation.id} binding transport is unsupported "
                    f"by backend {backend_record.id}"
                )
            if locality != backend_spec.locality.value:
                raise RegistryError(
                    f"implementation {implementation.id} binding locality contradicts "
                    f"backend {backend_record.id}"
                )
            backend_by_role[cast(str, binding["role"])] = backend
        adapter_record = _require_registered(
            indexes, "adapter", adapter_key, source=implementation
        )
        adapter_backend = adapter_record.data.get("backend")
        if adapter_backend is not None and _ref(
            adapter_backend, name=f"adapter {adapter_record.id} backend"
        ) != backend_by_role.get("runtime"):
            raise RegistryError(
                f"implementation {implementation.id} runtime backend contradicts its adapter"
            )
        runtime_binding = next(
            (
                item
                for item in bindings
                if isinstance(item, Mapping) and item.get("role") == "runtime"
            ),
            None,
        )
        adapter_spec = _validated_spec(adapter_record, AdapterSpec)
        if implementation_spec.artifact.distribution != adapter_spec.distribution:
            raise RegistryError(
                f"implementation {implementation.id} artifact distribution contradicts "
                f"adapter {adapter_record.id}"
            )
        if runtime_binding is not None and runtime_binding.get("transport") not in {
            item.value for item in adapter_spec.transports
        }:
            raise RegistryError(
                f"implementation {implementation.id} runtime transport is unsupported "
                f"by adapter {adapter_record.id}"
            )
        try:
            derive_availability_requirements(
                implementation_spec,
                tuple(
                    _validated_spec(record, BackendSpec)
                    for record in indexes["backend"].values()
                ),
            )
        except ValueError as exc:
            raise RegistryError(
                f"implementation {implementation.id} has contradictory backend boundaries: {exc}"
            ) from exc


def _evidence_kind(path: Path, root: Path) -> EntityKind | None:
    relative = path.relative_to(root)
    if path.name == "conformance.yaml" and relative.parts[0] == "capabilities":
        return "capability"
    if path.name == "evidence.yaml" and relative.parts[0] == "methods":
        return "method"
    if relative.parts[0] != "evidence" or len(relative.parts) < 3:
        return None
    by_directory = {
        "methods": "method",
        "capabilities": "capability",
        "backends": "backend",
        "adapters": "adapter",
        "implementations": "implementation",
    }
    return cast(EntityKind | None, by_directory.get(relative.parts[1]))


def _protocol_evidence_spec(
    data: Mapping[str, Any],
    *,
    kind: EntityKind,
    path: Path,
) -> EvidenceProtocolSpec:
    model: type[CapabilityConformance | MethodEvidence | AdapterEvidence | ImplementationEvidence]
    if kind == "capability" and path.name == "conformance.yaml":
        model = CapabilityConformance
    elif kind == "method":
        model = MethodEvidence
    elif kind == "adapter":
        model = AdapterEvidence
    elif kind == "implementation":
        model = ImplementationEvidence
    else:
        raise RegistryError(f"{path} uses an unsupported evidence record kind")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise RegistryError(f"invalid closed evidence record {path}: {exc}") from exc


def _evidence_subject(spec: EvidenceProtocolSpec) -> tuple[EntityKind, EntityKey]:
    if isinstance(spec, CapabilityConformance):
        return "capability", (spec.capability.id, spec.capability.version)
    kind: EntityKind = spec.subject.kind.value
    return kind, (spec.subject.id, spec.subject.version)


def _materialize_conformance_value(value: Any, *, name: str) -> Any:
    if isinstance(value, Mapping):
        if "$binary64" in value:
            if set(value) != {"$binary64"} or not isinstance(value["$binary64"], str):
                raise RegistryError(f"{name} uses a malformed $binary64 fixture")
            try:
                result = float.fromhex(value["$binary64"])
            except ValueError as exc:
                raise RegistryError(f"{name} uses an invalid $binary64 fixture") from exc
            if not math.isfinite(result):
                raise RegistryError(f"{name} uses a non-finite $binary64 fixture")
            return result
        return {
            str(key): _materialize_conformance_value(item, name=f"{name}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _materialize_conformance_value(item, name=f"{name}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


def _validate_conformance_payloads(
    spec: CapabilityConformance,
    *,
    capability: CapabilitySpec,
    path: Path,
) -> None:
    input_properties = capability.input_schema.get("properties")
    output_properties = capability.output_schema.get("properties")
    if not isinstance(input_properties, Mapping) or not isinstance(output_properties, Mapping):
        raise RegistryError(f"{path} references malformed capability schemas")
    for case in spec.cases:
        raw_input = case.input
        unknown_inputs = set(raw_input) - set(input_properties)
        if unknown_inputs:
            raise RegistryError(
                f"{path} conformance case {case.id} contains unknown capability inputs: "
                f"{tuple(sorted(unknown_inputs))}"
            )
        materialized_input = _materialize_conformance_value(
            raw_input,
            name=f"{path} conformance case {case.id} input",
        )
        if case.expected.outcome != "success":
            continue
        if not schema_matches(materialized_input, capability.input_schema):
            raise RegistryError(
                f"{path} successful conformance case {case.id} violates the exact "
                "capability input schema"
            )
        output = case.expected.output
        unknown_outputs = set(output) - set(output_properties)
        if unknown_outputs:
            raise RegistryError(
                f"{path} conformance case {case.id} contains unknown capability outputs: "
                f"{tuple(sorted(unknown_outputs))}"
            )
        materialized_output = _materialize_conformance_value(
            output,
            name=f"{path} conformance case {case.id} output",
        )
        if not schema_matches(materialized_output, capability.output_schema):
            raise RegistryError(
                f"{path} successful conformance case {case.id} violates the exact "
                "capability output schema"
            )


def _load_evidence(
    root: Path,
    indexes: Mapping[EntityKind, Mapping[EntityKey, RegistryRecord]],
) -> tuple[EvidenceRecord, ...]:
    paths = {
        *root.glob("evidence/**/*.yaml"),
        *root.glob("capabilities/*/*/conformance.yaml"),
        *root.glob("methods/*/*/evidence.yaml"),
    }
    entries = tuple((path, _load_mapping(path)) for path in _sorted_paths(paths, root))
    return _load_evidence_entries(entries, root=root, indexes=indexes)


def _load_evidence_entries(
    entries: Sequence[tuple[Path, dict[str, JsonValue]]],
    *,
    root: Path,
    indexes: Mapping[EntityKind, Mapping[EntityKey, RegistryRecord]],
) -> tuple[EvidenceRecord, ...]:
    result: list[EvidenceRecord] = []
    seen_paths: set[Path] = set()
    for path, data in entries:
        if path in seen_paths:
            raise RegistryError(f"duplicate evidence path: {path}")
        seen_paths.add(path)
        kind = _evidence_kind(path, root)
        if kind is None:
            raise RegistryError(f"cannot determine evidence kind from path: {path}")
        _validate_no_secret_values(data, path=path.as_posix())
        spec = _protocol_evidence_spec(
            data,
            kind=kind,
            path=path,
        )
        subject_kind, subject = _evidence_subject(spec)
        if subject_kind != kind:
            raise RegistryError(f"{path} evidence subject kind contradicts its path")
        subject_record = _require_registered(indexes, kind, subject, source=path)
        if isinstance(spec, CapabilityConformance):
            if subject_record.source_path.parent != path.parent:
                raise RegistryError(
                    f"{path} conformance must bind the exact capability in its directory"
                )
            _validate_conformance_payloads(
                spec,
                capability=_validated_spec(subject_record, CapabilitySpec),
                path=path,
            )
            identifier = None
            version = None
        else:
            identifier = spec.id
            version = spec.version
        evidence_hash = canonical_hash(data, domain="registry.evidence.content")
        frozen = _freeze(data)
        assert isinstance(frozen, Mapping)
        result.append(
            EvidenceRecord(
                subject_kind=kind,
                subject_id=subject[0],
                subject_version=subject[1],
                data=cast(Mapping[str, Any], frozen),
                evidence_hash=evidence_hash,
                source_path=path,
                id=identifier,
                version=version,
                spec=spec,
            )
        )
    ordered = tuple(
        sorted(
            result,
            key=lambda item: (
                item.subject_kind,
                item.subject_id.encode("utf-8"),
                item.subject_version,
                item.evidence_hash,
            ),
        )
    )
    identities: dict[EntityKey, Path] = {}
    conformance_subjects: dict[EntityKey, Path] = {}
    for item in ordered:
        if isinstance(item.spec, CapabilityConformance):
            prior_conformance = conformance_subjects.get(item.subject_key)
            if prior_conformance is not None:
                raise RegistryError(
                    f"duplicate capability conformance for {item.subject_id}@"
                    f"{item.subject_version}: {prior_conformance} and {item.source_path}"
                )
            conformance_subjects[item.subject_key] = item.source_path
        if item.id is None or item.version is None:
            continue
        key = (item.id, item.version)
        prior = identities.get(key)
        if prior is not None:
            raise RegistryError(
                f"duplicate evidence identity {item.id}@{item.version}: "
                f"{prior} and {item.source_path}"
            )
        identities[key] = item.source_path
    missing_conformance = set(indexes["capability"]) - set(conformance_subjects)
    if missing_conformance:
        rendered = ", ".join(
            f"{identifier}@{version}"
            for identifier, version in sorted(missing_conformance)
        )
        raise RegistryError(f"registered capabilities require exact conformance: {rendered}")
    _validate_evidence_relationships(ordered, root=root, indexes=indexes)
    return ordered


def _validate_evidence_relationships(
    evidence: tuple[EvidenceRecord, ...],
    *,
    root: Path,
    indexes: Mapping[EntityKind, Mapping[EntityKey, RegistryRecord]],
) -> None:
    conformance = {
        item.subject_key: item
        for item in evidence
        if isinstance(item.spec, CapabilityConformance)
    }
    for item in evidence:
        spec = item.spec
        if isinstance(spec, MethodEvidence):
            references = tuple(
                reference
                for claim in spec.evidence
                if claim.kind == "author_derivation"
                for reference in (
                    (claim.reference,) if claim.reference is not None else claim.references
                )
            )
            for reference in references:
                capability_key = (
                    reference.capability.id,
                    reference.capability.version,
                )
                _require_registered(
                    indexes,
                    "capability",
                    capability_key,
                    source=item.source_path,
                )
                conformance_record = conformance.get(capability_key)
                if conformance_record is None or not isinstance(
                    conformance_record.spec, CapabilityConformance
                ):
                    raise RegistryError(
                        f"method evidence {item.id} references capability without exact "
                        f"conformance: {capability_key[0]}@{capability_key[1]}"
                    )
                case_ids = {case.id for case in conformance_record.spec.cases}
                if reference.case not in case_ids:
                    raise RegistryError(
                        f"method evidence {item.id} references unknown conformance case "
                        f"{reference.case!r} for {capability_key[0]}@{capability_key[1]}"
                    )
        elif isinstance(spec, ImplementationEvidence):
            implementation = _require_registered(
                indexes,
                "implementation",
                item.subject_key,
                source=item.source_path,
            )
            capability_key = _ref(
                implementation.data.get("capability"),
                name=f"implementation {implementation.id} capability",
            )
            evidence_capability = (
                spec.conformance.capability.id,
                spec.conformance.capability.version,
            )
            if evidence_capability != capability_key:
                raise RegistryError(
                    f"implementation evidence {item.id} conformance does not bind the exact "
                    f"implementation capability {capability_key[0]}@{capability_key[1]}"
                )
            conformance_record = conformance.get(capability_key)
            if conformance_record is None:
                raise RegistryError(
                    f"implementation evidence {item.id} references capability without exact "
                    "conformance"
                )
            expected_suite = (
                Path("registry") / conformance_record.source_path.relative_to(root)
            ).as_posix()
            if spec.conformance.suite != expected_suite:
                raise RegistryError(
                    f"implementation evidence {item.id} conformance suite does not bind "
                    f"{expected_suite}"
                )


def _evidence_index(evidence: tuple[EvidenceRecord, ...]) -> dict[EntityKey, EvidenceRecord]:
    return {
        (item.id, item.version): item
        for item in evidence
        if item.id is not None and item.version is not None
    }


def _resolve_evidence_ref(
    value: Any,
    *,
    evidence: Mapping[EntityKey, EvidenceRecord],
    name: str,
    expected_subject: tuple[EntityKind, str, str],
) -> dict[str, Any]:
    key = _ref(value, name=name)
    selected = evidence.get(key)
    if selected is None:
        raise RegistryError(f"{name} is not registered: {key[0]}@{key[1]}")
    assert isinstance(value, Mapping)
    if not set(value) <= {"id", "version", "evidence_hash"}:
        raise RegistryError(f"{name} has unknown fields")
    authored_hash = value.get("evidence_hash")
    if authored_hash is not None and authored_hash != selected.evidence_hash:
        raise RegistryError(f"{name} evidence hash does not match the registry")
    selected_subject = (
        selected.subject_kind,
        selected.subject_id,
        selected.subject_version,
    )
    if selected_subject != expected_subject:
        raise RegistryError(
            f"{name} must bind exact {expected_subject[0]} "
            f"{expected_subject[1]}@{expected_subject[2]}"
        )
    return selected.ref


def _expand_evidence_references(
    record: RegistryRecord,
    evidence: Mapping[EntityKey, EvidenceRecord],
) -> RegistryRecord:
    data = record.as_dict()
    expected_subject = (record.kind, record.id, record.version)
    values = data.get("evidence_refs", [])
    if not isinstance(values, list):
        raise RegistryError(f"{record.id} evidence_refs must be an array")
    resolved_refs = [
        _resolve_evidence_ref(
            value,
            evidence=evidence,
            name=f"{record.kind} {record.id} evidence reference",
            expected_subject=expected_subject,
        )
        for value in values
    ]
    data["evidence_refs"] = _json_value(resolved_refs)
    trust = data.get("trust")
    if trust is not None:
        if not isinstance(trust, list):
            raise RegistryError(f"{record.id} trust must be an array")
        expanded_trust: list[JsonValue] = []
        for assertion in trust:
            if not isinstance(assertion, dict):
                raise RegistryError(f"{record.id} trust assertions must be objects")
            expanded = dict(assertion)
            expanded["evidence"] = _json_value(
                _resolve_evidence_ref(
                    assertion.get("evidence"),
                    evidence=evidence,
                    name=f"{record.kind} {record.id} trust evidence",
                    expected_subject=expected_subject,
                )
            )
            expanded_trust.append(expanded)
        data["trust"] = expanded_trust
    frozen = _freeze(data)
    assert isinstance(frozen, Mapping)
    return RegistryRecord(
        kind=record.kind,
        id=record.id,
        version=record.version,
        data=cast(Mapping[str, Any], frozen),
        record_hash=record.record_hash,
        source_path=record.source_path,
    )


def _taxonomy(root: Path) -> Mapping[str, Any]:
    path = root / "taxonomy" / "categories.yaml"
    if not path.is_file():
        return MappingProxyType({"schema_version": 1, "categories": ()})
    data = _load_mapping(path)
    return _taxonomy_data(data, source_path=path)


def _taxonomy_data(
    data: dict[str, JsonValue],
    *,
    source_path: Path,
) -> Mapping[str, Any]:
    _validate_no_secret_values(data, path=source_path.as_posix())
    if data.get("schema_version") != 1 or not isinstance(data.get("categories"), list):
        raise RegistryError("taxonomy/categories.yaml must declare a categories array")
    identifiers: set[str] = set()
    for category in cast(list[JsonValue], data["categories"]):
        if not isinstance(category, dict) or not isinstance(category.get("id"), str):
            raise RegistryError("taxonomy category entries require string IDs")
        identifier = cast(str, category["id"])
        if identifier in identifiers:
            raise RegistryError(f"duplicate taxonomy category: {identifier}")
        identifiers.add(identifier)
    frozen = _freeze(data)
    assert isinstance(frozen, Mapping)
    return cast(Mapping[str, Any], frozen)


def _validate_method_categories(
    taxonomy: Mapping[str, Any], methods: tuple[RegistryRecord, ...]
) -> None:
    categories = taxonomy.get("categories")
    if not isinstance(categories, Sequence):
        raise RegistryError("taxonomy categories must be an array")
    if methods and not categories:
        raise RegistryError("taxonomy must register every authored method category")
    registered = {
        item.get("id")
        for item in categories
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    for method in methods:
        category = method.data.get("category")
        if not isinstance(category, str) or category not in registered:
            raise RegistryError(
                f"method {method.id} references unregistered taxonomy category {category!r}"
            )


def _assemble_registry(
    *,
    selected_root: Path,
    loaded: dict[EntityKind, tuple[RegistryRecord, ...]],
    taxonomy: Mapping[str, Any],
    evidence_entries: Sequence[tuple[Path, dict[str, JsonValue]]] | None,
) -> Registry:
    indexes = _entity_index(loaded)

    for selected_kind in ("capability", "backend"):
        loaded[selected_kind] = tuple(
            sorted(
                (
                    _protocol_record(record, record.as_dict())
                    for record in loaded[selected_kind]
                ),
                key=_record_order,
            )
        )
    indexes = _entity_index(loaded)
    _validate_method_categories(taxonomy, loaded["method"])
    evidence = (
        _load_evidence(selected_root, indexes)
        if evidence_entries is None
        else _load_evidence_entries(
            evidence_entries,
            root=selected_root,
            indexes=indexes,
        )
    )
    evidence_by_ref = _evidence_index(evidence)

    loaded["adapter"] = tuple(
        sorted(
            (
                _protocol_record(
                    expanded,
                    _expand_evidence_references(expanded, evidence_by_ref).as_dict(),
                )
                for record in loaded["adapter"]
                for expanded in (
                    _expand_record_references(
                        record,
                        {"backend": ("backend", indexes["backend"])},
                    ),
                )
            ),
            key=_record_order,
        )
    )
    indexes = _entity_index(loaded)
    loaded["implementation"] = tuple(
        sorted(
            (
                _protocol_record(
                    expanded,
                    _expand_evidence_references(expanded, evidence_by_ref).as_dict(),
                )
                for record in loaded["implementation"]
                for expanded in (
                    _expand_backend_bindings(
                        _expand_record_references(
                            record,
                            {
                                "capability": (
                                    "capability",
                                    indexes["capability"],
                                ),
                                "adapter": ("adapter", indexes["adapter"]),
                            },
                        ),
                        indexes["backend"],
                    ),
                )
            ),
            key=_record_order,
        )
    )
    loaded["method"] = tuple(
        sorted(
            (
                _expand_method(record, indexes["capability"])
                for record in loaded["method"]
            ),
            key=_record_order,
        )
    )
    indexes = _entity_index(loaded)
    _validate_cross_references(loaded, indexes)
    projection = {
        "taxonomy": _thaw(taxonomy),
        "entities": [
            {
                "kind": kind,
                **record.ref,
                "record_hash": record.record_hash,
            }
            for kind in _ENTITY_KINDS
            for record in loaded[kind]
        ],
        "evidence": [
            {
                "subject_kind": item.subject_kind,
                "subject_id": item.subject_id,
                "subject_version": item.subject_version,
                "evidence_hash": item.evidence_hash,
            }
            for item in evidence
        ],
    }
    registry_hash = canonical_hash(projection, domain="registry.snapshot")
    return Registry(
        root=selected_root,
        taxonomy=taxonomy,
        methods=loaded["method"],
        capabilities=loaded["capability"],
        backends=loaded["backend"],
        adapters=loaded["adapter"],
        implementations=loaded["implementation"],
        evidence=evidence,
        registry_hash=registry_hash,
    )


def _bundle_source_path(root: Path, value: Any, *, name: str) -> Path:
    if not isinstance(value, str):
        raise RegistryError(f"compiled registry {name} path must be a string")
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RegistryError(f"compiled registry {name} path must be relative and contained")
    return root.joinpath(*relative.parts)


def _bundle_entry(
    root: Path,
    value: Any,
    *,
    name: str,
) -> tuple[Path, dict[str, JsonValue]]:
    if not isinstance(value, dict) or set(value) != {"path", "record"}:
        raise RegistryError(f"compiled registry {name} entry is malformed")
    path = _bundle_source_path(root, value.get("path"), name=name)
    record = value.get("record")
    if not isinstance(record, dict):
        raise RegistryError(f"compiled registry {name} record must be an object")
    normalized = _json_value(record)
    assert isinstance(normalized, dict)
    return path, normalized


def _load_compiled_registry(root: Path, path: Path) -> Registry:
    bundle = _load_mapping(path)
    if set(bundle) != {"schema_version", "taxonomy", "entities", "evidence"}:
        raise RegistryError("compiled registry has unknown or missing top-level fields")
    if bundle.get("schema_version") != 1:
        raise RegistryError("compiled registry requires schema_version: 1")
    entities = bundle.get("entities")
    if not isinstance(entities, dict) or set(entities) != set(_ENTITY_KINDS):
        raise RegistryError("compiled registry entity groups are malformed")
    loaded: dict[EntityKind, tuple[RegistryRecord, ...]] = {}
    for kind in _ENTITY_KINDS:
        values = entities.get(kind)
        if not isinstance(values, list):
            raise RegistryError(f"compiled registry {kind} entries must be an array")
        records = []
        for value in values:
            source_path, data = _bundle_entry(root, value, name=kind)
            records.append(_load_entity_data(kind, data, source_path))
        loaded[kind] = tuple(sorted(records, key=_record_order))

    taxonomy_value = bundle.get("taxonomy")
    taxonomy_path, taxonomy_data = _bundle_entry(
        root,
        taxonomy_value,
        name="taxonomy",
    )
    taxonomy = _taxonomy_data(taxonomy_data, source_path=taxonomy_path)
    evidence_values = bundle.get("evidence")
    if not isinstance(evidence_values, list):
        raise RegistryError("compiled registry evidence entries must be an array")
    evidence_entries = tuple(
        _bundle_entry(root, value, name="evidence") for value in evidence_values
    )
    return _assemble_registry(
        selected_root=root,
        loaded=loaded,
        taxonomy=taxonomy,
        evidence_entries=evidence_entries,
    )


def load_registry(*, root: str | Path | None = None) -> Registry:
    """Load and cross-validate one deterministic registry snapshot without code imports.

    Installed distributions prefer the build-generated ``registry.json`` so the core runtime
    remains Pydantic-only. Source checkouts load authored YAML for authoring diagnostics.
    """

    selected_root = _registry_root(root)
    compiled = selected_root / "registry.json"
    if compiled.is_file():
        return _load_compiled_registry(selected_root, compiled)

    loaded: dict[EntityKind, tuple[RegistryRecord, ...]] = {}
    for kind in _ENTITY_KINDS:
        paths = _sorted_paths(selected_root.glob(_ENTITY_GLOBS[kind]), selected_root)
        loaded[kind] = tuple(
            sorted((_load_entity(kind, path) for path in paths), key=_record_order)
        )
    return _assemble_registry(
        selected_root=selected_root,
        loaded=loaded,
        taxonomy=_taxonomy(selected_root),
        evidence_entries=None,
    )


def _normalize_filter(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("method filter values must be strings")
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    if not normalized:
        raise ValueError("method filter values must not be empty")
    return normalized


def _raw_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    result: list[str] = []
    current: list[str] = []
    for character in normalized:
        if character.isalnum():
            current.append(character)
        elif current:
            result.append("".join(current))
            current = []
    if current:
        result.append("".join(current))
    return tuple(result)


def _tokens(value: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for token in _raw_tokens(value):
        if token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return tuple(result)


def _nested(data: Mapping[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _field_values(method: RegistryRecord, name: str) -> tuple[str, ...]:
    if name == "id":
        return (method.id,)
    return _strings(_nested(method.data, name))


def _field_match(
    method: RegistryRecord,
    field_name: str,
    query_terms: tuple[str, ...],
) -> MethodFieldMatch | None:
    values = _field_values(method, field_name)
    indexed = {token for value in values for token in _raw_tokens(value)}
    terms = tuple(term for term in query_terms if term in indexed)
    if not terms:
        return None
    return MethodFieldMatch(field=field_name, terms=terms, values=values)


def _method_facets(method: RegistryRecord) -> dict[str, tuple[str, ...]]:
    return {
        "categories": tuple(
            _normalize_filter(item) for item in _strings(method.data.get("category"))
        ),
        "tags": tuple(
            _normalize_filter(item)
            for item in (
                _strings(_nested(method.data, "discovery.tags"))
                or _strings(method.data.get("tags"))
            )
        ),
        "intents": tuple(
            _normalize_filter(item)
            for item in _strings(_nested(method.data, "discovery.intents"))
        ),
        "input_concepts": tuple(
            _normalize_filter(item)
            for item in _strings(_nested(method.data, "discovery.input_concepts"))
        ),
        "output_concepts": tuple(
            _normalize_filter(item)
            for item in _strings(_nested(method.data, "discovery.output_concepts"))
        ),
        "lifecycles": tuple(
            _normalize_filter(item) for item in _strings(method.data.get("lifecycle"))
        ),
    }


def _eligible(method: RegistryRecord, filters: MethodFilters) -> bool:
    facets = _method_facets(method)
    return all(
        not requested or bool(set(requested) & set(facets[name]))
        for name, requested in (
            ("categories", filters.categories),
            ("tags", filters.tags),
            ("intents", filters.intents),
            ("input_concepts", filters.input_concepts),
            ("output_concepts", filters.output_concepts),
            ("lifecycles", filters.lifecycles),
        )
    )


def search_methods(
    query: str,
    *,
    registry: Registry | None = None,
    root: str | Path | None = None,
    filters: MethodFilters | None = None,
    limit: int = 20,
) -> MethodSearchResults:
    """Search authored method metadata only, with deterministic explanations."""

    if registry is not None and root is not None:
        raise ValueError("registry and root cannot be combined")
    if not isinstance(query, str):
        raise TypeError("method query must be a string")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("method search limit must be between 1 and 100")
    selected = registry or load_registry(root=root)
    selected_filters = filters or MethodFilters()
    terms = _tokens(query)
    hits: list[MethodSearchHit] = []
    for method in selected.methods:
        if not _eligible(method, selected_filters):
            continue
        positive: list[MethodFieldMatch] = []
        boundary: list[MethodFieldMatch] = []
        score = 0
        for field_name, weight in _POSITIVE_FIELDS:
            match = _field_match(method, field_name, terms)
            if match is not None:
                positive.append(match)
                score += weight * len(match.terms)
        for field_name in _BOUNDARY_FIELDS:
            match = _field_match(method, field_name, terms)
            if match is not None:
                boundary.append(match)
        if terms and not positive:
            continue
        matched = tuple(
            term for term in terms if any(term in item.terms for item in positive)
        )
        unmatched = tuple(term for term in terms if term not in matched)
        boundary_terms = {term for item in boundary for term in item.terms}
        score = max(0, score + 5 * len(matched) - 3 * len(boundary_terms))
        hits.append(
            MethodSearchHit(
                method=method,
                score=score,
                positive_matches=tuple(positive),
                boundary_matches=tuple(boundary),
                matched_terms=matched,
                unmatched_terms=unmatched,
            )
        )
    hits.sort(key=lambda item: (-item.score, *_record_order(item.method)))
    return MethodSearchResults(
        query=query,
        terms=terms,
        filters=selected_filters,
        total_matches=len(hits),
        hits=tuple(hits[:limit]),
    )


def inspect_method(
    method_id: str,
    *,
    version: str | None = None,
    registry: Registry | None = None,
    root: str | Path | None = None,
) -> MethodInspection:
    """Join one exact method to static registry support without importing executable code."""

    if registry is not None and root is not None:
        raise ValueError("registry and root cannot be combined")
    selected = registry or load_registry(root=root)
    method = selected.get("method", method_id, version)
    capability_keys = set(_recipe_capabilities(method))
    capabilities = tuple(
        item for item in selected.capabilities if item.key in capability_keys
    )
    implementations = tuple(
        item
        for item in selected.implementations
        if _ref(item.data.get("capability"), name=f"implementation {item.id} capability")
        in capability_keys
    )
    adapter_keys = {
        _ref(item.data.get("adapter"), name=f"implementation {item.id} adapter")
        for item in implementations
    }
    backend_keys = {
        _ref(binding.get("backend"), name=f"implementation {item.id} backend")
        for item in implementations
        for binding in cast(Sequence[Mapping[str, Any]], item.data.get("backend_bindings", ()))
    }
    adapters = tuple(item for item in selected.adapters if item.key in adapter_keys)
    backends = tuple(item for item in selected.backends if item.key in backend_keys)
    subjects = {
        ("method", method.key),
        *(("capability", item.key) for item in capabilities),
        *(("implementation", item.key) for item in implementations),
        *(("adapter", item.key) for item in adapters),
        *(("backend", item.key) for item in backends),
    }
    evidence = tuple(
        item
        for item in selected.evidence
        if (item.subject_kind, item.subject_key) in subjects
    )
    return MethodInspection(
        method=method,
        capabilities=capabilities,
        implementations=implementations,
        adapters=adapters,
        backends=backends,
        evidence=evidence,
    )


__all__ = [
    "EvidenceRecord",
    "MethodFieldMatch",
    "MethodFilters",
    "MethodInspection",
    "MethodSearchHit",
    "MethodSearchResults",
    "Registry",
    "RegistryError",
    "RegistryRecord",
    "inspect_method",
    "load_registry",
    "search_methods",
]
