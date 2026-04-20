"""Agent model registry — static dispatch identity for team seats.

Maps agent slugs to their canonical provider family and default model.
``web`` is intentionally absent — it is a strategic-advisor seat, not a
dispatch target (∀ dispatch with ``agent='web'``: caller error, not a
registry gap; documented on Cortex assertion 5258).

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
    "oppie": "xai/grok-4.20-0309-reasoning",
    "orion": "openai/gpt-5.4",
    "bard": "google/gemini-2.5-pro",
    "api_claude": "anthropic/claude-sonnet-4-6",
}

# Allowed model families per agent (for mismatch event payload).
_AGENT_VALID_FAMILIES: dict[str, list[str]] = {
    "oppie": ["xai/grok-4.20-0309-reasoning", "xai/grok-4-fast-reasoning"],
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
        "anthropic/claude-3-5-sonnet",
    ],
}


def resolve_agent_model(agent: str) -> str:
    """Return the default model for a dispatch agent slug.

    Raises ``ValueError`` for unknown slugs (including ``'web'``).
    """
    model = _AGENT_DEFAULTS.get(agent)
    if model is None:
        valid = sorted(_AGENT_DEFAULTS)
        raise ValueError(
            f"Unknown dispatch agent {agent!r}. Valid: {valid}. "
            "Note: 'web' is not a dispatch target."
        )
    return model


def resolve_agent_provider(agent: str) -> str | None:
    """Return the expected provider for an agent slug, or ``None`` if unknown."""
    return _AGENT_PROVIDERS.get(agent)


def resolve_agent_valid_family(agent: str) -> list[str]:
    """Return the allowed model list for an agent, or ``[]`` if unknown."""
    return _AGENT_VALID_FAMILIES.get(agent, [])
