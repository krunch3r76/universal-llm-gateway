"""Agent skill path helpers for boot briefing (index only — bodies via fs md_*)."""

from __future__ import annotations

from typing import Any

_SKILL_PREFIX = "agent-skills/"


def skill_slug(skill: dict[str, Any]) -> str:
    """Return the filesystem slug for a boot-skills row."""
    name = skill.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    entity_id = str(skill.get("id") or skill.get("entity_id") or "?")
    return entity_id.removeprefix("agent_skill:")


def skill_relpath(skill: dict[str, Any]) -> str:
    return f"{_SKILL_PREFIX}{skill_slug(skill)}.md"
