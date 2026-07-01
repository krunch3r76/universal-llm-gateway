"""Shared guidance-entity helpers — leaf module (no agent_seat imports)."""

from __future__ import annotations

GUIDANCE_ENTITY_TYPES = frozenset({"agent_skill", "rule", "skill"})
DISCOVERABLE_GATED_TYPES = frozenset({"agent_skill", "skill"})
SEAT_GATED_TYPES = frozenset({"agent_skill", "skill"})
GUIDANCE_ID_PREFIXES = ("agent_skill:", "rule:", "skill:")


def entity_slug_from_id(entity_id: str) -> str:
    """Bare slug from a typed entity id (prefix-agnostic)."""
    if ":" in entity_id:
        return entity_id.split(":", 1)[1]
    return entity_id


def strip_guidance_id_prefix(value: str) -> str:
    """Normalize a loaded slug/id token to bare slug (3-prefix set)."""
    stripped = value.strip()
    lowered = stripped.lower()
    for prefix in GUIDANCE_ID_PREFIXES:
        if lowered.startswith(prefix):
            return stripped[len(prefix) :]
    return stripped
