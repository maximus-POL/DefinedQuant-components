"""Phase-3 strict normalization and configured-root tests."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from defined_quant.data_records import DatasetRegistrationRequest
from defined_quant.dataset_registry import (
    MAX_DECODED_CELL_BYTES,
    ConfiguredFileRoots,
    DatasetRegistryError,
    normalize_dataset,
    resolve_dataset_fields,
)
from defined_quant.host_failures import HostFailureException
from defined_quant_protocol import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "docs" / "local_mcp" / "hash_vectors.v1.json"


def _request(
    source: dict[str, Any],
    *,
    external_preprocessing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "source": source,
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
    if external_preprocessing is not None:
        value["external_preprocessing"] = external_preprocessing
    return value


def _vectors() -> dict[str, Any]:
    value = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_normalizer_reproduces_linked_fixed_dataset_vector_without_mcp() -> None:
    fixture = _vectors()
    byte_vector = next(
        item
        for item in fixture["byte_sha256_vectors"]
        if item["id"] == "inline_rows_source_two_rows"
    )
    payload_vector = next(
        item for item in fixture["vectors"] if item["id"] == "dataset_payload_two_rows"
    )
    record_vector = next(
        item for item in fixture["vectors"] if item["id"] == "dataset_record_inline_two_rows"
    )

    normalized = normalize_dataset(
        _request(
            {"kind": "inline_rows", "rows": byte_vector["value"]},
            external_preprocessing={"status": "none_declared"},
        )
    )

    assert (
        hashlib.sha256(canonical_json_bytes(byte_vector["value"])).hexdigest()
        == byte_vector["expected_sha256"]
    )
    assert normalized.payload.canonical_projection() == payload_vector["value"]
    assert normalized.payload.digest == payload_vector["expected_sha256"]
    assert normalized.record.canonical_projection() == record_vector["value"]
    assert normalized.record.digest == record_vector["expected_sha256"]
    assert normalized.record.reference == record_vector["expected_reference"]
    assert not any(name == "mcp" or name.startswith("mcp.") for name in sys.modules)


def test_typed_and_mapping_registration_requests_normalize_identically() -> None:
    payload = _request(
        {
            "kind": "inline_rows",
            "rows": [{"timestamp": "2024-01-02T00:00:00Z", "price": 100}],
        }
    )
    typed = DatasetRegistrationRequest.model_validate(payload)

    mapping_result = normalize_dataset(payload)
    typed_result = normalize_dataset(typed)

    assert typed_result.payload.canonical_projection() == (
        mapping_result.payload.canonical_projection()
    )
    assert typed_result.record.canonical_projection() == (
        mapping_result.record.canonical_projection()
    )
    assert typed_result.record.reference == mapping_result.record.reference


def test_external_preprocessing_default_is_visible_and_hash_significant() -> None:
    rows = [
        {"timestamp": "2024-01-02T00:00:00Z", "price": 100},
        {"timestamp": "2024-01-03T00:00:00Z", "price": 101},
    ]
    unknown = normalize_dataset(_request({"kind": "inline_rows", "rows": rows}))
    none = normalize_dataset(
        _request(
            {"kind": "inline_rows", "rows": rows},
            external_preprocessing={"status": "none_declared"},
        )
    )
    receipt = normalize_dataset(
        _request(
            {"kind": "inline_rows", "rows": rows},
            external_preprocessing={
                "status": "receipt_supplied",
                "receipt": {"schema_id": "source_receipt", "sha256": "a" * 64},
            },
        )
    )

    assert unknown.record.external_preprocessing.status == "unknown"
    assert len({unknown.record.reference, none.record.reference, receipt.record.reference}) == 3


def test_request_schema_and_host_version_fail_through_closed_host_codes() -> None:
    missing_kind = _request(
        {"rows": [{"timestamp": "2024-01-02T00:00:00Z", "price": 100}]}
    )
    with pytest.raises(HostFailureException) as malformed:
        normalize_dataset(missing_kind)
    assert malformed.value.code == "invalid_tool_request"
    assert malformed.value.failure.error.details == {"fields": []}

    explicit_empty = _request(
        {
            "kind": "inline_rows",
            "rows": [{"timestamp": "2024-01-02T00:00:00Z", "price": 100}],
        },
        external_preprocessing={},
    )
    with pytest.raises(HostFailureException) as unsafe_default:
        normalize_dataset(explicit_empty)
    assert unsafe_default.value.code == "invalid_tool_request"

    unsupported = _request(
        {
            "kind": "inline_rows",
            "rows": [{"timestamp": "2024-01-02T00:00:00Z", "price": 100}],
        }
    )
    unsupported["host_schema_version"] = 2
    with pytest.raises(HostFailureException) as host_version:
        normalize_dataset(unsupported)
    assert host_version.value.code == "unsupported_host_schema"
    assert host_version.value.failure.error.details == {"requested_version": 2}


def test_json_missing_fields_and_timestamp_changes_are_completely_ledgered() -> None:
    request = _request(
        {
            "kind": "inline_json",
            "text": json.dumps(
                [
                    {"timestamp": "2024-01-02T01:00:00+01:00", "price": 100},
                    {"timestamp": "2024-01-03T00:00:00Z"},
                ],
                separators=(",", ":"),
            ),
        }
    )
    normalized = normalize_dataset(request)

    assert normalized.payload.rows == (
        ("2024-01-02T00:00:00Z", 100),
        ("2024-01-03T00:00:00Z", None),
    )
    assert [event.model_dump(mode="json") for event in normalized.record.normalization_events] == [
        {"code": "json_missing_field_to_null", "field_id": "price", "count": 1},
        {"code": "timestamp_to_canonical_utc", "field_id": "timestamp", "count": 1},
    ]


def test_csv_normalization_preserves_order_and_accounts_for_every_conversion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    source.write_bytes(
        b"price,timestamp\r\n100,2024-01-02T01:00:00+01:00\r\n,2024-01-03T00:00:00Z\r\n"
    )
    normalized = normalize_dataset(
        _request(
            {
                "kind": "local_file",
                "path": str(source),
                "format": "csv",
                "csv": {"delimiter": ",", "header": True},
            }
        ),
        configured_roots=(tmp_path,),
    )

    assert normalized.payload.rows == (
        ("2024-01-02T00:00:00Z", 100),
        ("2024-01-03T00:00:00Z", None),
    )
    assert [event.model_dump(mode="json") for event in normalized.record.normalization_events] == [
        {"code": "csv_empty_field_to_null", "field_id": "price", "count": 1},
        {"code": "csv_number_parsed", "field_id": "price", "count": 1},
        {"code": "timestamp_to_canonical_utc", "field_id": "timestamp", "count": 1},
    ]
    source_record = normalized.record.canonical_projection()["source"]
    assert "path" not in source_record
    assert normalized.record.raw_source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_csv_parser_is_strict_and_preserves_closed_cell_limit_failures(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.csv"
    malformed.write_text('label\n"unterminated\n', encoding="utf-8")
    request = {
        "source": {
            "kind": "local_file",
            "path": str(malformed),
            "format": "csv",
        },
        "columns": [
            {
                "source_name": "label",
                "field_id": "label",
                "data_type": "string",
                "role": "label",
            }
        ],
        "semantics": {"ordering": "preserve_source_order"},
        "provenance": {
            "source_kind": "synthetic",
            "interpretation_method": "caller_structured",
            "label": "CSV parser boundary.",
        },
    }
    with pytest.raises(DatasetRegistryError) as malformed_error:
        normalize_dataset(request, configured_roots=(tmp_path,))
    assert malformed_error.value.details == {"finding_codes": ["invalid_csv_source"]}

    oversized = tmp_path / "oversized.csv"
    oversized.write_text("label\n" + "x" * 200_000 + "\n", encoding="utf-8")
    request["source"]["path"] = str(oversized)
    with pytest.raises(DatasetRegistryError) as limit_error:
        normalize_dataset(request, configured_roots=(tmp_path,))
    assert limit_error.value.details == {
        "limit_name": "decoded_cell_bytes",
        "maximum": MAX_DECODED_CELL_BYTES,
        "actual": 200_000,
    }


def test_csv_unsafe_number_and_timestamp_boundaries_never_leak_raw_errors(
    tmp_path: Path,
) -> None:
    unsafe_number = tmp_path / "unsafe-number.csv"
    unsafe_number.write_text(
        "timestamp,price\n2024-01-01T00:00:00Z,9007199254740992.0\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetRegistryError) as number_error:
        normalize_dataset(
            _request(
                {
                    "kind": "local_file",
                    "path": str(unsafe_number),
                    "format": "csv",
                }
            ),
            configured_roots=(tmp_path,),
        )
    assert number_error.value.details == {"finding_codes": ["invalid_number_cell"]}

    for timestamp in (
        "0001-01-01T00:00:00+23:59",
        "9999-12-31T23:59:59-23:59",
    ):
        with pytest.raises(DatasetRegistryError) as timestamp_error:
            normalize_dataset(
                _request(
                    {
                        "kind": "inline_rows",
                        "rows": [{"timestamp": timestamp, "price": 100}],
                    }
                )
            )
        assert timestamp_error.value.details == {"finding_codes": ["invalid_timestamp"]}


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        (
            {
                "kind": "inline_json",
                "text": '[{"timestamp":"2024-01-01T00:00:00Z","price":1,"price":2}]',
            },
            "invalid_json_source",
        ),
        (
            {"kind": "inline_json", "text": '[{"timestamp":"2024-01-01T00:00:00Z","price":NaN}]'},
            "invalid_json_source",
        ),
        (
            {"kind": "inline_rows", "rows": [{"timestamp": "2024-01-01t00:00:00z", "price": 1}]},
            "invalid_timestamp",
        ),
        (
            {"kind": "inline_rows", "rows": [{"timestamp": "2024-01-01T00:00:60Z", "price": 1}]},
            "invalid_timestamp",
        ),
        (
            {"kind": "inline_rows", "rows": [{"timestamp": "2024-01-01T00:00:00Z", "price": "1"}]},
            "invalid_number_cell",
        ),
        (
            {
                "kind": "inline_rows",
                "rows": [{"timestamp": "2024-01-01T00:00:00Z", "price": 1, "extra": 2}],
            },
            "undeclared_source_field",
        ),
    ],
)
def test_strict_sources_refuse_ambiguous_or_undeclared_values(
    source: dict[str, Any], finding: str
) -> None:
    with pytest.raises(DatasetRegistryError) as caught:
        normalize_dataset(_request(source))
    assert caught.value.code == "invalid_dataset"
    assert finding in caught.value.details["finding_codes"]


def test_provenance_digest_must_bind_the_accepted_source() -> None:
    request = _request(
        {
            "kind": "inline_rows",
            "rows": [{"timestamp": "2024-01-01T00:00:00Z", "price": 1}],
        }
    )
    request["provenance"]["content_sha256"] = "0" * 64
    with pytest.raises(DatasetRegistryError) as caught:
        normalize_dataset(request)
    assert caught.value.details == {"finding_codes": ["source_digest_mismatch"]}


def test_configured_roots_refuse_disabled_outside_and_symlink_paths(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    inside_file = allowed / "data.json"
    outside_file = outside / "data.json"
    inside_file.write_text("[]", encoding="utf-8")
    outside_file.write_text("[]", encoding="utf-8")

    with pytest.raises(DatasetRegistryError) as disabled:
        ConfiguredFileRoots().read(str(inside_file))
    assert disabled.value.code == "input_root_denied"
    with pytest.raises(DatasetRegistryError) as outside_root:
        ConfiguredFileRoots((allowed,)).read(str(outside_file))
    assert outside_root.value.code == "input_root_denied"

    link = allowed / "linked.json"
    link.symlink_to(outside_file)
    with pytest.raises(DatasetRegistryError) as unsafe:
        ConfiguredFileRoots((allowed,)).read(str(link))
    assert unsafe.value.code == "unsafe_input_path"


def test_configured_root_identity_is_pinned_and_special_files_fail_nonblocking(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    moved = tmp_path / "moved"
    allowed.mkdir()
    (allowed / "data.json").write_bytes(b"original")
    roots = ConfiguredFileRoots((allowed,))
    try:
        allowed.rename(moved)
        allowed.mkdir()
        (allowed / "data.json").write_bytes(b"replacement")
        assert roots.read(str(allowed / "data.json")) == b"original"
    finally:
        roots.close()

    if os.name == "posix" and hasattr(os, "mkfifo"):
        fifo_root = tmp_path / "fifo-root"
        fifo_root.mkdir()
        fifo = fifo_root / "blocked.csv"
        os.mkfifo(fifo)
        fifo_roots = ConfiguredFileRoots((fifo_root,))
        try:
            with pytest.raises(DatasetRegistryError) as special:
                fifo_roots.read(str(fifo))
            assert special.value.code == "unsupported_data_format"
        finally:
            fifo_roots.close()


def test_decoded_cell_limit_is_utf8_bytes() -> None:
    request = {
        "source": {"kind": "inline_rows", "rows": [{"label": "é" * 32_769}]},
        "columns": [
            {
                "source_name": "label",
                "field_id": "label",
                "data_type": "string",
                "role": "label",
            }
        ],
        "semantics": {"ordering": "preserve_source_order"},
        "provenance": {
            "source_kind": "synthetic",
            "interpretation_method": "caller_structured",
            "label": "large cell",
        },
    }
    with pytest.raises(DatasetRegistryError) as caught:
        normalize_dataset(request)
    assert caught.value.details == {
        "limit_name": "decoded_cell_bytes",
        "maximum": MAX_DECODED_CELL_BYTES,
        "actual": 65_538,
    }


def test_request_count_and_inline_byte_limits_precede_model_validation() -> None:
    oversized_inline = _request(
        {"kind": "inline_json", "text": " " * (512 * 1024 + 1)}
    )
    with pytest.raises(DatasetRegistryError) as inline_error:
        normalize_dataset(oversized_inline)
    assert inline_error.value.details == {
        "limit_name": "inline_json_source_bytes",
        "maximum": 512 * 1024,
        "actual": 512 * 1024 + 1,
    }

    too_many_columns = _request(
        {
            "kind": "inline_rows",
            "rows": [{"timestamp": "2024-01-01T00:00:00Z", "price": 1}],
        }
    )
    too_many_columns["columns"] = [
        {
            "source_name": f"field_{index}",
            "field_id": f"field_{index}",
            "data_type": "number",
            "role": "value",
        }
        for index in range(129)
    ]
    with pytest.raises(DatasetRegistryError) as column_error:
        normalize_dataset(too_many_columns)
    assert column_error.value.details == {
        "limit_name": "dataset_columns",
        "maximum": 128,
        "actual": 129,
    }


def test_whole_column_mapping_is_ordered_transform_free_and_collision_safe() -> None:
    normalized = normalize_dataset(
        _request(
            {
                "kind": "inline_rows",
                "rows": [
                    {"timestamp": "2024-01-02T00:00:00Z", "price": 100},
                    {"timestamp": "2024-01-03T00:00:00Z", "price": 101},
                ],
            }
        )
    )

    class MappingValue:
        def __init__(self, source_field: str, input_field: str) -> None:
            self.source_field = source_field
            self.input_field = input_field

    resolved = resolve_dataset_fields(
        normalized.payload,
        [
            MappingValue("price", "prices"),
            MappingValue("timestamp", "timestamps"),
        ],
        literals={"price_kind": "adjusted"},
    )
    assert resolved == {
        "price_kind": "adjusted",
        "prices": [100, 101],
        "timestamps": ["2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z"],
    }

    with pytest.raises(DatasetRegistryError) as collision:
        resolve_dataset_fields(
            normalized.payload,
            [MappingValue("price", "price_kind")],
            literals={"price_kind": "adjusted"},
        )
    assert collision.value.code == "invalid_field_mapping"
    assert collision.value.details == {"fields": []}
