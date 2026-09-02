"""Metadata-only discovery for independently installed adapter distributions.

Discovery reads package metadata and entry-point names only.  It never calls
``EntryPoint.load`` and therefore never imports adapter or provider code.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from types import MappingProxyType
from typing import Any, Protocol, cast

from defined_quant_protocol import AdapterSpec, ArtifactPin, InstallationStatus

ADAPTER_ENTRY_POINT_GROUP = "defined_quant.adapters"

_DISTRIBUTION_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$")
_SAFE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _EntryPoint(Protocol):
    group: str
    name: str
    value: str

    def load(self) -> Any: ...


class _Distribution(Protocol):
    version: str
    entry_points: Iterable[_EntryPoint]
    metadata: Mapping[str, str]
    files: Iterable[Any] | None

    def locate_file(self, path: Any) -> Any: ...


def _canonical_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


@dataclass(frozen=True, slots=True)
class InstalledAdapterDescriptor:
    """Bounded public metadata for one installed adapter entry-point identity."""

    distribution: str
    version: str
    dispatch_key: str
    entry_point_count: int


@dataclass(frozen=True, slots=True)
class InstalledAdapterDiscovery:
    """One immutable metadata snapshot plus private, still-unloaded entry points."""

    descriptors: tuple[InstalledAdapterDescriptor, ...]
    _entries: Mapping[tuple[str, str, str], tuple[_EntryPoint, ...]]
    _distributions: Mapping[tuple[str, str], tuple[_Distribution, ...]]
    _distribution_versions: Mapping[str, tuple[str, ...]]

    def installation(
        self,
        adapter: AdapterSpec,
        artifact: ArtifactPin,
    ) -> InstallationStatus:
        """Report whether an adapter and implementation-owned pin are exactly installed."""

        distribution = _canonical_distribution(adapter.distribution)
        if artifact.distribution != adapter.distribution:
            return InstallationStatus.MISMATCH
        version = artifact.version
        key = (distribution, version, adapter.dispatch_key)
        if (
            len(self._distributions.get((distribution, version), ())) == 1
            and len(self._entries.get(key, ())) == 1
        ):
            return InstallationStatus.INSTALLED
        if distribution in self._distribution_versions:
            return InstallationStatus.MISMATCH
        return InstallationStatus.NOT_INSTALLED

    def _entry_point_for(
        self,
        adapter: AdapterSpec,
        artifact: ArtifactPin,
    ) -> _EntryPoint:
        """Return an opaque unloaded entry point for the trusted catalog only."""

        if artifact.distribution != adapter.distribution:
            raise LookupError("implementation artifact distribution contradicts its adapter")
        distribution = _canonical_distribution(adapter.distribution)
        key = (
            distribution,
            artifact.version,
            adapter.dispatch_key,
        )
        selected = self._entries.get(key, ())
        if len(selected) != 1:
            raise LookupError("adapter entry point is not one exact installed match")
        return selected[0]

    def _installed_match(
        self,
        adapter: AdapterSpec,
        artifact: ArtifactPin,
    ) -> tuple[_Distribution, _EntryPoint]:
        """Return one exact still-unloaded distribution/entry-point match.

        This is an internal handoff to the artifact verifier and trusted catalog.  It exposes
        opaque package-metadata objects, never imported adapter code.
        """

        if artifact.distribution != adapter.distribution:
            raise LookupError("implementation artifact distribution contradicts its adapter")
        distribution = _canonical_distribution(adapter.distribution)
        version = artifact.version
        selected_distributions = self._distributions.get((distribution, version), ())
        selected_entries = self._entries.get(
            (distribution, version, adapter.dispatch_key),
            (),
        )
        if len(selected_distributions) != 1 or len(selected_entries) != 1:
            raise LookupError("adapter is not one exact installed distribution match")
        return selected_distributions[0], selected_entries[0]


def discover_installed_adapters(
    *,
    distributions: Iterable[_Distribution] | None = None,
) -> InstalledAdapterDiscovery:
    """Enumerate installed adapter entry-point metadata without importing adapter code.

    ``distributions`` is injectable so hosts and tests can supply an already bounded metadata
    view.  Omitting it reads the active interpreter's installed distribution metadata.
    """

    selected: Iterable[_Distribution]
    if distributions is None:
        selected = cast(Iterable[_Distribution], importlib_metadata.distributions())
    else:
        selected = distributions

    entries: dict[tuple[str, str, str], list[_EntryPoint]] = defaultdict(list)
    exact_distributions: dict[tuple[str, str], list[_Distribution]] = defaultdict(list)
    versions: dict[str, set[str]] = defaultdict(set)
    for distribution in selected:
        try:
            raw_name = distribution.metadata.get("Name")
            version = distribution.version
        except (AttributeError, KeyError, TypeError):
            continue
        if (
            not isinstance(raw_name, str)
            or _DISTRIBUTION_PATTERN.fullmatch(raw_name) is None
            or not isinstance(version, str)
            or not version
            or len(version) > 128
        ):
            continue
        canonical_name = _canonical_distribution(raw_name)
        versions[canonical_name].add(version)
        exact_distributions[(canonical_name, version)].append(distribution)
        try:
            entry_points = distribution.entry_points
        except (AttributeError, TypeError):
            continue
        for entry_point in entry_points:
            try:
                group = entry_point.group
                dispatch_key = entry_point.name
            except (AttributeError, TypeError):
                continue
            if (
                group != ADAPTER_ENTRY_POINT_GROUP
                or not isinstance(dispatch_key, str)
                or _SAFE_ID_PATTERN.fullmatch(dispatch_key) is None
            ):
                continue
            entries[(canonical_name, version, dispatch_key)].append(entry_point)

    frozen_entries = MappingProxyType(
        {
            key: tuple(value)
            for key, value in sorted(
                entries.items(),
                key=lambda item: (
                    item[0][0].encode("utf-8"),
                    item[0][1],
                    item[0][2].encode("utf-8"),
                ),
            )
        }
    )
    frozen_versions = MappingProxyType(
        {
            key: tuple(sorted(value))
            for key, value in sorted(
                versions.items(),
                key=lambda item: item[0].encode("utf-8"),
            )
        }
    )
    frozen_distributions = MappingProxyType(
        {
            key: tuple(value)
            for key, value in sorted(
                exact_distributions.items(),
                key=lambda item: (
                    item[0][0].encode("utf-8"),
                    item[0][1],
                ),
            )
        }
    )
    descriptors = tuple(
        InstalledAdapterDescriptor(
            distribution=distribution,
            version=version,
            dispatch_key=dispatch_key,
            entry_point_count=len(value),
        )
        for (distribution, version, dispatch_key), value in frozen_entries.items()
    )
    return InstalledAdapterDiscovery(
        descriptors=descriptors,
        _entries=frozen_entries,
        _distributions=frozen_distributions,
        _distribution_versions=frozen_versions,
    )


__all__ = [
    "ADAPTER_ENTRY_POINT_GROUP",
    "InstalledAdapterDescriptor",
    "InstalledAdapterDiscovery",
    "discover_installed_adapters",
]
