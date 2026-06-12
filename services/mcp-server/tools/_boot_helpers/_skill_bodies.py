"""Agent skill path helpers for boot briefing (index only — bodies via fs md_*)."""

from __future__ import annotations

from typing import Any

_SKILL_PREFIX = "agent-skills/"


def skill_slug(skill: dict[str, Any]) -> str:
    """Return the on-disk filesystem slug for a boot-skills row.

    The slug is the basename of ``agent-skills/<slug>.md`` and is the canonical
    reference form agents must type in skill-refs (handoff packets, ``md_read``
    hints, etc.). It derives from the entity id (``agent_skill:<slug>``), NOT
    the display ``name``: the name carries spaces / em-dashes and does not
    resolve on disk. Returning the name here produced non-resolving boot-card
    ``md_read`` hints and trained agents to write display-name skill-refs that
    404 on read and fail the handoff arch-skillref validator (friction 16958).
    """
    entity_id = skill.get("id") or skill.get("entity_id")
    if isinstance(entity_id, str) and entity_id.strip():
        slug = entity_id.strip().removeprefix("agent_skill:")
        if slug:
            return slug
    name = skill.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "?"


def skill_relpath(skill: dict[str, Any]) -> str:
    return f"{_SKILL_PREFIX}{skill_slug(skill)}.md"
