"""Versions accepted by the public Defined Quant protocol package."""

from typing import Final, Literal, TypeAlias

ProtocolVersion: TypeAlias = Literal["0.1.0", "0.2.0", "0.3.0", "0.4.0"]

PROTOCOL_VERSION: Final[Literal["0.4.0"]] = "0.4.0"
SUPPORTED_PROTOCOL_VERSIONS: Final[tuple[ProtocolVersion, ...]] = (
    "0.1.0",
    "0.2.0",
    "0.3.0",
    PROTOCOL_VERSION,
)

__all__ = ["PROTOCOL_VERSION", "SUPPORTED_PROTOCOL_VERSIONS", "ProtocolVersion"]
