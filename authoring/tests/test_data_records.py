from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from defined_quant.data_records import (
    DATASET_PAYLOAD_HASH_DOMAIN,
    DATASET_RECORD_HASH_DOMAIN,
    OPERATION_RECORD_HASH_DOMAIN,
    DatasetPayloadV1,
    DatasetRecordV1,
    DatasetRegistrationRequest,
    NoExternalPreprocessing,
    NormalizationEventCode,
    OperationBindingV1,
    OperationRecordV1,
    ReceiptExternalPreprocessing,
    UnknownExternalPreprocessing,
    cas_json_bytes,
    dataset_reference,
    operation_reference,
    parse_dataset_reference,
    parse_operation_reference,
    pretty_json_bytes,
    strict_cas_json_loads,
    strict_json_loads,
)
from defined_quant_protocol import (
    canonical_hash,
    canonical_hash_framing,
    canonical_json_bytes,
)
from pydantic import ValidationError

ROOT = Path(__file__).parents[2]
HASH_VECTORS = ROOT / "docs" / "local_mcp" / "hash_vectors.json"


def _fixture() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(HASH_VECTORS.read_text(encoding="utf-8")))


def _vector(vector_id: str) -> dict[str, Any]:
    return next(
        cast(dict[str, Any], vector)
        for vector in _fixture()["vectors"]
        if vector["id"] == vector_id
    )


def _registration_request() -> dict[str, Any]:
    return {
        "source": {
            "kind": "inline_rows",
            "rows": [
                {"timestamp": "2024-01-02T01:00:00+01:00", "price": 100},
                {"timestamp": "2024-01-03T00:00:00.000000Z", "price": 101.5},
            ],
        },
        "columns": [
            {
                "source_name": "timestamp",
                "field_id": "timestamp",
                "data_type": "timestamp",
                "role": "timestamp",
            },
            {
                "source_name": "price",
                "field_id": "price",
                "data_type": "number",
                "role": "value",
            },
        ],
        "semantics": {
            "instrument": {"namespace": "synthetic", "symbol": "VECTOR"},
            "frequency": "daily",
            "timezone": "UTC",
            "ordering": "preserve_source_order",
            "price_kind": "adjusted",
        },
        "provenance": {
            "source_kind": "synthetic",
            "interpretation_method": "caller_structured",
            "label": "cross-platform hash vector",
        },
    }


def test_every_frozen_hash_and_byte_vector_reproduces() -> None:
    fixture = _fixture()
    assert fixture["schema_version"] == 1
    assert fixture["canonicalization_id"] == "dq-tagged-json-v1"
    assert fixture["hash_algorithm"] == "sha256"
    assert fixture["framing"] == canonical_hash_framing()

    for vector in fixture["vectors"]:
        canonical = canonical_json_bytes(vector["value"])
        if "expected_canonical_utf8" in vector:
            assert canonical == vector["expected_canonical_utf8"].encode("utf-8")
        digest = canonical_hash(vector["value"], domain=vector["domain"])
        assert digest == vector["expected_sha256"]
        reference = vector["expected_reference"]
        if reference is not None:
            assert reference.endswith(digest)

    for vector in fixture["byte_sha256_vectors"]:
        if vector["serialization"] == "canonical_json_bytes":
            content = canonical_json_bytes(vector["value"])
        else:
            assert vector["serialization"] == "defined-quant-pretty-json-v1"
            content = pretty_json_bytes(vector["value"])
            assert content == vector["expected_utf8"].encode("utf-8")
        assert hashlib.sha256(content).hexdigest() == vector["expected_sha256"]

    assertion = fixture["domain_separation_assertions"][0]
    left = _vector(assertion["left_vector"])
    right = _vector(assertion["right_vector"])
    assert canonical_json_bytes(left["value"]) == canonical_json_bytes(right["value"])
    assert left["expected_sha256"] != right["expected_sha256"]


