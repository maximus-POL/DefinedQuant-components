"""Tagged canonical JSON trees and domain-separated SHA-256 bindings."""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Final

from pydantic import BaseModel

CANONICALIZATION_ID: Final = "dq-tagged-json-v1"

_DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_HASH_PREFIX = b"defined-quant"
_MAX_SAFE_INTEGER = (1 << 53) - 1
_SHORT_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


def _valid_utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("canonical strings cannot contain lone Unicode surrogates") from exc


def _number_hex(value: int | float) -> str:
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("canonical integers must be within the IEEE-754 safe-integer range")
        normalized = float(value)
    else:
        if not math.isfinite(value):
            raise ValueError("canonical numbers must be finite")
        if value.is_integer() and abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("integer-valued floats must be within the safe-integer range")
        normalized = value

    if normalized == 0.0:
        normalized = 0.0
    return struct.pack(">d", normalized).hex()


def _tagged_tree(value: Any) -> list[Any]:
    """Transform a JSON-compatible value into the collision-free v1 tagged tree."""

    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        _valid_utf8(value)
        return ["string", value]
    if isinstance(value, int | float):
        return ["number", _number_hex(value)]
    if isinstance(value, Enum):
        return _tagged_tree(value.value)
    if isinstance(value, BaseModel):
        return _tagged_tree(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        entries: list[list[Any]] = []
        keyed_items: list[tuple[bytes, str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            keyed_items.append((_valid_utf8(key), key, item))
        for _, key, item in sorted(keyed_items, key=lambda entry: entry[0]):
            entries.append([key, _tagged_tree(item)])
        return ["object", entries]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return ["array", [_tagged_tree(item) for item in value]]
    raise TypeError(f"{type(value).__name__} is not a canonical JSON value")


def _encode_string(value: str) -> str:
    _valid_utf8(value)
    encoded: list[str] = ['"']
    for character in value:
        escaped = _SHORT_ESCAPES.get(character)
        if escaped is not None:
            encoded.append(escaped)
            continue
        code_point = ord(character)
        if code_point < 0x20:
            encoded.append(f"\\u{code_point:04x}")
        else:
            encoded.append(character)
    encoded.append('"')
    return "".join(encoded)


def _encode_tagged_json(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_encode_tagged_json(item) for item in value) + "]"
    raise TypeError(f"invalid internal tagged-tree value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a value as the exact UTF-8 ``dq-tagged-json-v1`` byte format."""

    return _encode_tagged_json(_tagged_tree(value)).encode("utf-8")


def canonical_hash(value: Any, *, domain: str) -> str:
    """Bind tagged canonical bytes to their algorithm and an explicit hash domain."""

    if _DOMAIN_PATTERN.fullmatch(domain) is None:
        raise ValueError("hash domain must match ^[a-z][a-z0-9_.-]{0,127}$")
    payload = b"\x00".join(
        (
            _HASH_PREFIX,
            CANONICALIZATION_ID.encode("ascii"),
            domain.encode("ascii"),
            canonical_json_bytes(value),
        )
    )
    return hashlib.sha256(payload).hexdigest()


def canonical_hash_framing() -> dict[str, str | list[str]]:
    """Describe the byte framing needed to reproduce every protocol digest."""

    return {
        "prefix_utf8": _HASH_PREFIX.decode("ascii"),
        "separator_hex": "00",
        "ordered_parts": [
            "prefix_utf8",
            "canonicalization_id",
            "domain_ascii",
            "canonical_bytes",
        ],
    }


__all__ = [
    "CANONICALIZATION_ID",
    "canonical_hash",
    "canonical_hash_framing",
    "canonical_json_bytes",
]
