"""Agent model registry — dispatch identity resolved from CapabilityProfile.

Provides normalize_agent_slug (for frontier dispatch pipeline slug normalization)
and the resolve_agent_* / check_agent_* helpers used by admission gates.

``web`` seats are not dispatch targets — strategic-advisor seats only
(∀ dispatch with a web seat slug: caller error; see Cortex assertion 5258).
"""

from __future__ import annotations

from .profiles import get_profile, load_roles


def normalize_agent_slug(slug: str) -> str:
    """Normalize dispatch agent slug to canonical seat or role slug.

    Used by the frontier dispatch pipeline to handle case/spelling variants
    in team_dispatch(role=...) and frontier_dispatch(agent=...) calls.

    Handles:
    - Case variations (Oppie → skeptic)
    - Common misspellings from model output (Oppia → skeptic)
    - Hyphen/underscore/space variants → canonical {family}-{platform} form
    - Legacy persona names → new role slugs
    - Legacy seat slugs → new {family}-{platform} seat slugs

    Returns a canonical seat slug ({family}-{platform}) or role slug.
    """
    if not isinstance(slug, str):
        slug = str(slug)
    norm = slug.strip().lower().replace("-", "_").replace(" ", "_")
    aliases: dict[str, str] = {
        # Legacy persona slugs → role slugs
        "oppie": "skeptic",
        "oppia": "skeptic",  # common misspelling
        "forge": "artisan",
        "cursor_forge": "artisan",
        "orion": "gatherer",
        "bard": "synthesizer",
        # Legacy seat slugs → new seat slugs
        "cursor": "claude-cursor",
        "cursor_claude": "claude-cursor",
        "web": "claude-web",
        "web_claude": "claude-web",
        "api": "claude-api",
        "api_claude": "claude-api",
        "cursor_orion": "gpt-cursor",
        "cursor_grok": "grok-cursor",
        "grok": "grok-cursor",
        "web_grok": "grok-web",
        "superheavy": "grok-web",
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
        # Role slugs pass through unchanged
        "lead": "lead",
        "reviewer": "reviewer",
        "gatherer": "gatherer",
        "synthesizer": "synthesizer",
        "artisan": "artisan",
        "skeptic": "skeptic",
        "investigator": "investigator",
    }
    return aliases.get(norm, norm)


def resolve_agent_provider(role_or_seat: str) -> str | None:
    """Return the expected provider for a role or seat slug, or None if unknown.

    role slugs → look up via load_roles, then load_profiles by (family, platform).
    seat slugs ({family}-{platform}) → look up directly via load_profiles.
    Normalizes case/variants first.
    """
    canonical = normalize_agent_slug(role_or_seat)
    role = load_roles().get(canonical)
    if role is not None:
        return get_profile(role.default_family, role.default_platform).provider

    parts = canonical.split("-", 1)
    if len(parts) == 2 and parts[0] in {"claude", "gpt", "grok", "gemini"}:
        try:
            return get_profile(parts[0], parts[1]).provider
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

    parts = canonical.split("-", 1)
    if len(parts) == 2:
        try:
            profile = get_profile(parts[0], parts[1])
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

    parts = canonical.split("-", 1)
    if len(parts) == 2:
        try:
            return list(get_profile(parts[0], parts[1]).allowed_models)
        except KeyError:
            pass
    return []


def resolve_agent_model_requirement(role_or_seat: str) -> str | None:
    """Return the required model-variant substring for a role or seat, or None.

    Normalizes case/variants first.
    """
    canonical = normalize_agent_slug(role_or_seat)
    role = load_roles().get(canonical)
    if role is not None:
        return get_profile(role.default_family, role.default_platform).model_requirement

    parts = canonical.split("-", 1)
    if len(parts) == 2:
        try:
            return get_profile(parts[0], parts[1]).model_requirement
        except KeyError:
            pass
    return None


def check_agent_model_requirement(role_or_seat: str, model: str) -> str | None:
    """Return a violation description if model fails the role/seat's variant
    requirement, or None if satisfied (or no requirement exists).

    ∀ role/seat ∈ profiles with model_requirement: model MUST contain the
    required substring. skeptic (grok-api-multi) specifically MUST use an
    xAI multi-agent variant — non-multi-agent xAI models reject client-side
    tools at the API level.

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
