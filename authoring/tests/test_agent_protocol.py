from __future__ import annotations

from defined_quant import (
    InterpretationMethod,
    OperationManifest,
    OperationProvenance,
    OperationRequest,
    SourceKind,
    VerificationStatus,
    operation_hash,
    operation_protocol_schema,
)


def _request(output_dir: str) -> OperationRequest:
    return OperationRequest(
        component_id="dq.financial_analysis.earnings_comparison",
        input={
            "metric_name": "Revenue",
            "reporting_currency": "USD",
            "value_scale": "millions",
        },
        provenance=OperationProvenance(
            source_kind=SourceKind.USER_ATTACHMENT,
            interpretation_method=InterpretationMethod.AI_INTERPRETED,
            verification_status=VerificationStatus.UNVERIFIED,
            label="earnings.xlsx interpreted by the agent",
            assumptions=("Revenue column treated as USD millions.",),
        ),
        output_dir=output_dir,
    )


def test_operation_hash_excludes_host_paths_but_binds_interpretation() -> None:
    first = _request("/tmp/first")
    second = _request("/tmp/second")

    assert operation_hash(first) == operation_hash(second)

    changed = second.model_copy(
        update={
            "provenance": second.provenance.model_copy(
                update={"verification_status": VerificationStatus.CALLER_CONFIRMED}
            )
        }
    )
    assert operation_hash(first) != operation_hash(changed)


def test_operation_protocol_exports_closed_request_and_manifest_schemas() -> None:
    protocol = operation_protocol_schema()

    assert protocol["name"] == "defined_quant_operation"
    assert protocol["request_schema"]["additionalProperties"] is False
    assert protocol["manifest_schema"]["additionalProperties"] is False
    assert set(protocol) == {
        "failure_schema",
        "manifest_schema",
        "name",
        "request_schema",
        "schema_version",
        "success_schema",
    }
    assert OperationManifest.model_fields["schema_version"].default == 2
