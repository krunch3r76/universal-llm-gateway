"""Boot briefing identity — capability policy vs logical continuity."""

from __future__ import annotations

import os
import re
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode

from .profiles import family_anchor, get_profile, known_families, load_roles
from .registry import (
    STAGE_MANAGER_ADDRESSES,
    _dispatch_aliases,
    normalize_agent_slug,
    normalize_bus_address,
    resolve_capability_cell_from_bus_address,
)
from .session_id import SESSION_ID_RE

_CANONICAL_KEY_RE = re.compile(r"^[a-z]+(-[a-z]+)*$")
_SUPPORTED_SCHEMA_VERSION = 1
_INSPECT_PREFIX = "inspect"
_HOUSE_RE = re.compile(r"^[0-9]+$")
_ALLOWED_ROOT_KEYS = frozenset({"schema_version", "seats"})
_ALLOWED_SEAT_KEYS = frozenset(
    {"aliases", "family", "platform", "house", "surface", "inbox_recipient"}
)


class BootIdentityConfigError(Exception):
    """Private boot-seats registry is configured but invalid."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class BootIdentity:
    """Resolved identity for cortex_brief / boot_inspect."""

    family: str
    platform: str
    agent: str
    session_prefix: str
    inbox_recipient: str
    self_entity_id: str
    logical_seat_id: str | None = None
    house: str | None = None
    surface: str | None = None

    @property
    def capability_slug(self) -> str:
        return f"{self.family}-{self.platform}"

    @property
    def is_logical(self) -> bool:
        return self.logical_seat_id is not None


@dataclass(frozen=True, slots=True)
class _SeatEntry:
    family: str
    platform: str
    house: str | None
    surface: str | None
    inbox_recipient: str | None


@dataclass(frozen=True, slots=True)
class _BootIdentityRegistry:
    by_alias: types.MappingProxyType[str, str]
    seats: types.MappingProxyType[str, _SeatEntry]

    @classmethod
    def empty(cls) -> _BootIdentityRegistry:
        return cls(
            by_alias=types.MappingProxyType({}),
            seats=types.MappingProxyType({}),
        )

    def lookup(self, query: str) -> BootIdentity | None:
        key = _normalize_query(query)
        if not key:
            return None
        canonical = self.by_alias.get(key)
        if canonical is None:
            return None
        entry = self.seats.get(canonical)
        if entry is None:
            return None
        return _logical_boot_identity(canonical, entry)


def _normalize_query(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def legacy_boot_identity(family: str, platform: str) -> BootIdentity:
    """Reproduce pre-logical-seat boot identity for a capability cell."""
    capability_slug = f"{family}-{platform}"
    return BootIdentity(
        family=family,
        platform=platform,
        agent=capability_slug,
        session_prefix=normalize_bus_address(capability_slug),
        inbox_recipient=capability_slug,
        self_entity_id=family_anchor(family),
    )


def _logical_boot_identity(canonical_key: str, entry: _SeatEntry) -> BootIdentity:
    inbox = entry.inbox_recipient
    inbox_recipient = inbox.strip() if inbox else canonical_key
    return BootIdentity(
        family=entry.family,
        platform=entry.platform,
        agent=canonical_key,
        session_prefix=canonical_key,
        inbox_recipient=inbox_recipient,
        self_entity_id=f"seat:{canonical_key}",
        logical_seat_id=f"seat:{canonical_key}",
        house=entry.house,
        surface=entry.surface,
    )


def _validate_canonical_key(key: str, *, context: str) -> None:
    if not isinstance(key, str):
        raise BootIdentityConfigError(
            "noncanonical_key",
            f"{context}: canonical key must be a string, got {type(key).__name__}",
        )
    if not _CANONICAL_KEY_RE.fullmatch(key):
        raise BootIdentityConfigError(
            "noncanonical_key",
            f"{context}: canonical key {key!r} must be lowercase alphabetic hyphen components",
        )
    first = key.split("-", 1)[0]
    if first == _INSPECT_PREFIX:
        raise BootIdentityConfigError(
            "reserved_key",
            f"{context}: first component {first!r} is reserved",
        )
    prefix_probe = f"{key}-2026-01-01-010101-abc"
    if not SESSION_ID_RE.fullmatch(prefix_probe):
        raise BootIdentityConfigError(
            "invalid_session_prefix",
            f"{context}: key {key!r} is not a valid session-id prefix",
        )


def _reject_traversal(path: Path) -> None:
    if ".." in path.parts:
        raise BootIdentityConfigError(
            "path_traversal",
            f"boot identities path must not traverse: {path}",
        )


def _strict_load_yaml(text: str, *, source: str) -> Any:
    class _StrictLoader(yaml.SafeLoader):
        pass

    def _mapping(loader: yaml.SafeLoader, node: MappingNode, deep: bool = False) -> dict:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                if key in mapping:
                    raise BootIdentityConfigError(
                        "duplicate_yaml_key",
                        f"{source}: duplicate YAML key {key!r}",
                    )
            except TypeError as exc:
                raise BootIdentityConfigError(
                    "bad_yaml_key",
                    f"{source}: mapping key must be hashable, got {type(key).__name__}",
                ) from exc
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    _StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _mapping,
    )
    try:
        return yaml.load(text, Loader=_StrictLoader)
    except BootIdentityConfigError:
        raise
    except yaml.YAMLError as exc:
        raise BootIdentityConfigError("malformed_yaml", f"{source}: {exc}") from exc


def _check_runtime_collision(name: str, *, context: str) -> None:
    probe = name.strip()
    if resolve_legacy_boot_identity(probe) is not None:
        raise BootIdentityConfigError(
            "alias_collision",
            f"{context}: {name!r} collides with legacy boot resolution",
        )
    if normalize_bus_address(probe) != probe:
        raise BootIdentityConfigError(
            "alias_collision",
            f"{context}: {name!r} collides with a normalized bus address",
        )
    norm = _normalize_query(probe)
    if norm in _dispatch_aliases():
        raise BootIdentityConfigError(
            "alias_collision",
            f"{context}: {name!r} collides with dispatch alias {norm!r}",
        )
    if probe in STAGE_MANAGER_ADDRESSES or norm in {
        _normalize_query(x) for x in STAGE_MANAGER_ADDRESSES
    }:
        raise BootIdentityConfigError(
            "alias_collision",
            f"{context}: {name!r} collides with stage-manager address",
        )
    if probe in known_families():
        raise BootIdentityConfigError(
            "alias_collision",
            f"{context}: {name!r} collides with known family name",
        )
    for role in load_roles():
        if role == probe or _normalize_query(role) == norm:
            raise BootIdentityConfigError(
                "alias_collision",
                f"{context}: {name!r} collides with role {role!r}",
            )


def _parse_registry_document(raw: Any, *, source: str) -> _BootIdentityRegistry:
    if not isinstance(raw, dict):
        raise BootIdentityConfigError("malformed_yaml", f"{source}: root must be a mapping")
    unknown_root = set(raw) - _ALLOWED_ROOT_KEYS
    if unknown_root:
        raise BootIdentityConfigError(
            "unknown_fields",
            f"{source}: unknown root fields {sorted(unknown_root)}",
        )
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise BootIdentityConfigError(
            "unsupported_schema_version",
            f"{source}: schema_version must be integer {_SUPPORTED_SCHEMA_VERSION}, got {version!r}",
        )
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise BootIdentityConfigError(
            "unsupported_schema_version",
            f"{source}: schema_version must be {_SUPPORTED_SCHEMA_VERSION}, got {version!r}",
        )
    seats_raw = raw.get("seats")
    if not isinstance(seats_raw, dict):
        raise BootIdentityConfigError("malformed_yaml", f"{source}: seats must be a mapping")

    seats: dict[str, _SeatEntry] = {}
    by_alias: dict[str, str] = {}

    for canonical_key, entry in seats_raw.items():
        ctx = f"{source} seats[{canonical_key!r}]"
        _validate_canonical_key(canonical_key, context=ctx)
        _check_runtime_collision(canonical_key, context=ctx)
        if not isinstance(entry, dict):
            raise BootIdentityConfigError(
                "malformed_seat",
                f"{source}: seat {canonical_key!r} must be a mapping",
            )
        unknown = set(entry) - _ALLOWED_SEAT_KEYS
        if unknown:
            raise BootIdentityConfigError(
                "unknown_fields",
                f"{source}: seat {canonical_key!r} has unknown fields {sorted(unknown)}",
            )
        family = entry.get("family")
        platform = entry.get("platform")
        if not isinstance(family, str) or not isinstance(platform, str):
            raise BootIdentityConfigError(
                "bad_profile",
                f"{source}: seat {canonical_key!r} requires family and platform strings",
            )
        try:
            get_profile(family, platform)
        except KeyError as exc:
            raise BootIdentityConfigError(
                "bad_profile",
                f"{source}: seat {canonical_key!r} references unknown capability "
                f"({family!r}, {platform!r})",
            ) from exc

        if "house" in entry:
            house_val = entry["house"]
            if house_val is None:
                raise BootIdentityConfigError(
                    "bad_field_type",
                    f"{source}: seat {canonical_key!r} house must be a digit string",
                )
            if isinstance(house_val, str) and _HOUSE_RE.fullmatch(house_val):
                house: str | None = house_val
            else:
                raise BootIdentityConfigError(
                    "bad_field_type",
                    f"{source}: seat {canonical_key!r} house must be a digit string",
                )
        else:
            house = None

        if "surface" in entry:
            surface_val = entry["surface"]
            if surface_val is None or not isinstance(surface_val, str) or not surface_val.strip():
                raise BootIdentityConfigError(
                    "bad_field_type",
                    f"{source}: seat {canonical_key!r} surface must be a nonblank string",
                )
            surface = surface_val.strip()
        else:
            surface = None

        if "inbox_recipient" in entry:
            inbox_val = entry["inbox_recipient"]
            if inbox_val is None or not isinstance(inbox_val, str) or not inbox_val.strip():
                raise BootIdentityConfigError(
                    "bad_field_type",
                    f"{source}: seat {canonical_key!r} inbox_recipient must be a nonblank string",
                )
            inbox_recipient = inbox_val.strip()
        else:
            inbox_recipient = None

        if "aliases" not in entry:
            aliases_raw: list[Any] = []
        else:
            aliases_val = entry["aliases"]
            if aliases_val is None or isinstance(aliases_val, (bool, int, str, dict)):
                raise BootIdentityConfigError(
                    "malformed_aliases",
                    f"{source}: seat {canonical_key!r} aliases must be a list",
                )
            if not isinstance(aliases_val, list):
                raise BootIdentityConfigError(
                    "malformed_aliases",
                    f"{source}: seat {canonical_key!r} aliases must be a list",
                )
            aliases_raw = aliases_val
        alias_keys = [_normalize_query(canonical_key)] + []
        for item in aliases_raw:
            if not isinstance(item, str) or not item.strip():
                raise BootIdentityConfigError(
                    "malformed_aliases",
                    f"{source}: seat {canonical_key!r} aliases must be nonblank strings",
                )
            alias_keys.append(_normalize_query(item))

        seen: set[str] = set()
        for alias in alias_keys:
            if not alias:
                continue
            if alias in seen:
                raise BootIdentityConfigError(
                    "duplicate_alias",
                    f"{source}: seat {canonical_key!r} lists duplicate alias {alias!r}",
                )
            seen.add(alias)
            _check_runtime_collision(alias, context=f"{source} alias {alias!r}")
            if alias in by_alias and by_alias[alias] != canonical_key:
                raise BootIdentityConfigError(
                    "alias_collision",
                    f"{source}: alias {alias!r} maps to both "
                    f"{by_alias[alias]!r} and {canonical_key!r}",
                )
            by_alias[alias] = canonical_key

        seats[canonical_key] = _SeatEntry(
            family=family,
            platform=platform,
            house=house,
            surface=surface,
            inbox_recipient=inbox_recipient,
        )

    return _BootIdentityRegistry(
        by_alias=types.MappingProxyType(by_alias),
        seats=types.MappingProxyType(seats),
    )


def load_boot_identity_registry(path: Path | None = None) -> _BootIdentityRegistry:
    """Load the private logical-seat registry.

    Unset path → empty immutable lookup. Configured but missing/unreadable/malformed
    paths raise ``BootIdentityConfigError`` (no fallback).
    """
    if path is None:
        env_path = os.environ.get("CORTEX_BOOT_IDENTITIES_PATH", "").strip()
        if not env_path:
            return _BootIdentityRegistry.empty()
        path = Path(env_path)
    _reject_traversal(path)
    if not path.is_file():
        raise BootIdentityConfigError(
            "missing_configured_file",
            f"boot identities file not found: {path}",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BootIdentityConfigError(
            "invalid_utf8",
            f"boot identities file {path} is not valid UTF-8: {exc}",
        ) from exc
    except OSError as exc:
        raise BootIdentityConfigError(
            "unreadable_configured_file",
            f"cannot read boot identities file {path}: {exc}",
        ) from exc
    raw = _strict_load_yaml(text, source=str(path))
    return _parse_registry_document(raw, source=str(path))


def _registry_for_lookup() -> _BootIdentityRegistry:
    return load_boot_identity_registry()


def resolve_legacy_boot_identity(seat: str) -> BootIdentity | None:
    """Resolve legacy capability-cell identity only (no private registry)."""
    return _legacy_from_capability_cell(seat)


def _legacy_from_capability_cell(seat: str) -> BootIdentity | None:
    bus_addr = normalize_bus_address(seat)
    resolved = resolve_capability_cell_from_bus_address(bus_addr)
    if resolved is None:
        slug = normalize_agent_slug(seat)
        parts = slug.split("-", 1)
        if len(parts) == 2:
            try:
                get_profile(parts[0], parts[1])
            except KeyError:
                return None
            return legacy_boot_identity(parts[0], parts[1])
        return None
    family, platform = resolved
    try:
        get_profile(family, platform)
    except KeyError:
        return None
    return legacy_boot_identity(family, platform)


def seat_unresolved_error(seat: str) -> dict[str, Any]:
    """Structured error when ``seat`` does not resolve to legacy or logical identity."""
    return {
        "error": (
            f"seat={seat!r} did not resolve to a known bus address, "
            "capability cell, or registered logical seat."
        ),
        "reason": "seat_unresolved",
        "missing_fields": ["seat"],
    }


def logical_seat_requires_identity_error(seat: str) -> dict[str, Any]:
    """Structured error when tuple resolution hits a registry seat needing full identity."""
    return {
        "error": (
            f"seat={seat!r} is a registered logical seat; "
            "use resolve_boot_identity / cortex_brief with seat= for full identity."
        ),
        "reason": "logical_seat_requires_identity",
        "missing_fields": ["seat"],
    }


def boot_identity_config_error(exc: BootIdentityConfigError) -> dict[str, Any]:
    """Map a registry parse/load failure to a wire-shaped boot error payload."""
    return {
        "error": exc.detail,
        "reason": exc.code,
        "missing_fields": [],
    }


def resolve_boot_identity_for_seat(seat: str) -> BootIdentity | dict[str, Any]:
    """Resolve an explicit ``seat`` string to identity (legacy or logical registry)."""
    legacy = _legacy_from_capability_cell(seat)
    if legacy is not None:
        return legacy
    try:
        logical = _registry_for_lookup().lookup(seat)
    except BootIdentityConfigError as exc:
        return boot_identity_config_error(exc)
    if logical is not None:
        return logical
    return seat_unresolved_error(seat)


def boot_identity_to_wire(identity: BootIdentity) -> dict[str, Any]:
    """Serialize identity for boot tool responses."""
    payload: dict[str, Any] = {
        "family": identity.family,
        "platform": identity.platform,
        "agent": identity.agent,
        "capability_slug": identity.capability_slug,
        "session_prefix": identity.session_prefix,
        "inbox_recipient": identity.inbox_recipient,
        "self_entity_id": identity.self_entity_id,
        "is_logical": identity.is_logical,
    }
    if identity.logical_seat_id:
        payload["logical_seat_id"] = identity.logical_seat_id
    if identity.house is not None:
        payload["house"] = identity.house
    if identity.surface is not None:
        payload["surface"] = identity.surface
    return payload
