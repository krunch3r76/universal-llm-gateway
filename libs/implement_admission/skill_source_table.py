"""Build-time generated canonical skill slug → source_uri table (D1).

Hot paths (packet render, boot, materialize) read this table only — never live
``entity_get``. Generation-time validation and ``skill_table_freshness`` compare
against live Cortex entities (F1).
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

from cortex_store.guidance_entity import entity_slug_from_id

TEMPLATE_VERSION: Final[str] = "1"

# slug synonyms → canonical table key (``entity_slug_from_id`` output may differ)
CANONICAL_SLUG_ALIASES: Final[dict[str, str]] = {
    "session-close-kernel": "session-close",
    "ulg-architecture": "ulg-architecture_ulg",
}

# Generated from live Cortex entities — prefer substantiated ``rule:`` ``source_uri``.
CANONICAL_SKILL_SOURCE_URIS: Final[dict[str, str]] = {
    "architecture-invariants": (
        "workspaces://universal-llm-gateway/docs/agent-guides/skills/architecture-invariants.md"
    ),
    "completion-provenance-discipline": "agent-skills/completion-provenance-discipline.md",
    "consult-routing": "agent-skills/consult-routing.md",
    "cortex-orientation": "agent-skills/cortex-orientation.md",
    "cortex-provenance-discipline": "agent-skills/cortex-provenance-discipline.md",
    "dispatch-shape": "agent-skills/dispatch-shape.md",
    "entity-lifecycle-discipline": "agent-skills/entity-lifecycle-discipline.md",
    "frontier-reasoning-discipline": "agent-skills/frontier-reasoning-discipline.md",
    "fs": "agent-skills/fs.md",
    "git-posture": (
        "workspaces://universal-llm-gateway/docs/agent-guides/skills/git-posture.md"
    ),
    "implement-todo": "agent-skills/implement-todo.md",
    "implement-work-item": (
        "workspaces://universal-llm-gateway/.cursor/skills/implement-work-item/SKILL.md"
    ),
    "lead-seat-boot": (
        "workspaces://universal-llm-gateway/.cursor/skills/lead-seat-boot/SKILL.md"
    ),
    "mcp-surface-change": "agent-skills/mcp-surface-change.md",
    "model-tier-awareness-web": "agent-skills/model-tier-awareness-web.md",
    "operator-posture": "agent-skills/operator-posture.md",
    "orchestrator-core": "agent-skills/orchestrator-core.md",
    "orchestrator-workflow": "agent-skills/orchestrator-workflow.md",
    "prose-discipline": "agent-skills/prose-discipline.md",
    "service-lifecycle": "agent-skills/service-lifecycle.md",
    "session-close": "agent-skills/session-close-kernel.md",
    "session-close-audit": "agent-skills/session-close-audit.md",
    "ulg-architecture_ulg": (
        "workspaces://universal-llm-gateway/docs/agent-guides/skills/ulg-architecture.md"
    ),
    "web-transcript-preprocessing": (
        "workspaces://universal-llm-gateway/.cursor/skills/web-transcript-preprocessing/SKILL.md"
    ),
    "agent-identity-signoff": (
        "workspaces://universal-llm-gateway/agent-surface/sources/agent-identity-signoff.md"
    ),
}

TABLE_DIGEST: Final[str] = (
    "sha256:"
    + hashlib.sha256(
        json.dumps(CANONICAL_SKILL_SOURCE_URIS, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
)


class SkillSourceResolveError(LookupError):
    """Canonical slug absent from the committed resolver table."""


def canonical_table_key(slug_or_entity_id: str) -> str:
    """Normalize any entity id or bare slug to a canonical table key."""
    raw = slug_or_entity_id.strip()
    slug = entity_slug_from_id(raw) if ":" in raw else raw
    return CANONICAL_SLUG_ALIASES.get(slug, slug)


def canonical_agent_skill_id(slug_or_entity_id: str) -> str:
    """Double-load exclusion key — always ``agent_skill:{canonical_slug}``."""
    return f"agent_skill:{canonical_table_key(slug_or_entity_id)}"


def resolve_canonical_source_uri(slug_or_entity_id: str) -> str:
    """Map slug/entity id → ``source_uri`` via the committed table (D1)."""
    key = canonical_table_key(slug_or_entity_id)
    uri = CANONICAL_SKILL_SOURCE_URIS.get(key)
    if not uri:
        raise SkillSourceResolveError(
            f"canonical slug {key!r} absent from skill source table "
            f"(template_version={TEMPLATE_VERSION})"
        )
    return uri


def table_bytes_for_digest() -> bytes:
    """Stable serialization for determinism / freshness probes."""
    return json.dumps(CANONICAL_SKILL_SOURCE_URIS, sort_keys=True, separators=(",", ":")).encode()
