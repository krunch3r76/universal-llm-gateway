"""Boot briefing identity — capability policy vs logical continuity."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .profiles import family_anchor, get_profile
from .registry import (
    normalize_agent_slug,
    normalize_bus_address,
    resolve_capability_cell_from_bus_address,
)
from .session_id import SESSION_ID_RE

_CANONICAL_KEY_RE = re.compile(r"^[a-z]+(-[a-z]+)*$")
_SUPPORTED_SCHEMA_VERSION = 1


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
class _BootIdentityRegistry:
    by_alias: dict[str, str]
    seats: dict[str, dict[str, Any]]

    @classmethod
    def empty(cls) -> _BootIdentityRegistry:
        return cls(by_alias={}, seats={})

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


def _logical_boot_identity(canonical_key: str, entry: dict[str, Any]) -> BootIdentity:
    family = str(entry["family"])
    platform = str(entry["platform"])
    inbox = entry.get("inbox_recipient")
    inbox_recipient = str(inbox).strip() if inbox else canonical_key
    return BootIdentity(
        family=family,
        platform=platform,
        agent=canonical_key,
        session_prefix=canonical_key,
        inbox_recipient=inbox_recipient,
        self_entity_id=f"seat:{canonical_key}",
        logical_seat_id=canonical_key,
        house=str(entry["house"]) if entry.get("house") is not None else None,
        surface=str(entry["surface"]) if entry.get("surface") is not None else None,
    )


def _validate_canonical_key(key: str, *, context: str) -> None:
    if not _CANONICAL_KEY_RE.fullmatch(key):
        raise BootIdentityConfigError(
            "invalid_canonical_key",
            f"{context}: canonical key {key!r} must be lowercase alphabetic hyphen components",
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


def _parse_registry_document(raw: Any, *, source: str) -> _BootIdentityRegistry:
    if not isinstance(raw, dict):
        raise BootIdentityConfigError("malformed_yaml", f"{source}: root must be a mapping")
    version = raw.get("schema_version")
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise BootIdentityConfigError(
            "unsupported_schema_version",
            f"{source}: schema_version must be {_SUPPORTED_SCHEMA_VERSION}, got {version!r}",
        )
    seats_raw = raw.get("seats")
    if not isinstance(seats_raw, dict):
        raise BootIdentityConfigError("malformed_yaml", f"{source}: seats must be a mapping")

    from .profiles import load_profiles, load_roles

    reserved: dict[str, str] = {}
    for family, platform in load_profiles():
        slug = f"{family}-{platform}"
        reserved[_normalize_query(slug)] = f"capability:{slug}"
        reserved[_normalize_query(normalize_bus_address(slug))] = f"bus:{slug}"
    for role in load_roles():
        reserved[_normalize_query(role)] = f"role:{role}"

    seats: dict[str, dict[str, Any]] = {}
    by_alias: dict[str, str] = {}

    for canonical_key, entry in seats_raw.items():
        if not isinstance(entry, dict):
            raise BootIdentityConfigError(
                "malformed_seat",
                f"{source}: seat {canonical_key!r} must be a mapping",
            )
        norm_key = _normalize_query(str(canonical_key))
        _validate_canonical_key(norm_key, context=f"{source} seats[{canonical_key!r}]")
        if norm_key in by_alias:
            raise BootIdentityConfigError(
                "duplicate_canonical_key",
                f"{source}: duplicate seat key {canonical_key!r}",
            )
        unknown = set(entry) - {
            "aliases",
            "family",
            "platform",
            "house",
            "surface",
            "inbox_recipient",
        }
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

        aliases_raw = entry.get("aliases") or []
        if not isinstance(aliases_raw, list):
            raise BootIdentityConfigError(
                "malformed_aliases",
                f"{source}: seat {canonical_key!r} aliases must be a list",
            )
        alias_keys = [_normalize_query(str(canonical_key))] + [
            _normalize_query(str(a)) for a in aliases_raw
        ]
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
            if alias in reserved:
                raise BootIdentityConfigError(
                    "alias_collision",
                    f"{source}: alias {alias!r} collides with {reserved[alias]}",
                )
            if alias in by_alias and by_alias[alias] != norm_key:
                raise BootIdentityConfigError(
                    "alias_collision",
                    f"{source}: alias {alias!r} maps to both "
                    f"{by_alias[alias]!r} and {norm_key!r}",
                )
            by_alias[alias] = norm_key
        seats[norm_key] = {
            "family": family,
            "platform": platform,
            "house": entry.get("house"),
            "surface": entry.get("surface"),
            "inbox_recipient": entry.get("inbox_recipient"),
        }

    return _BootIdentityRegistry(by_alias=by_alias, seats=seats)


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
    except OSError as exc:
        raise BootIdentityConfigError(
            "unreadable_configured_file",
            f"cannot read boot identities file {path}: {exc}",
        ) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BootIdentityConfigError("malformed_yaml", f"{path}: {exc}") from exc
    return _parse_registry_document(raw, source=str(path))


def clear_boot_identity_registry_cache() -> None:
    """Test helper — drop cached env-path registry loads."""
    _load_boot_identity_registry_cached.cache_clear()


@lru_cache(maxsize=4)
def _load_boot_identity_registry_cached(env_path: str) -> _BootIdentityRegistry:
    if not env_path:
        return _BootIdentityRegistry.empty()
    return load_boot_identity_registry(Path(env_path))


def _registry_for_lookup() -> _BootIdentityRegistry:
    env_path = os.environ.get("CORTEX_BOOT_IDENTITIES_PATH", "").strip()
    if not env_path:
        return _BootIdentityRegistry.empty()
    return _load_boot_identity_registry_cached(env_path)


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
    return {
        "error": (
            f"seat={seat!r} did not resolve to a known bus address, "
            "capability cell, or registered logical seat."
        ),
        "reason": "seat_unresolved",
        "missing_fields": ["seat"],
    }


def boot_identity_config_error(exc: BootIdentityConfigError) -> dict[str, Any]:
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
