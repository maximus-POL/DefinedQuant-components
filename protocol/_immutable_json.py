"""Recursively immutable JSON containers for governance identities and plans."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Never, cast

from pydantic import JsonValue


class FrozenJsonDict(dict[str, JsonValue]):
    """A JSON-serializable mapping whose mutation methods fail closed."""

    @staticmethod
    def _immutable() -> Never:
        raise TypeError("hash-bound governance JSON is immutable")

    def __setitem__(self, _key: str, _value: JsonValue) -> Never:
        self._immutable()

    def __delitem__(self, _key: str) -> Never:
        self._immutable()

    def clear(self) -> Never:
        self._immutable()

    def pop(self, _key: str, _default: Any = None) -> Never:
        self._immutable()

    def popitem(self) -> Never:
        self._immutable()

    def setdefault(self, _key: str, _default: JsonValue = None) -> Never:
        self._immutable()

    def update(self, *args: Any, **kwargs: JsonValue) -> Never:
        del args, kwargs
        self._immutable()

    def __ior__(self, _value: Any) -> FrozenJsonDict:  # type: ignore[override,misc]
        self._immutable()

    def __copy__(self) -> FrozenJsonDict:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> FrozenJsonDict:
        return self


class FrozenJsonList(list[JsonValue]):
    """A JSON-serializable sequence whose mutation methods fail closed."""

    @staticmethod
    def _immutable() -> Never:
        raise TypeError("hash-bound governance JSON is immutable")

    def __setitem__(self, _key: Any, _value: Any) -> Never:
        self._immutable()

    def __delitem__(self, _key: Any) -> Never:
        self._immutable()

    def append(self, _value: JsonValue) -> Never:
        self._immutable()

    def clear(self) -> Never:
        self._immutable()

    def extend(self, _values: Any) -> Never:
        self._immutable()

    def insert(self, _index: Any, _value: JsonValue) -> Never:
        self._immutable()

    def pop(self, _index: Any = -1) -> Never:
        self._immutable()

    def remove(self, _value: JsonValue) -> Never:
        self._immutable()

    def reverse(self) -> Never:
        self._immutable()

    def sort(self, *, key: Any = None, reverse: bool = False) -> Never:
        del key, reverse
        self._immutable()

    def __iadd__(self, _values: Any) -> FrozenJsonList:  # type: ignore[override,misc]
        self._immutable()

    def __imul__(self, _value: Any) -> FrozenJsonList:  # type: ignore[misc]
        self._immutable()

    def __copy__(self) -> FrozenJsonList:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> FrozenJsonList:
        return self


def freeze_json(value: Any) -> Any:
    """Copy nested JSON containers while preserving typed protocol records."""

    if isinstance(value, Mapping):
        return cast(
            JsonValue,
            FrozenJsonDict({str(key): freeze_json(item) for key, item in value.items()}),
        )
    if isinstance(value, (list, tuple)):
        frozen = tuple(freeze_json(item) for item in value)
        if isinstance(value, tuple):
            return frozen
        return cast(JsonValue, FrozenJsonList(frozen))
    return value


__all__ = ["FrozenJsonDict", "FrozenJsonList", "freeze_json"]
