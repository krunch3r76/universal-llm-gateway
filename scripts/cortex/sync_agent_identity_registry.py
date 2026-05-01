"""Agent identity registry metadata — synced to Cortex by sync-agent-identity.py.

Allowed-model lists for dispatch-target agents (orion, oppie, bard, api-claude)
are imported from ``agent_seat.registry`` so the runtime admission gate and the
Cortex sync remain the single source of truth.  Non-dispatch agents (web-claude,
cursor-claude) keep their lists inline since they have no registry entry.
"""

from agent_seat.registry import resolve_agent_valid_family

AGENT_REGISTRY: dict[str, dict[str, object]] = {
    "orion": {
        "name": "Orion",
        "provider": "OpenAI",
        "frontier_kind": "openai",
        "default_model": "openai/gpt-5.4",
        "allowed_models": resolve_agent_valid_family("orion"),
        "tools": None,
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/orion-birth.md",
    },
    "oppie": {
        "name": "Oppie",
        "provider": "xAI",
        "frontier_kind": "xai",
        "default_model": "xai/grok-4.20-multi-agent-0309",
        "allowed_models": resolve_agent_valid_family("oppie"),
        "tools": None,
        "allowed_options": None,
        "persona_seed_ref": "notes/system/prompts/oppie-seed-mcp-v1.5.md",
    },
    "bard": {
        "name": "Bard",
        "provider": "Google",
        "frontier_kind": "google",
        "default_model": "google/gemini-2.5-pro",
        "allowed_models": resolve_agent_valid_family("bard"),
        "tools": None,
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/bard-birth.md",
    },
    "web-claude": {
        "name": "Web Claude",
        "provider": "Anthropic",
        "frontier_kind": "anthropic",
        # Web Claude is not API-reachable — no default_model / allowed_models.
        "default_model": None,
        "allowed_models": [],
        "tools": None,
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/web-claude-birth.md",
    },
    # Cortex uses hyphen-form slug ("api-claude"); the dispatch registry
    # uses underscore-form ("api_claude"). The sync script uses hyphen-form
    # to match the Cortex entity ID convention.
    "api-claude": {
        "name": "API Claude",
        "provider": "Anthropic",
        "frontier_kind": "anthropic",
        "default_model": "anthropic/claude-sonnet-4-6",
        "allowed_models": resolve_agent_valid_family("api_claude"),
        "tools": None,
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/api-claude-birth.md",
    },
    "cursor-claude": {
        "name": "Cursor Claude",
        "provider": "Anthropic",
        "frontier_kind": "anthropic",
        "default_model": "anthropic/claude-sonnet-4-6",
        "allowed_models": [
            "anthropic/claude-sonnet-4-6",
            "anthropic/claude-opus-4-7",
        ],
        "tools": None,
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/cursor-claude-birth.md",
    },
}