def test_frozen_normalization_contract_matches_the_runtime_models() -> None:
    contract = _fixture()["dataset_normalization_contract"]

    assert contract["event_codes"] == [code.value for code in NormalizationEventCode]
    assert contract["event_unique_by"] == ["code", "field_id"]
    assert contract["event_sort"] == ["code_utf8", "field_id_utf8"]
    assert contract["maximum_events"] == 768
    assert contract["external_preprocessing_statuses"] == [
        NoExternalPreprocessing(status="none_declared").status,
        UnknownExternalPreprocessing(status="unknown").status,
        ReceiptExternalPreprocessing(
            status="receipt_supplied",
            receipt={"schema_id": "receipt", "sha256": "ab" * 32},
        ).status,
    ]
    request = DatasetRegistrationRequest.model_validate(_registration_request())
    assert request.external_preprocessing.status == contract[
        "default_external_preprocessing_status"
    ]


def test_record_models_reproduce_the_linked_dqds_and_dqop_chain() -> None:
    payload_vector = _vector("dataset_payload_two_rows")
    dataset_vector = _vector("dataset_record_inline_two_rows")
    operation_vector = _vector("operation_record_dataset_bound")

    payload = DatasetPayloadV1.model_validate(payload_vector["value"])
    dataset = DatasetRecordV1.model_validate(dataset_vector["value"])
    operation = OperationRecordV1.model_validate(operation_vector["value"])

    assert payload.canonical_projection() == payload_vector["value"]
    assert payload.digest == payload_vector["expected_sha256"]
    assert dataset.canonical_projection() == dataset_vector["value"]
    assert dataset.reference == dataset_vector["expected_reference"]
    assert dataset.validate_payload(payload) is dataset
    assert operation.canonical_projection() == operation_vector["value"]
    assert operation.reference == operation_vector["expected_reference"]
    assert operation.binding.source is not None
    assert operation.binding.source.ref == dataset.reference
    assert DATASET_PAYLOAD_HASH_DOMAIN == payload_vector["domain"]
    assert DATASET_RECORD_HASH_DOMAIN == dataset_vector["domain"]
    assert OPERATION_RECORD_HASH_DOMAIN == operation_vector["domain"]


def test_registration_defaults_and_hash_projection_omissions_are_exact() -> None:
    request = DatasetRegistrationRequest.model_validate(_registration_request())
    assert request.host_schema_version == 1
    assert request.external_preprocessing == UnknownExternalPreprocessing(status="unknown")
    assert request.provenance.verification_status.value == "unverified"
    assert request.provenance.references == ()
    assert request.provenance.assumptions == ()
    assert "content_sha256" not in request.provenance.canonical_projection()

    dataset = DatasetRecordV1.model_validate(_vector("dataset_record_inline_two_rows")["value"])
    projection = dataset.canonical_projection()
    provenance = projection["source"]["provenance"]
    assert "content_sha256" not in provenance
    assert [column["semantic_port"] for column in projection["columns"]] == [None, None]
    assert "currency" not in projection["semantics"]


def test_external_preprocessing_is_closed_and_hash_bound() -> None:
    base = _vector("dataset_record_inline_two_rows")["value"]
    none_declared = DatasetRecordV1.model_validate(base)

    unknown_value = deepcopy(base)
    unknown_value["external_preprocessing"] = {"status": "unknown"}
    unknown = DatasetRecordV1.model_validate(unknown_value)

    receipt_value = deepcopy(base)
    receipt_value["external_preprocessing"] = {
        "status": "receipt_supplied",
        "receipt": {"schema_id": "vendor_receipt", "sha256": "ab" * 32},
    }
    receipt = DatasetRecordV1.model_validate(receipt_value)
    assert isinstance(receipt.external_preprocessing, ReceiptExternalPreprocessing)
    assert len({none_declared.digest, unknown.digest, receipt.digest}) == 3

    mixed = deepcopy(receipt_value)
    mixed["external_preprocessing"] = {
        "status": "unknown",
        "receipt": {"schema_id": "vendor_receipt", "sha256": "ab" * 32},
    }
    with pytest.raises(ValidationError):
        DatasetRecordV1.model_validate(mixed)

    with pytest.raises(ValidationError):
        DatasetRegistrationRequest.model_validate(
            {**_registration_request(), "external_preprocessing": {}}
        )


