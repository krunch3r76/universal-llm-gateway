"""Role-slug registry metadata — consumed by ``scripts/cortex/sync-roles.py``.

Phase 5 of the agent-naming cleanup arc reframed this registry: it now
supplies the input data used to generate ``role:{slug}`` Cortex entities
(execution contracts), not ``ai_agent:{slug}`` entities (persona+contract
conflated). The module name preserves its historical import path to limit
blast radius; the *semantic* role of each entry is "role contract source",
not "agent identity source".

Allowed-model lists for dispatch-target slugs (orion, oppie, bard,
api-claude, forge) are imported from ``agent_seat.registry`` so the runtime
admission gate and the Cortex role sync remain a single source of truth.
Non-dispatch slugs (web-claude, cursor-claude, cursor_orion, web-grok) keep
empty allowed_models lists since they have no registry dispatch entry.

Tools allowlist retired per todo:retire-tools-allowlist-as-caller-concern —
no longer present in role contract; tool surface is universal
(provider-derived silent coercion for xAI multi-agent quirks).

``persona_seed_ref`` is retained as an opaque URI field per the Phase 5
role schema, but post-Phase-6 the birth-prompt source files are retired —
new sync runs write ``persona_seed_ref: None`` for slugs whose birth prompt
was in ``agent-identity/`` (now in cortex ``trash/`` from the 2026-05-11
retirement sweep). Slugs pointing at non-birth-prompt seed files
(e.g. oppie → ``notes/system/prompts/...``) keep their reference.
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
        "persona_seed_ref": None,
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
        "default_model": "google/gemini-2.5-flash",
        "allowed_models": resolve_agent_valid_family("bard"),
        "allowed_options": None,
        "persona_seed_ref": None,
    },
    "web-claude": {
        "name": "Web Claude",
        "provider": "Anthropic",
        "frontier_kind": "anthropic",
        # Web Claude is not API-reachable — no default_model / allowed_models.
        "default_model": None,
        "allowed_models": [],
        "allowed_options": None,
        "persona_seed_ref": None,
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
        "persona_seed_ref": None,
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
        "persona_seed_ref": None,
    },
    # Forge is a Grok-family agent. Dual surface:
    # (1) off-IDE peer consult via team_dispatch(op="generate", agent="forge"), and
    # (2) IDE-resident when the active Cursor engine is Grok 4.20 or Grok 4.3.
    "forge": {
        "name": "Forge",
        "provider": "xAI",
        "frontier_kind": "xai",
        "default_model": "xai/grok-4.20-0309-reasoning",
        "allowed_models": resolve_agent_valid_family("forge"),
        "allowed_options": None,
        "persona_seed_ref": None,
    },
    # web-grok is the routing slot for Grok on grok.com (auto / 4.3 /
    # Expert — anything besides SuperHeavy). Not API-reachable. The boot
    # seed adds cortex routing and tool topology conventions.
    "web-grok": {
        "name": "web-grok",
        "provider": "xAI",
        "frontier_kind": "xai",
        "default_model": None,
        "allowed_models": [],
        "allowed_options": None,
        "persona_seed_ref": None,
    },
}
