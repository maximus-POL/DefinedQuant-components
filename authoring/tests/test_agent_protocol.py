"""Contract tests for the host-neutral typed operation protocol."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, cast

import pytest
from defined_quant_protocol import (
    CANONICALIZATION_ID,
    PROTOCOL_VERSION,
    CallerProvenance,
    ComponentRef,
    FileDigest,
    InterpretationMethod,
    OperationError,
    OperationErrorCode,
    OperationFailure,
    OperationManifest,
    OperationRequest,
    OperationSuccess,
    ProvenanceStatus,
    RunnerIdentity,
    SourceKind,
    SvgArtifact,
    SvgArtifactRequest,
    canonical_hash,
    canonical_json_bytes,
    operation_protocol_schema,
)
from defined_quant_protocol.operation import portable_member_key
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import TypeAdapter, ValidationError

SHA_A = "a" * 64
SHA_B = "b" * 64
COMPONENT = ComponentRef(
    id="dq.market_data.simple_return",
    version="0.1.0",
    subject_hash=SHA_A,
)
WINDOWS_DEVICE_NAMES = (
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
)


def _mixed_ascii_case(value: str) -> str:
    return "".join(
        character.upper() if index % 2 == 0 else character
        for index, character in enumerate(value)
    )


WINDOWS_DEVICE_MEMBER_PATHS = tuple(
    path
    for name in WINDOWS_DEVICE_NAMES
    for spelling in (name, name.upper(), _mixed_ascii_case(name))
    for path in (spelling, f"{spelling}.txt", f"safe/{spelling}.data.json")
)


def test_phase_4_excludes_component_output_provenance() -> None:
    assert PROTOCOL_VERSION == "0.4.0"
    assert {member.value for member in SourceKind} == {
        "user_prompt",
        "user_attachment",
        "external_provider",
        "synthetic",
    }
    assert {member.value for member in InterpretationMethod} == {
        "ai_interpreted",
        "caller_structured",
        "adapter_normalized",
    }


def _request(*, confirmed: bool = False) -> OperationRequest:
    return OperationRequest(
        component=COMPONENT,
        input={"prices": [100.0, 105.0, 102.9]},
        provenance=CallerProvenance(
            source_kind=SourceKind.USER_ATTACHMENT,
            interpretation_method=InterpretationMethod.CALLER_STRUCTURED,
            verification_status=(
                ProvenanceStatus.CALLER_CONFIRMED
                if confirmed
                else ProvenanceStatus.UNVERIFIED
            ),
            label="user-supplied synthetic prices",
        ),
        artifacts=SvgArtifactRequest(),
    )


def _manifest(request: OperationRequest | None = None) -> OperationManifest:
    bound_request = request or _request()
    return OperationManifest(
        operation_hash=bound_request.operation_hash,
        request=bound_request,
        component=bound_request.component,
        runner=RunnerIdentity(name="catalog_runner", version="0.1.0+local.1"),
        input=FileDigest(path="input.json", sha256=SHA_A),
        result=FileDigest(path="result.json", sha256=SHA_B),
        artifacts=(
            SvgArtifact(
                path="artifacts/simple_return.svg",
                visualization_id="simple_return",
                title="Simple return",
                alt_text="A deterministic simple-return chart.",
                visualization_hash=SHA_A,
                sha256=SHA_B,
                renderer="defined_quant_svg",
                renderer_version="0.1.0-rc.1",
            ),
        ),
    )


def test_operation_hash_is_stable_domain_separated_and_semantic() -> None:
    first = _request()
    reordered = OperationRequest.model_validate(
        {
            "provenance": first.provenance.model_dump(mode="json"),
            "artifacts": {"selection": "all", "kind": "svg"},
            "input": {"prices": [100.0, 105.0, 102.9]},
            "component": first.component.model_dump(mode="json"),
            "schema_version": 1,
        }
    )

    assert first.operation_hash == reordered.operation_hash
    assert first.operation_hash == (
        "b52cee2bc9703d64261cc9a5ae4f512aab1bf63d53729f5de894ee2a8b3e50e4"
    )
    assert first.operation_hash != _request(confirmed=True).operation_hash
    assert len(first.operation_hash) == 64

    changed_component = first.model_copy(
        update={"component": first.component.model_copy(update={"subject_hash": SHA_B})}
    )
    assert first.operation_hash != changed_component.operation_hash


@pytest.mark.parametrize(
    ("value", "expected_text", "expected_hash"),
    [
        (
            {"b": 2, "a": 1},
            '["object",[["a",["number","3ff0000000000000"]],'
            '["b",["number","4000000000000000"]]]]',
            "c2140aabfc1f1f588b06a959e0ac58417983c34f5c88db15ffebebbf0670b26b",
        ),
        (
            {"é": "雪", "e\u0301": "🙂"},
            '["object",[["e\u0301",["string","🙂"]],["é",["string","雪"]]]]',
            "d6b6da423df6038d82bc32773e80401bcfdaf866427dfa69c66f27d49fccd81c",
        ),
        (
            1,
            '["number","3ff0000000000000"]',
            "8a2058a5ed3035149cff16e0ac89c49910a6051ca3e8e998f6ac58138266d9c4",
        ),
        (
            1.0,
            '["number","3ff0000000000000"]',
            "8a2058a5ed3035149cff16e0ac89c49910a6051ca3e8e998f6ac58138266d9c4",
        ),
        (
            -0.0,
            '["number","0000000000000000"]',
            "e38e67b2fe2727ac86a17d0c966fa512b2318fea774e9dba9c9b843e179b1578",
        ),
        (
            1.25e-7,
            '["number","3e80c6f7a0b5ed8d"]',
            "3d3381cc7c68edb8f9ffbdd0f979d0e11428b55c516eada1a758bdf0e2c36fbe",
        ),
        (
            [],
            '["array",[]]',
            "7bcf2a54e76de9cecbdf790a5c1dd585ff2bb4d5f3f48dddbe759cb9115aaf61",
        ),
        (
            {},
            '["object",[]]',
            "dac54dad495eb4aa2996e577865322a7073570b1d309af07b9e3085d7fd048b8",
        ),
        (
            9_007_199_254_740_991,
            '["number","433fffffffffffff"]',
            "b85e2c4db9cb58428f56914f83926c969156655f68a1657de58f26c7fa192b87",
        ),
        (
            -9_007_199_254_740_991,
            '["number","c33fffffffffffff"]',
            "84eb3d28ce4987b90f8e65195148720355faa08eeb08289f4c25c232c8a0463f",
        ),
        (
            '"\n\\/\x01',
            r'["string","\"\n\\/\u0001"]',
            "742ee82bc1ec693a0e1bccc6340ab6fe7113f7a4071c58d31544f30698832f72",
        ),
    ],
)
def test_tagged_canonical_normative_vectors(
    value: Any,
    expected_text: str,
    expected_hash: str,
) -> None:
    assert canonical_json_bytes(value) == expected_text.encode("utf-8")
    assert canonical_hash(value, domain="test.vector") == expected_hash


def test_tagged_canonical_object_order_and_number_equivalence() -> None:
    assert canonical_json_bytes({"a": 1, "b": 2}) == canonical_json_bytes({"b": 2, "a": 1})
    assert canonical_json_bytes(1) == canonical_json_bytes(1.0)
    assert canonical_json_bytes(-0.0) == canonical_json_bytes(0)
    assert canonical_hash({}, domain="first.domain") != canonical_hash({}, domain="second.domain")


def test_tagged_canonical_preserves_finite_binary64_outside_safe_integer_range() -> None:
    assert canonical_json_bytes(float(9_007_199_254_740_992)) == (
        b'["number","4340000000000000"]'
    )


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        9_007_199_254_740_992,
        -9_007_199_254_740_992,
        "\ud800",
        {"\udfff": "invalid key"},
    ],
)
def test_tagged_canonical_refuses_ambiguous_or_invalid_values(value: Any) -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes(value)


@pytest.mark.parametrize(
    ("path", "accepted"),
    [
        ("a", True),
        ("input.json", True),
        ("artifacts/simple_return.svg", True),
        ("Artifacts-1/_return.final.svg", True),
        ("a" * 512, True),
        ("/tmp/result.json", False),
        ("C:/result.json", False),
        ("results\\result.json", False),
        ("./result.json", False),
        ("../result.json", False),
        ("results/../result.json", False),
        ("results/.", False),
        ("results//result.json", False),
        ("results/", False),
        (".hidden", False),
        ("result.", False),
        ("result.json.", False),
        ("result.json ", False),
        ("result.json:stream", False),
        ("result..json", False),
        ("PROGRA~1/result.json", False),
        ("result name.json", False),
        ("résult.json", False),
        ("result.json\n", False),
        ("result\tname.json", False),
        ("result\x00name.json", False),
        (".", False),
        ("..", False),
        ("a" * 513, False),
    ],
)
def test_member_path_runtime_and_schema_share_one_portable_matrix(
    path: str,
    accepted: bool,
) -> None:
    path_schema = FileDigest.model_json_schema()["properties"]["path"]
    schema_accepts = Draft202012Validator(path_schema).is_valid(path)
    try:
        FileDigest(path=path, sha256=SHA_A)
    except ValidationError:
        runtime_accepts = False
    else:
        runtime_accepts = True

    assert schema_accepts is accepted
    assert runtime_accepts is accepted


def test_member_path_rule_is_visible_in_public_schemas() -> None:
    file_schema = FileDigest.model_json_schema()
    path_schema = cast(dict[str, object], file_schema["properties"]["path"])
    pattern = cast(str, path_schema["pattern"])

    assert path_schema["minLength"] == 1
    assert path_schema["maxLength"] == 512
    path_exclusions = cast(dict[str, object], path_schema["not"])
    exclusions = cast(list[dict[str, str]], path_exclusions["anyOf"])
    assert exclusions[0] == {"pattern": "[^A-Za-z0-9_./-]"}
    assert exclusions[1]["pattern"].startswith("(^|/)")
    for invalid in ("/result.json", "C:/result.json", "../result.json", "a//b"):
        assert re.fullmatch(pattern, invalid) is None

    manifest_schema = OperationManifest.model_json_schema()
    manifest_file_schema = cast(dict[str, object], manifest_schema["$defs"]["FileDigest"])
    manifest_properties = cast(dict[str, object], manifest_file_schema["properties"])
    manifest_path_schema = cast(dict[str, object], manifest_properties["path"])
    assert manifest_path_schema["pattern"] == pattern


@pytest.mark.parametrize("path", WINDOWS_DEVICE_MEMBER_PATHS)
def test_member_paths_refuse_every_windows_device_alias(path: str) -> None:
    path_schema = FileDigest.model_json_schema()["properties"]["path"]

    assert not Draft202012Validator(path_schema).is_valid(path)
    with pytest.raises(ValidationError, match="safe-ASCII segments"):
        FileDigest(path=path, sha256=SHA_A)
    with pytest.raises(ValueError, match="safe-ASCII segments"):
        portable_member_key(path)


def test_portable_member_key_is_segmentwise_ascii_case_folded() -> None:
    assert portable_member_key("Artifacts/Report.JSON") == "artifacts/report.json"
    assert portable_member_key("A_B-C/D.E") == "a_b-c/d.e"
    assert RunnerIdentity(name="con", version="0.1.0").name == "con"


def test_manifest_reconciles_request_identity_hash_and_members() -> None:
    request = _request()
    manifest = _manifest(request)

    assert OperationSuccess(manifest=manifest).status == "succeeded"

    with pytest.raises(ValidationError, match="operation_hash"):
        OperationManifest(
            **{
                **manifest.model_dump(mode="python"),
                "operation_hash": SHA_B,
            }
        )

    with pytest.raises(ValidationError, match="component does not match"):
        OperationManifest(
            **{
                **manifest.model_dump(mode="python"),
                "component": manifest.component.model_copy(update={"subject_hash": SHA_B}),
            }
        )

    with pytest.raises(ValidationError, match="member paths must be unique"):
        OperationManifest(
            **{
                **manifest.model_dump(mode="python"),
                "result": FileDigest(path="input.json", sha256=SHA_B),
            }
        )

    with pytest.raises(ValidationError, match="member paths must be unique"):
        OperationManifest(
            **{
                **manifest.model_dump(mode="python"),
                "input": FileDigest(path="Input.JSON", sha256=SHA_A),
                "result": FileDigest(path="input.json", sha256=SHA_B),
            }
        )

    with pytest.raises(ValidationError, match="member paths must be unique"):
        OperationManifest(
            **{
                **manifest.model_dump(mode="python"),
                "result": FileDigest(path="MANIFEST.JSON", sha256=SHA_B),
            }
        )


def test_manifest_defaults_to_protocol_0_4_and_parses_older_protocols() -> None:
    current = _manifest()
    protocol_0_1 = current.model_dump(mode="json")
    protocol_0_1["protocol_version"] = "0.1.0"
    protocol_0_2 = current.model_dump(mode="json")
    protocol_0_2["protocol_version"] = "0.2.0"
    protocol_0_3 = current.model_dump(mode="json")
    protocol_0_3["protocol_version"] = "0.3.0"

    assert current.protocol_version == "0.4.0"
    assert OperationManifest.model_validate(protocol_0_1).protocol_version == "0.1.0"
    assert OperationManifest.model_validate(protocol_0_2).protocol_version == "0.2.0"
    assert OperationManifest.model_validate(protocol_0_3).protocol_version == "0.3.0"


def test_manifest_refuses_a_request_mutated_after_hash_capture() -> None:
    request = _request()
    captured_hash = request.operation_hash
    prices = cast(list[float], request.input["prices"])
    prices.append(99.0)

    with pytest.raises(ValidationError, match="operation_hash"):
        OperationManifest(
            operation_hash=captured_hash,
            request=request,
            component=request.component,
            runner=RunnerIdentity(name="catalog_runner", version="0.1.0"),
            input=FileDigest(path="input.json", sha256=SHA_A),
            result=FileDigest(path="result.json", sha256=SHA_B),
            artifacts=(
                SvgArtifact(
                    path="artifacts/simple_return.svg",
                    visualization_id="simple_return",
                    title="Simple return",
                    alt_text="A deterministic simple-return chart.",
                    visualization_hash=SHA_A,
                    sha256=SHA_B,
                    renderer="defined_quant_svg",
                    renderer_version="0.1.0",
                ),
            ),
        )


def test_failure_and_schema_are_closed_and_discriminated() -> None:
    failure = OperationFailure(
        operation_hash=_request().operation_hash,
        error=OperationError(
            code=OperationErrorCode.INVALID_COMPONENT_INPUT,
            message="input did not match the component model",
            component=COMPONENT,
            details={"field": "prices"},
        ),
    )
    assert failure.status == "failed"

    protocol = operation_protocol_schema()
    schemas = cast(dict[str, dict[str, object]], protocol["schemas"])
    result_schema = schemas["result"]
    assert set(protocol) == {
        "canonicalization_vector",
        "canonicalization_id",
        "execution_mode",
        "hash_algorithm",
        "hash_framing",
        "name",
        "operation_request_domain",
        "package",
        "protocol_version",
        "schema_version",
        "schemas",
    }
    assert set(schemas) == {"failure", "manifest", "request", "result", "success"}
    assert protocol["package"] == "defined_quant_protocol"
    assert protocol["protocol_version"] == "0.4.0"
    assert protocol["execution_mode"] == "unmanaged"
    assert protocol["canonicalization_id"] == CANONICALIZATION_ID
    assert protocol["hash_algorithm"] == "sha256"
    assert protocol["operation_request_domain"] == "operation.request.v1"
    framing = cast(dict[str, object], protocol["hash_framing"])
    assert framing == {
        "prefix_utf8": "defined-quant",
        "separator_hex": "00",
        "ordered_parts": [
            "prefix_utf8",
            "canonicalization_id",
            "domain_ascii",
            "canonical_bytes",
        ],
    }
    vector = cast(dict[str, Any], protocol["canonicalization_vector"])
    vector_bytes = canonical_json_bytes(vector["input"])
    assert vector_bytes.hex() == vector["canonical_bytes_hex"]
    assert canonical_hash(vector["input"], domain=vector["domain"]) == vector["sha256"]
    assert schemas["request"]["additionalProperties"] is False
    assert schemas["request"]["x-defined-quant-canonicalization"] == CANONICALIZATION_ID
    assert schemas["manifest"]["additionalProperties"] is False
    assert "discriminator" in result_schema
    request_properties = cast(dict[str, object], schemas["request"]["properties"])
    assert set(request_properties) == {
        "artifacts",
        "component",
        "input",
        "provenance",
        "schema_version",
    }

    result_adapter: TypeAdapter[OperationSuccess | OperationFailure] = TypeAdapter(
        OperationSuccess | OperationFailure
    )
    assert isinstance(result_adapter.validate_python(failure.model_dump()), OperationFailure)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_operation_error_details_refuse_nonfinite_json(value: float) -> None:
    with pytest.raises(ValidationError):
        OperationError(
            code=OperationErrorCode.OPERATION_FAILED,
            message="non-finite detail",
            details={"value": value},
        )

    token = "NaN" if value != value else ("Infinity" if value > 0 else "-Infinity")
    with pytest.raises(ValidationError):
        OperationError.model_validate_json(
            '{"code":"operation_failed","message":"non-finite detail",'
            f'"details":{{"value":{token}}}}}'
        )


def test_operation_error_details_round_trip_without_loss() -> None:
    error = OperationError(
        code=OperationErrorCode.INVALID_COMPONENT_INPUT,
        message="structured validation failure",
        details={"errors": [{"field": "prices", "position": 1}]},
    )

    assert OperationError.model_validate_json(error.model_dump_json()) == error


def test_unmanaged_protocol_cannot_claim_source_bound_provenance() -> None:
    payload = _request().model_dump(mode="json")
    provenance = cast(dict[str, object], payload["provenance"])
    provenance["verification_status"] = "source_bound"

    with pytest.raises(ValidationError):
        OperationRequest.model_validate(payload)


def test_component_reference_requires_exact_lowercase_sha256() -> None:
    payload = COMPONENT.model_dump()
    payload["subject_hash"] = "A" * 64

    with pytest.raises(ValidationError):
        ComponentRef.model_validate(payload)


@pytest.mark.parametrize(
    "version",
    ["0.1.0", "1.0.0-rc.1", "2.3.4+build.7", "3.2.1-alpha.1+sha.abc"],
)
def test_protocol_identities_accept_semver_2_versions(version: str) -> None:
    component = ComponentRef(id=COMPONENT.id, version=version, subject_hash=COMPONENT.subject_hash)
    assert component.version == version
    assert RunnerIdentity(name="catalog_runner", version=version).version == version


@pytest.mark.parametrize("version", ["v1.0.0", "1.0", "1.0.0-01", "1.0.0+", "01.0.0"])
def test_protocol_identities_refuse_malformed_semver(version: str) -> None:
    with pytest.raises(ValidationError):
        RunnerIdentity(name="catalog_runner", version=version)


def test_protocol_package_does_not_import_catalog_or_component_runtime() -> None:
    protocol_root = Path(__file__).resolve().parents[2] / "protocol"
    forbidden_roots = {"categories", "defined_quant", "shared"}

    for source_path in protocol_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots = {alias.name.partition(".")[0] for alias in node.names}
                assert imported_roots.isdisjoint(forbidden_roots), source_path
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
                assert node.module.partition(".")[0] not in forbidden_roots, source_path
