"""Agent model registry — dispatch identity resolved from CapabilityProfile.

Two normalization layers (identity doctrine Phase 2 §B):
- ``normalize_bus_address`` — endpoint mailbox addressing (``web-anthropic``,
  ``cursor``, ``api-openai``); alias-complete across old ``{family}-{platform}``
  slugs and nicknames.
- ``normalize_agent_slug`` — capability-cell key (``{family}-{platform}``);
  unchanged for model/provider/family resolution and lead-seat checks.

``web`` seats are not dispatch targets — strategic-advisor seats only
(∀ dispatch with a web seat slug: caller error; see Cortex assertion 5258).
"""

from __future__ import annotations

import functools

from .profiles import (
    get_profile,
    load_lead_agent_slugs,
    load_profiles,
    load_roles,
    seat_to_family,
)

# Endpoint mailboxes only — sdk/subagent are capability routing, not bus addresses.
_BUS_ADDRESS_EXCLUDED_PLATFORMS = frozenset({"sdk", "subagent"})


def _normalize_agent_key(slug: str) -> str:
    return slug.strip().lower().replace("-", "_").replace(" ", "_")


# Legacy seat spellings → canonical {family}-{platform} slug. HAND-MAINTAINED
# residue: ONLY spellings that cannot be derived from config/agents.yaml
# (historical nicknames). Underscore forms of canonical seat/role slugs are
# derived in _dispatch_aliases(); adding a derivable key here raises at import
# and fails test_legacy_residue_not_derivable.
_LEGACY_ALIASES: dict[str, str] = {
    "cursor": "claude-cursor",
    "cursor_claude": "claude-cursor",
    "web": "claude-web",
    "web_claude": "claude-web",
    "api": "claude-api",
    "api_claude": "claude-api",
    "cursor_grok": "grok-cursor",
    "grok": "grok-cursor",
    "web_grok": "grok-web",
    "superheavy": "grok-web",
    "cursor_gemini": "gemini-cursor",
    "gemini": "gemini-cursor",
    "web_gemini": "gemini-web",
}


@functools.cache
def _dispatch_aliases() -> dict[str, str]:
    """Merged alias map — legacy residue + spellings derived from agents.yaml.

    Derived entries: underscore normalization of every canonical seat slug
    ({family}-{platform} profile cell) and every role slug. New seats/roles
    resolve without touching any hand-maintained list (assertion 13585 class).

    Provider-scoped bus addresses (``web-anthropic``, ``api-openai``, …) alias to
    their capability cells so capability-keyed queries resolve post-mint address
    forms. Bare ``cursor`` is excluded (non-injective fold — legacy alias only).
    """
    derived: dict[str, str] = {}
    for family, platform in load_profiles():
        slug = f"{family}-{platform}"
        derived[_normalize_agent_key(slug)] = slug
    for role_slug in load_roles():
        derived[_normalize_agent_key(role_slug)] = role_slug
    overlap = _LEGACY_ALIASES.keys() & derived.keys()
    if overlap:
        raise RuntimeError(
            f"_LEGACY_ALIASES shadows derivable spellings: {sorted(overlap)} — "
            "remove them; derivation owns these keys."
        )
    merged: dict[str, str] = {**_LEGACY_ALIASES, **derived}
    address_aliases: dict[str, str] = {}
    for cell, addr in _bus_address_map().items():
        if addr == "cursor":
            continue
        norm_addr = _normalize_agent_key(addr)
        if norm_addr in address_aliases and address_aliases[norm_addr] != cell:
            raise RuntimeError(
                f"_dispatch_aliases non-injective address alias: {addr!r} "
                f"← {address_aliases[norm_addr]!r} and {cell!r}"
            )
        address_aliases[norm_addr] = cell
    for norm_addr, cell in address_aliases.items():
        existing = merged.get(norm_addr)
        if existing is not None and existing != cell:
            raise RuntimeError(
                f"_dispatch_aliases address alias shadows canonical: {norm_addr!r} "
                f"→ {cell!r} conflicts with existing {existing!r}"
            )
        merged[norm_addr] = cell
    return merged


@functools.cache
def _bus_address_map() -> dict[str, str]:
    """Capability cell slug → canonical bus address (derived from load_profiles)."""
    mapping: dict[str, str] = {}
    for (family, platform), profile in load_profiles().items():
        if platform in _BUS_ADDRESS_EXCLUDED_PLATFORMS:
            continue
        cell = f"{family}-{platform}"
        if platform == "cursor":
            mapping[cell] = "cursor"
        else:
            mapping[cell] = f"{platform}-{profile.provider}"
    addr_to_cells: dict[str, list[str]] = {}
    for cell, addr in mapping.items():
        addr_to_cells.setdefault(addr, []).append(cell)
    for addr, cells in addr_to_cells.items():
        if addr == "cursor":
            continue
        if len(cells) > 1:
            raise RuntimeError(
                f"_bus_address_map non-injective: {addr!r} ← {sorted(cells)}"
            )
    return mapping


