"""Selected component inspection executed only inside the bounded worker."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from defined_quant.catalog import component_record
from defined_quant.host_failures import HostFailureCode, HostFailureException
from defined_quant.operation_runtime import (
    OperationRuntimeError,
    component_execution_models,
    component_reference,
)
from defined_quant.types import ComponentNotFound, DQError
from defined_quant_protocol import (
    PORT_SCHEMA_KEY,
    ComponentRef,
    PortDirection,
    SemanticPortError,
    canonical_json_bytes,
    compare_semantic_ports,
    extract_semantic_port,
)


def _component(
    component_id: str,
    *,
    catalog_root: Path | None,
    expected_version: str | None = None,
    expected_subject_hash: str | None = None,
) -> tuple[Any, ComponentRef, type[Any], type[Any]]:
    try:
        record = component_record(component_id, root=catalog_root)
    except ComponentNotFound as exc:
        raise HostFailureException(
            HostFailureCode.COMPONENT_NOT_FOUND,
            details={"component_id": component_id},
        ) from exc
    except DQError as exc:
        raise HostFailureException(HostFailureCode.COMPONENT_CONTRACT_ERROR) from exc
    try:
        reference = component_reference(record)
        if (
            expected_version is not None
            and reference.version != expected_version
        ) or (
            expected_subject_hash is not None
            and reference.subject_hash != expected_subject_hash
        ):
            raise HostFailureException(
                HostFailureCode.COMPONENT_IDENTITY_MISMATCH,
                details={"component_id": component_id},
            )
        inputs, output = component_execution_models(record, component=reference)
        return record, reference, inputs, output
    except HostFailureException:
        raise
    except OperationRuntimeError as exc:
        code = (
            HostFailureCode.COMPONENT_IDENTITY_MISMATCH
            if exc.code.value == "component_identity_mismatch"
            else HostFailureCode.COMPONENT_CONTRACT_ERROR
        )
        details = (
            {"component_id": component_id}
            if code is HostFailureCode.COMPONENT_IDENTITY_MISMATCH
            else {}
        )
        raise HostFailureException(code, details=details) from exc


def _guidance(record: Any) -> dict[str, Any]:
    raw = record.metadata.get("guidance")
    if not isinstance(raw, Mapping):
        raise HostFailureException(
            HostFailureCode.COMPONENT_CONTRACT_ERROR,
            details={"component_id": record.component_id},
        )
    result: dict[str, Any] = {}
    for name in ("use_when", "do_not_use_when", "unsupported_scope"):
        values = raw.get(name)
        if not isinstance(values, (list, tuple)) or any(
            not isinstance(value, str) for value in values
        ):
            raise HostFailureException(
                HostFailureCode.COMPONENT_CONTRACT_ERROR,
                details={"component_id": record.component_id},
            )
        result[name] = list(values)
    questions = raw.get("required_questions")
    if not isinstance(questions, (list, tuple)):
        raise HostFailureException(
            HostFailureCode.COMPONENT_CONTRACT_ERROR,
            details={"component_id": record.component_id},
        )
    projected: list[dict[str, Any]] = []
    for question in questions:
        if not isinstance(question, Mapping):
            raise HostFailureException(
                HostFailureCode.COMPONENT_CONTRACT_ERROR,
                details={"component_id": record.component_id},
            )
        item = {
            "id": question.get("id"),
            "ask": question.get("ask"),
            "why": question.get("why"),
            "resolves_to": question.get("resolves_to"),
            "has_default": question.get("default") is not None,
        }
        if item["has_default"]:
            item["default"] = question["default"]
        projected.append(item)
    result["required_questions"] = projected
    return result


def _ports(
    model: type[Any],
    *,
    direction: PortDirection,
) -> tuple[list[dict[str, Any]], list[str]]:
    schema = model.model_json_schema(by_alias=False)
    required = set(schema.get("required", []))
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise HostFailureException(HostFailureCode.COMPONENT_CONTRACT_ERROR)
    ports: list[dict[str, Any]] = []
    unported: list[str] = []
    for name in model.model_fields:
        field = model.model_fields[name]
        metadata = field.json_schema_extra
        if not isinstance(metadata, Mapping) or PORT_SCHEMA_KEY not in metadata:
            unported.append(name)
            continue
        try:
            port = extract_semantic_port(model, name, direction)
        except SemanticPortError as exc:
            raise HostFailureException(HostFailureCode.COMPONENT_CONTRACT_ERROR) from exc
        item: dict[str, Any] = {
            "name": name,
            "semantic_port": port.model_dump(mode="json"),
        }
        if direction is PortDirection.INPUT:
            property_schema = properties.get(name)
            item["required"] = name in required
            item["has_default"] = (
                isinstance(property_schema, Mapping) and "default" in property_schema
            )
            if item["has_default"]:
                assert isinstance(property_schema, Mapping)
                item["default"] = property_schema["default"]
        ports.append(item)
    return ports, unported


def inspect_component(
    arguments: Mapping[str, Any],
    *,
    catalog_root: Path | None,
) -> dict[str, Any]:
    """Return one exact installed component/schema projection."""

    component_id = arguments["component_id"]
    view: Literal["compact", "schemas"] = arguments.get("view", "compact")
    record, reference, inputs, output = _component(
        component_id,
        catalog_root=catalog_root,
        expected_version=arguments.get("expected_version"),
        expected_subject_hash=arguments.get("expected_subject_hash"),
    )
    input_schema = inputs.model_json_schema(by_alias=False)
    output_schema = output.model_json_schema(by_alias=False)
    input_ports, unported_inputs = _ports(inputs, direction=PortDirection.INPUT)
    output_ports, unported_outputs = _ports(output, direction=PortDirection.OUTPUT)
    data: dict[str, Any] = {
        "compact": view == "compact",
        "view": view,
        "component": reference.model_dump(mode="json"),
        "title": record.metadata.get("title"),
        "lifecycle": record.metadata.get("lifecycle"),
        "summary": record.metadata.get("summary"),
        "guidance": _guidance(record),
        "input_ports": input_ports,
        "output_ports": output_ports,
        "unported_input_fields": unported_inputs,
        "unported_output_fields": unported_outputs,
        "input_schema_sha256": hashlib.sha256(canonical_json_bytes(input_schema)).hexdigest(),
        "output_schema_sha256": hashlib.sha256(canonical_json_bytes(output_schema)).hexdigest(),
    }
    if view == "schemas":
        data["input_schema"] = input_schema
        data["output_schema"] = output_schema
    return data


def compare_ports(
    arguments: Mapping[str, Any],
    *,
    catalog_root: Path | None,
) -> dict[str, Any]:
    """Compare two exactly identified selected component fields."""

    producer_arg = arguments["producer"]
    consumer_arg = arguments["consumer"]
    producer_ref = ComponentRef.model_validate(producer_arg["component"])
    consumer_ref = ComponentRef.model_validate(consumer_arg["component"])
    _producer_record, installed_producer, _producer_inputs, producer_output = _component(
        producer_ref.id,
        catalog_root=catalog_root,
        expected_version=producer_ref.version,
        expected_subject_hash=producer_ref.subject_hash,
    )
    _consumer_record, installed_consumer, consumer_inputs, _consumer_output = _component(
        consumer_ref.id,
        catalog_root=catalog_root,
        expected_version=consumer_ref.version,
        expected_subject_hash=consumer_ref.subject_hash,
    )
    producer_field = producer_arg["field"]
    consumer_field = consumer_arg["field"]
    try:
        compatibility = compare_semantic_ports(
            producer_output,
            producer_field,
            consumer_inputs,
            consumer_field,
        )
    except SemanticPortError as exc:
        raise HostFailureException(
            HostFailureCode.INVALID_FIELD_MAPPING,
            details={"fields": []},
        ) from exc
    if not compatibility.compatible:
        raise HostFailureException(
            HostFailureCode.INCOMPATIBLE_PORTS,
            details={
                "differences": [
                    difference.model_dump(mode="json")
                    for difference in compatibility.differences
                ]
            },
        )
    return {
        "compatible": True,
        "producer": {
            "component": installed_producer.model_dump(mode="json"),
            "field": producer_field,
            "port": compatibility.producer.model_dump(mode="json"),
        },
        "consumer": {
            "component": installed_consumer.model_dump(mode="json"),
            "field": consumer_field,
            "port": compatibility.consumer.model_dump(mode="json"),
        },
        "differences": [],
    }


__all__ = ["compare_ports", "inspect_component"]