def test_required_discriminators_and_materialized_record_fields_cannot_default() -> None:
    request = _registration_request()
    del request["source"]["kind"]
    with pytest.raises(ValidationError):
        DatasetRegistrationRequest.model_validate(request)

    payload = deepcopy(_vector("dataset_payload_two_rows")["value"])
    del payload["serialization"]
    with pytest.raises(ValidationError):
        DatasetPayloadV1.model_validate(payload)

    dataset = deepcopy(_vector("dataset_record_inline_two_rows")["value"])
    del dataset["source"]["provenance"]["verification_status"]
    with pytest.raises(ValidationError):
        DatasetRecordV1.model_validate(dataset)

    operation = deepcopy(_vector("operation_record_dataset_bound")["value"])
    del operation["binding"]["literals"]
    with pytest.raises(ValidationError):
        OperationRecordV1.model_validate(operation)


def test_operation_literals_are_recursively_immutable_and_hash_stable() -> None:
    binding = OperationBindingV1.model_validate(
        {"literals": {"configuration": {"windows": [20, 60]}}}
    )
    before = binding.canonical_projection()

    with pytest.raises(TypeError):
        binding.literals["other"] = True
    configuration = binding.literals["configuration"]
    assert isinstance(configuration, dict)
    with pytest.raises(TypeError):
        configuration["windows"] = []
    windows = configuration["windows"]
    assert isinstance(windows, list)
    with pytest.raises(TypeError):
        windows.append(120)

    assert binding.canonical_projection() == before


@pytest.mark.parametrize(
    "mutation",
    [
        {"extra": True},
        {"columns": []},
        {
            "columns": [
                {
                    "source_name": "x",
                    "field_id": "X",
                    "data_type": "number",
                    "role": "value",
                }
            ]
        },
    ],
)
def test_registration_request_is_closed_and_strict(mutation: dict[str, Any]) -> None:
    value = _registration_request()
    value.update(mutation)
    with pytest.raises(ValidationError):
        DatasetRegistrationRequest.model_validate(value)


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_schema_version_requires_an_integer(version: Any) -> None:
    value = _registration_request()
    value["host_schema_version"] = version
    with pytest.raises(ValidationError):
        DatasetRegistrationRequest.model_validate(value)


def test_payload_refuses_wrong_width_type_timestamp_and_unsafe_number() -> None:
    base = _vector("dataset_payload_two_rows")["value"]
    mutations = []
    wrong_width = deepcopy(base)
    wrong_width["rows"][0].pop()
    mutations.append(wrong_width)
    wrong_type = deepcopy(base)
    wrong_type["rows"][0][1] = True
    mutations.append(wrong_type)
    bad_timestamp = deepcopy(base)
    bad_timestamp["rows"][0][0] = "2024-01-02t00:00:00z"
    mutations.append(bad_timestamp)
    unsafe = deepcopy(base)
    unsafe["rows"][0][1] = 2**53
    mutations.append(unsafe)
    for value in mutations:
        with pytest.raises(ValidationError):
            DatasetPayloadV1.model_validate(value)


