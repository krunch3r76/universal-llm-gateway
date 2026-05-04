"""Agent identity registry metadata — synced to Cortex by sync-agent-identity.py.

Allowed-model lists for dispatch-target agents (orion, oppie, bard, api-claude)
are imported from ``agent_seat.registry`` so the runtime admission gate and the
Cortex sync remain the single source of truth.  Non-dispatch agents (web-claude,
cursor-claude) keep their lists inline since they have no registry entry.

Tools allowlist retired per todo:retire-tools-allowlist-as-caller-concern — no
longer present in persona contract; tool surface is universal (provider-derived
silent coercion for quirks like xAI multi-agent).
"""

from agent_seat.registry import resolve_agent_valid_family

AGENT_REGISTRY: dict[str, dict[str, object]] = {
    "orion": {
        "name": "Orion",
        "provider": "OpenAI",
        "frontier_kind": "openai",
        "default_model": "openai/gpt-5.4",
        "allowed_models": resolve_agent_valid_family("orion"),
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/orion-birth.md",
    },
    "oppie": {
        "name": "Oppie",
        "provider": "xAI",
        "frontier_kind": "xai",
        "default_model": "xai/grok-4.20-multi-agent-0309",
        "allowed_models": resolve_agent_valid_family("oppie"),
        "allowed_options": None,
        "persona_seed_ref": "notes/system/prompts/oppie-seed-mcp-v1.5.md",
    },
    "bard": {
        "name": "Bard",
        "provider": "Google",
        "frontier_kind": "google",
        "default_model": "google/gemini-2.5-pro",
        "allowed_models": resolve_agent_valid_family("bard"),
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
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/cursor-claude-birth.md",
    },
    # cursor_grok is the Cursor-resident worker (xAI Grok family). Not a persona.
    # default_model is notional — Cursor selects the active model from its own UI.
    "cursor-grok": {
        "name": "cursor_grok",
        "provider": "xAI",
        "frontier_kind": "xai",
        "default_model": "xai/grok-4.20-multi-agent-0309",
        "allowed_models": [
            "xai/grok-4.20-multi-agent-0309",
        ],
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/cursor-grok-birth.md",
    },
    # Forge is a Grok-family trAId persona. Dual surface:
    # (1) off-IDE peer consult via team_dispatch(op="generate", agent="forge"), and
    # (2) IDE-resident persona when the active Cursor engine is Grok 4.20 or
    #     Grok 4.3 — sign-off is "(Cursor) Forge".
    "forge": {
        "name": "Forge",
        "provider": "xAI",
        "frontier_kind": "xai",
        "default_model": "xai/grok-4.20-0309-reasoning",
        "allowed_models": resolve_agent_valid_family("forge"),
        "allowed_options": None,
        "persona_seed_ref": "agent-identity/forge-birth.md",
    },
}
