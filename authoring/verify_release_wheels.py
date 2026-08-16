"""Verify and fingerprint the exact core and MCP release wheels."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from zipfile import ZipFile

MANIFEST_NAME = "wheel-manifest.v1.json"


@dataclass(frozen=True, slots=True)
class WheelExpectation:
    filename: str
    project_name: str
    version: str
    requirements: frozenset[str]


EXPECTED_WHEELS = (
    WheelExpectation(
        filename="defined_quant-0.1.3-py3-none-any.whl",
        project_name="defined-quant",
        version="0.1.3",
        requirements=frozenset({"pydantic<3,>=2.7"}),
    ),
    WheelExpectation(
        filename="defined_quant_mcp-0.1.0a1-py3-none-any.whl",
        project_name="defined-quant-mcp",
        version="0.1.0a1",
        requirements=frozenset({"defined-quant==0.1.3", "mcp==2.0.0"}),
    ),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-directory", required=True, type=Path)
    parser.add_argument("--write-manifest", action="store_true")
    return parser


def _metadata(archive: ZipFile, expectation: WheelExpectation) -> Message:
    suffix = expectation.filename.removesuffix("-py3-none-any.whl") + ".dist-info/METADATA"
    matches = [name for name in archive.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"{expectation.filename}: expected exactly one METADATA member")
    return BytesParser(policy=default).parsebytes(archive.read(matches[0]))


def _verify_wheel(path: Path, expectation: WheelExpectation) -> dict[str, str]:
    if path.name != expectation.filename:
        raise ValueError(f"unexpected wheel filename: {path.name}")
    with ZipFile(path) as archive:
        metadata = _metadata(archive, expectation)
        if metadata["Name"] != expectation.project_name:
            raise ValueError(f"{path.name}: unexpected project name")
        if metadata["Version"] != expectation.version:
            raise ValueError(f"{path.name}: unexpected project version")
        requirements = frozenset(metadata.get_all("Requires-Dist", []))
        if requirements != expectation.requirements:
            raise ValueError(f"{path.name}: unexpected runtime requirements")
        wheel_members = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
        if len(wheel_members) != 1:
            raise ValueError(f"{path.name}: expected exactly one WHEEL member")
        wheel_metadata = archive.read(wheel_members[0]).decode("utf-8")
        if "Tag: py3-none-any\n" not in wheel_metadata.replace("\r\n", "\n"):
            raise ValueError(f"{path.name}: wheel is not platform-neutral")
    return {
        "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _projection(directory: Path) -> dict[str, object]:
    wheels = tuple(sorted(directory.glob("*.whl")))
    expected_names = tuple(item.filename for item in EXPECTED_WHEELS)
    if tuple(path.name for path in wheels) != tuple(sorted(expected_names)):
        raise ValueError("wheel directory must contain exactly the core and MCP release wheels")
    by_name = {item.filename: item for item in EXPECTED_WHEELS}
    artifacts = [_verify_wheel(path, by_name[path.name]) for path in wheels]
    return {"schema_version": 1, "artifacts": artifacts}


def main() -> int:
    args = _parser().parse_args()
    directory = args.wheel_directory.resolve()
    projection = _projection(directory)
    manifest = directory / MANIFEST_NAME
    encoded = (json.dumps(projection, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if args.write_manifest:
        if manifest.exists():
            raise SystemExit("wheel manifest already exists")
        manifest.write_bytes(encoded)
    else:
        if not manifest.is_file():
            raise SystemExit("wheel manifest is missing")
        if manifest.read_bytes() != encoded:
            raise SystemExit("wheel artifact digest manifest does not match downloaded wheels")
    print(f"Verified {len(EXPECTED_WHEELS)} exact release wheels in {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