def test_dataset_record_cross_checks_payload_and_canonical_event_order() -> None:
    payload = DatasetPayloadV1.model_validate(_vector("dataset_payload_two_rows")["value"])
    value = deepcopy(_vector("dataset_record_inline_two_rows")["value"])
    value["payload"]["sha256"] = "00" * 32
    record = DatasetRecordV1.model_validate(value)
    with pytest.raises(ValueError, match="payload digest"):
        record.validate_payload(payload)

    events = deepcopy(_vector("dataset_record_inline_two_rows")["value"])
    events["normalization_events"] = [
        {"code": "timestamp_to_canonical_utc", "field_id": "timestamp", "count": 2},
        {"code": "json_missing_field_to_null", "field_id": "price", "count": 1},
    ]
    with pytest.raises(ValidationError, match="canonical UTF-8 ordering"):
        DatasetRecordV1.model_validate(events)

    finding = deepcopy(_vector("dataset_record_inline_two_rows")["value"])
    finding["findings"] = [
        {
            "code": "unfrozen_warning",
            "severity": "warning",
            "message": "Caller-controlled text must not enter an immutable record.",
            "count": 1,
        }
    ]
    with pytest.raises(ValidationError, match="separately frozen"):
        DatasetRecordV1.model_validate(finding)


def test_operation_record_refuses_noncanonical_members_and_binding_overlap() -> None:
    value = deepcopy(_vector("operation_record_dataset_bound")["value"])
    value["members"].reverse()
    with pytest.raises(ValidationError, match="canonically ordered"):
        OperationRecordV1.model_validate(value)

    value = deepcopy(_vector("operation_record_dataset_bound")["value"])
    value["binding"]["literals"]["prices"] = [1, 2]
    with pytest.raises(ValidationError, match="overlap"):
        OperationRecordV1.model_validate(value)

    value = deepcopy(_vector("operation_record_dataset_bound")["value"])
    value["members"][1]["path"] = "INPUT.JSON"
    with pytest.raises(ValidationError, match="operation member paths must be unique"):
        OperationRecordV1.model_validate(value)

    value = deepcopy(_vector("operation_record_dataset_bound")["value"])
    value["members"][1]["path"] = "MANIFEST.JSON"
    with pytest.raises(ValidationError, match="operation member paths must be unique"):
        OperationRecordV1.model_validate(value)


def test_operation_source_binding_is_deferred_until_protocol_050() -> None:
    value = deepcopy(_vector("operation_record_dataset_bound")["value"])
    value["binding"]["source"] = {
        "kind": "operation",
        "ref": "dqop:v1:" + "ab" * 32,
        "mappings": value["binding"]["source"]["mappings"],
    }
    value["compatibility"] = []
    with pytest.raises(ValidationError, match="protocol 0.5.0"):
        OperationRecordV1.model_validate(value)


def test_strict_json_and_cas_serialization_reject_ambiguity() -> None:
    value = {"z": "é", "a": [1, True, None]}
    content = cas_json_bytes(value)
    assert content == b'{"a":[1,true,null],"z":"\xc3\xa9"}\n'
    assert strict_cas_json_loads(content) == value
    assert strict_json_loads(b'{"a":1}') == {"a": 1}

    invalid = (
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'\xef\xbb\xbf{"a":1}',
        b'{"a":9007199254740992}',
        b'{"a":"\\ud800"}',
    )
    for document in invalid:
        with pytest.raises(ValueError):
            strict_json_loads(document)
    with pytest.raises(ValueError, match="CAS document"):
        strict_cas_json_loads(b'{ "a": 1 }\n')


def test_reference_parsers_preserve_version_for_closed_policy_mapping() -> None:
    dataset = dataset_reference("ab" * 32)
    operation = operation_reference("cd" * 32)
    assert parse_dataset_reference(dataset).digest == "ab" * 32
    assert parse_dataset_reference("dqds:v999:" + "ab" * 32).version_token == "999"
    assert parse_operation_reference(operation).kind == "operation"
    with pytest.raises(ValueError):
        parse_dataset_reference("dqds:v1000:" + "ab" * 32)
    with pytest.raises(ValueError):
        parse_operation_reference("dqop:v1:" + "CD" * 32)
