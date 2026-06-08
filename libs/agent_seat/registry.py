"""Agent model registry — dispatch identity resolved from CapabilityProfile.

Provides normalize_agent_slug (for frontier dispatch pipeline slug normalization)
and the resolve_agent_* / check_agent_* helpers used by admission gates.

``web`` seats are not dispatch targets — strategic-advisor seats only
(∀ dispatch with a web seat slug: caller error; see Cortex assertion 5258).
"""

from __future__ import annotations

from .profiles import get_profile, load_lead_agent_slugs, load_roles, seat_to_family

# Legacy seat spellings → canonical {family}-{platform} slug.
# Shared by dispatch normalization and agent-bus recipient matching.
_DISPATCH_ALIASES: dict[str, str] = {
    # Legacy seat slugs → new seat slugs
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
    "gemini_web": "gemini-web",
    # New canonical seat slugs (hyphen form stored under underscore key
    # since norm replaces hyphens with underscores above)
    "claude_cursor": "claude-cursor",
    "claude_api": "claude-api",
    "claude_web": "claude-web",
    "gpt_cursor": "gpt-cursor",
    "gpt_api": "gpt-api",
    "grok_cursor": "grok-cursor",
    "grok_api": "grok-api",
    "grok_api_multi": "grok-api-multi",
    "grok_web": "grok-web",
    "gemini_api": "gemini-api",
    "gemini_cursor": "gemini-cursor",
    # Handoff roster + API functional roles
    "web_consult": "web-consult",
    "web_implement": "web-implement",
    "cursor_consult": "cursor-consult",
    "cursor_implement": "cursor-implement",
    "reviewer": "reviewer",
    "gatherer": "gatherer",
    "synthesizer": "synthesizer",
    "artisan": "artisan",
    "skeptic": "skeptic",
    "investigator": "investigator",
}


def _normalize_agent_key(slug: str) -> str:
    return slug.strip().lower().replace("-", "_").replace(" ", "_")


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
    return _DISPATCH_ALIASES.get(norm, norm)


def is_lead_agent(slug: str) -> bool:
    """True when ``slug`` is an operator lead seat (``agents.yaml`` ``lead_seats``)."""
    return normalize_agent_slug(slug) in load_lead_agent_slugs()


def expand_recipient_slugs(slug: str) -> list[str]:
    """All ``to_agent`` values that should match inbox fetches for this seat.

    Historical turns store legacy short slugs (``web``, ``cursor``) while
    seats query with canonical ``claude-web`` / ``claude-cursor``. Agent-bus
    recipient filters must match every alias that normalizes to the same seat.
    """
    canonical = normalize_agent_slug(slug)
    values: set[str] = {canonical}
    raw = slug.strip()
    if raw:
        values.add(raw)
    values.add(canonical.replace("-", "_"))
    for alias_norm, target in _DISPATCH_ALIASES.items():
        if target != canonical:
            continue
        values.add(alias_norm)
        values.add(alias_norm.replace("_", "-"))
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