@functools.cache
def _bus_address_aliases() -> dict[str, str]:
    """Normalized spelling → canonical bus address (alias-complete old↔new)."""
    cell_to_addr = _bus_address_map()
    aliases: dict[str, str] = {}
    for addr in set(cell_to_addr.values()):
        aliases[_normalize_agent_key(addr)] = addr
    for alias_norm, cell in _dispatch_aliases().items():
        if cell in cell_to_addr:
            aliases[alias_norm] = cell_to_addr[cell]
    return aliases


def normalize_bus_address(slug: str) -> str:
    """Normalize a bus ``to``/``from_agent`` value to a canonical endpoint address.

    Canonical forms: ``cursor`` | ``web-{provider}`` | ``api-{provider}`` |
    ``api-multi-{provider}``. Old ``{family}-{platform}`` slugs and nicknames
    (``web``, ``cursor``, ``web_claude``) alias to the same canonical. Non-endpoint
    slugs (roles, ``cursor-sdk``, ``subagent-subagent``) pass through unchanged.
    """
    if not isinstance(slug, str):
        slug = str(slug)
    norm = _normalize_agent_key(slug)
    hit = _bus_address_aliases().get(norm)
    if hit is not None:
        return hit
    cell = normalize_agent_slug(slug)
    return _bus_address_map().get(cell, cell)


def resolve_capability_cell_from_bus_address(
    address: str,
) -> tuple[str, str] | None:
    """Map a bus endpoint address to ``(family, platform)`` for boot resolution.

    Ambiguous ``cursor`` defaults to ``(claude, cursor)``. Provider-scoped
    addresses are unique. Non-endpoint slugs fall back to capability-cell parse.
    """
    bus_addr = normalize_bus_address(address)
    if bus_addr == "cursor":
        return "claude", "cursor"
    cell_map = _bus_address_map()
    for cell, addr in cell_map.items():
        if addr == bus_addr:
            family, _, platform = cell.partition("-")
            return family, platform
    canonical = normalize_agent_slug(address)
    if canonical in cell_map:
        family, _, platform = canonical.partition("-")
        return family, platform
    family = seat_to_family(canonical)
    if family is None:
        return None
    parts = canonical.split("-", 1)
    if len(parts) == 2:
        return family, parts[1]
    return None


def normalize_agent_slug(slug: str) -> str:
    """Normalize dispatch agent slug to canonical seat or role slug.

    Used by the frontier dispatch pipeline to handle case/spelling variants
    in team_dispatch(role=...) calls.

    Handles:
    - Case variations
    - Hyphen/underscore/space variants → canonical {family}-{platform} form
    - Legacy seat slugs → new {family}-{platform} seat slugs

    Returns a canonical seat slug ({family}-{platform}) or role slug.
    """
    if not isinstance(slug, str):
        slug = str(slug)
    norm = _normalize_agent_key(slug)
    return _dispatch_aliases().get(norm, norm)


def is_lead_agent(slug: str) -> bool:
    """True when ``slug`` is an operator lead seat (``agents.yaml`` ``lead_seats``).

    The folded bus address ``cursor`` alone is never lead — lead-ness is a
    capability-cell property (``claude-cursor``, ``gpt-cursor``, …).
    """
    if _normalize_agent_key(slug) == "cursor" and normalize_bus_address(slug) == "cursor":
        return False
    return normalize_agent_slug(slug) in load_lead_agent_slugs()


def expand_recipient_slugs(slug: str) -> list[str]:
    """All ``to_agent`` values that should match inbox fetches for this seat.

    Address-layer superset: new canonical (``web-anthropic``, ``cursor``), old
    ``{family}-{platform}`` cells, and legacy nicknames (``web``, ``cursor``).
    """
    canonical = normalize_bus_address(slug)
    values: set[str] = {canonical}
    raw = slug.strip()
    if raw:
        values.add(raw)
    values.add(canonical.replace("-", "_"))
    for alias_norm, target in _bus_address_aliases().items():
        if target != canonical:
            continue
        values.add(alias_norm)
        values.add(alias_norm.replace("_", "-"))
    for cell, addr in _bus_address_map().items():
        if addr != canonical:
            continue
        values.add(cell)
        values.add(cell.replace("-", "_"))
    return sorted(values)


