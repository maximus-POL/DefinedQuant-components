"""Small immutable JSON containers for hash-bound host records and failures."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Never, cast

from pydantic import JsonValue


class FrozenJsonDict(dict[str, JsonValue]):
    """A normal JSON-serializable dict whose mutation methods fail closed."""

    @staticmethod
    def _immutable() -> Never:
        raise TypeError("hash-bound JSON objects are immutable")

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

    # Typeshed couples the in-place signature to the non-mutating union overloads; this method
    # intentionally has no successful return path because mutation is forbidden.
    def __ior__(self, _value: Any) -> FrozenJsonDict:  # type: ignore[override,misc]
        self._immutable()

    def __copy__(self) -> FrozenJsonDict:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> FrozenJsonDict:
        return self


class FrozenJsonList(list[JsonValue]):
    """A normal JSON-serializable list whose mutation methods fail closed."""

    @staticmethod
    def _immutable() -> Never:
        raise TypeError("hash-bound JSON arrays are immutable")

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

    # Typeshed couples these in-place signatures to the non-mutating sequence overloads; both
    # intentionally have no successful return path because mutation is forbidden.
    def __iadd__(self, _values: Any) -> FrozenJsonList:  # type: ignore[override,misc]
        self._immutable()

    def __imul__(self, _value: Any) -> FrozenJsonList:  # type: ignore[misc]
        self._immutable()

    def __copy__(self) -> FrozenJsonList:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> FrozenJsonList:
        return self


def freeze_json(value: Any) -> JsonValue:
    """Copy one validated JSON value into recursively immutable containers."""

    if isinstance(value, Mapping):
        return cast(
            JsonValue,
            FrozenJsonDict({str(key): freeze_json(item) for key, item in value.items()}),
        )
    if isinstance(value, (list, tuple)):
        return cast(JsonValue, FrozenJsonList(freeze_json(item) for item in value))
    return cast(JsonValue, value)


def freeze_json_object(value: Mapping[str, Any]) -> FrozenJsonDict:
    """Copy a string-keyed JSON object into an immutable, serializable dict subtype."""

    frozen = freeze_json(value)
    assert isinstance(frozen, FrozenJsonDict)
    return frozen


__all__ = ["FrozenJsonDict", "FrozenJsonList", "freeze_json", "freeze_json_object"]
