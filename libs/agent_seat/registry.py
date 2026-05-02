"""Agent model registry — static dispatch identity for team seats.

Provides ``normalize_agent_slug()`` (handles case, "Oppie"/"Oppia"→"oppie",
hyphen/underscore variants) + mapping dicts for provider/defaults/valid families.

``web`` is intentionally absent from dispatch registry — it is a
strategic-advisor seat, not a dispatch target (∀ dispatch with
``agent='web'``: caller error; see Cortex assertion 5258).

Used by the pipeline handler for:
- pre-hydration admission check (agent + model provider consistency)
- default model resolution when caller omits ``model`` with ``agent`` set
"""

from __future__ import annotations

# Expected provider per agent (admission gate: model provider MUST match).
_AGENT_PROVIDERS: dict[str, str] = {
    "oppie": "xai",
    "orion": "openai",
    "bard": "google",
    "api_claude": "anthropic",
}

# Default model per agent (canonical, mirrors Cortex assertions 5255–5258).
_AGENT_DEFAULTS: dict[str, str] = {
    "oppie": "xai/grok-4.20-multi-agent-0309",
    "orion": "openai/gpt-5.4",
    "bard": "google/gemini-2.5-pro",
    "api_claude": "anthropic/claude-sonnet-4-6",
}

# Allowed model families per agent (for mismatch event payload).
# ∀ entry: exact snapshots — update when new model variants are approved for an agent.
_AGENT_VALID_FAMILIES: dict[str, list[str]] = {
    "oppie": [
        "xai/grok-4.20-multi-agent-0309"
    ],  # exact snapshot; update when multi-agent variants expand
    "orion": ["openai/gpt-5.4", "openai/gpt-5.4-mini", "openai/o4-mini", "openai/o3"],
    "bard": [
        "google/gemini-2.5-pro",
        "google/gemini-2.5-flash",
        "google/gemini-3-flash-preview",
        "google/gemini-3.1-pro-preview",
    ],
    "api_claude": [
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-4",
        "anthropic/claude-opus-4-7",
        "anthropic/claude-3-5-sonnet",
    ],
}

# ∀ agent with a model-variant requirement: the model ID MUST contain this
# substring. Checked AFTER provider consistency — a provider mismatch is
# reported first. Oppie MUST use an xAI multi-agent model; non-multi-agent
# xAI models reject client-side tools with a hard API error.
_AGENT_MODEL_REQUIREMENTS: dict[str, str] = {
    "oppie": "multi-agent",
}


def normalize_agent_slug(slug: str) -> str:
    """Normalize dispatch agent slug to canonical lowercase form.

    Handles:
    - Case variations (Oppie → oppie)
    - Common misspellings from persona prompts / model output (Oppia → oppie)
    - Hyphen/underscore/ space variants (api-claude, web claude → canonical key)
    - Returns key that matches _AGENT_* dicts and _SELF_ENTITY in hydration.

    This ensures team_generate, hydrate_agent, load_birth_prompt etc. accept
    natural references from birth prompts (e.g. web claude's "Oppie").
    """
    if not isinstance(slug, str):
        slug = str(slug)
    norm = slug.strip().lower().replace("-", "_").replace(" ", "_")
    aliases: dict[str, str] = {
        "oppie": "oppie",
        "oppia": "oppie",  # common misspelling
        "orion": "orion",
        "bard": "bard",
        "api_claude": "api_claude",
        "web": "web",
        "web_claude": "web",
        "cursor": "cursor",
        "cursor_claude": "cursor",
        "cursor_grok": "cursor_grok",
        "grok": "cursor_grok",
    }
    return aliases.get(norm, norm)


def resolve_agent_model(agent: str) -> str:
    """Return the default model for a dispatch agent slug.

    Raises ``ValueError`` for unknown slugs (including ``'web'``).
    Normalizes case/variants first (Oppie, Oppia → oppie).
    """
    canonical = normalize_agent_slug(agent)
    model = _AGENT_DEFAULTS.get(canonical)
    if model is None:
        valid = sorted(_AGENT_DEFAULTS)
        raise ValueError(
            f"Unknown dispatch agent {agent!r} (normalized: {canonical!r}). "
            f"Valid: {valid}. Note: 'web' is not a dispatch target."
        )
    return model


def resolve_agent_provider(agent: str) -> str | None:
    """Return the expected provider for an agent slug, or ``None`` if unknown.

    Normalizes case/variants first (Oppie → oppie).
    """
    canonical = normalize_agent_slug(agent)
    return _AGENT_PROVIDERS.get(canonical)


def resolve_agent_valid_family(agent: str) -> list[str]:
    """Return the allowed model list for an agent, or ``[]`` if unknown.

    Normalizes case/variants first (Oppie → oppie).
    """
    canonical = normalize_agent_slug(agent)
    return _AGENT_VALID_FAMILIES.get(canonical, [])


def resolve_agent_model_requirement(agent: str) -> str | None:
    """Return the required model-variant substring for an agent, or ``None``.

    Normalizes case/variants first (Oppie → oppie).
    """
    canonical = normalize_agent_slug(agent)
    return _AGENT_MODEL_REQUIREMENTS.get(canonical)


def check_agent_model_requirement(agent: str, model: str) -> str | None:
    """Return a violation description if ``model`` fails the agent's variant
    requirement, or ``None`` if satisfied (or no requirement exists).

    ∀ agent ∈ _AGENT_MODEL_REQUIREMENTS: model MUST contain the required
    substring. Oppie specifically MUST use an xAI multi-agent variant.

    Normalizes slug first (Oppie/Oppia → oppie).
    """
    canonical = normalize_agent_slug(agent)
    required = _AGENT_MODEL_REQUIREMENTS.get(canonical)
    if required is None:
        return None
    if required in model:
        return None
    return (
        f"Agent {agent!r} (canonical: {canonical}) requires a model containing "
        f"{required!r}; got {model!r}. Non-multi-agent xAI models reject "
        "client-side tools at the API level."
    )