def resolve_agent_provider(role_or_seat: str) -> str | None:
    """Return the default provider for a role or expected provider for a seat.

    role slugs → look up their default assignment via load_roles, then
    load_profiles by (family, platform). This is a default, not a provider lock:
    explicit model overrides may fill any functional role. Seat slugs
    ({family}-{platform}) remain concrete provider-bound dispatch targets.
    Normalizes case/variants first.
    """
    canonical = normalize_agent_slug(role_or_seat)
    role = load_roles().get(canonical)
    if role is not None:
        return get_profile(role.default_family, role.default_platform).provider

    family = seat_to_family(canonical)
    if family is not None:
        parts = canonical.split("-", 1)
        if len(parts) == 2:
            try:
                return get_profile(family, parts[1]).provider
            except KeyError:
                pass
    return None


def resolve_agent_model(role_or_seat: str) -> str:
    """Return the default model for a role or seat slug.

    Raises ValueError for unknown slugs (including web-* non-dispatch seats).
    Normalizes case/variants first.
    """
    canonical = normalize_agent_slug(role_or_seat)
    role = load_roles().get(canonical)
    if role is not None and role.default_model:
        return role.default_model

    family = seat_to_family(canonical)
    if family is not None:
        parts = canonical.split("-", 1)
        if len(parts) == 2:
            try:
                profile = get_profile(family, parts[1])
                if profile.default_model:
                    return profile.default_model
            except KeyError:
                pass

    raise ValueError(
        f"Unknown dispatch role or seat {role_or_seat!r} (normalized: {canonical!r}). "
        f"Note: web seats are not dispatch targets."
    )


def resolve_agent_valid_family(role_or_seat: str) -> list[str]:
    """Return the allowed model list for a role or seat, or [] if unknown.

    Normalizes case/variants first.
    """
    canonical = normalize_agent_slug(role_or_seat)
    role = load_roles().get(canonical)
    if role is not None:
        allowed = (
            role.allowed_models
            or get_profile(role.default_family, role.default_platform).allowed_models
        )
        return list(allowed)

    family = seat_to_family(canonical)
    if family is not None:
        parts = canonical.split("-", 1)
        if len(parts) == 2:
            try:
                return list(get_profile(family, parts[1]).allowed_models)
            except KeyError:
                pass
    return []


def resolve_agent_model_requirement(role_or_seat: str) -> str | None:
    """Return the default seat's variant substring for a role or concrete seat.

    For roles this is descriptive of the default assignment only; admission
    enforcement skips functional role slugs because any model may assume any
    role. Concrete seats still enforce this requirement.
    """
    canonical = normalize_agent_slug(role_or_seat)
    role = load_roles().get(canonical)
    if role is not None:
        return get_profile(role.default_family, role.default_platform).model_requirement

    family = seat_to_family(canonical)
    if family is not None:
        parts = canonical.split("-", 1)
        if len(parts) == 2:
            try:
                return get_profile(family, parts[1]).model_requirement
            except KeyError:
                pass
    return None


def check_agent_model_requirement(role_or_seat: str, model: str) -> str | None:
    """Return a violation description if model fails a seat's variant
    requirement, or None if satisfied (or no requirement exists).

    This helper still resolves role defaults for diagnostics, but admission
    enforcement skips functional roles. Concrete seats with
    ``model_requirement`` (currently ``grok-api-multi``) must contain the
    required substring because non-multi-agent xAI models have different
    client-side tool behavior.

    Normalizes slug first.
    """
    canonical = normalize_agent_slug(role_or_seat)
    required = resolve_agent_model_requirement(canonical)
    if required is None:
        return None
    if required in model:
        return None
    return (
        f"Role/seat {role_or_seat!r} (canonical: {canonical}) requires a model "
        f"containing {required!r}; got {model!r}. "
        f"Non-multi-agent xAI models reject client-side tools at the API level."
    )


def check_agent_model_consistency(role_or_seat: str, model: str) -> str | None:
    """Return a violation description if model's provider mismatches the expected
    provider for the role/seat, or None if consistent (or unknown).

    Resolves the provider via the CapabilityProfile registry and compares it
    against the model ID prefix. Returns None for unknown slugs or providers.
    """
    expected_provider = resolve_agent_provider(role_or_seat)
    if expected_provider is None:
        return None
    model_prefix = model.split("/")[0] if "/" in model else ""
    if model_prefix == "cdp":
        # CDP is its own substrate (web-anthropic-cdp); never anthropic-alias.
        return None
    provider_map = {
        "anthropic": "anthropic",
        "openai": "openai",
        "xai": "xai",
        "google": "google",
    }
    model_provider = provider_map.get(model_prefix)
    if model_provider is None:
        return None
    if model_provider != expected_provider:
        return (
            f"Role/seat {role_or_seat!r} expects provider {expected_provider!r}; "
            f"model {model!r} is from provider {model_provider!r}."
        )
    return None
