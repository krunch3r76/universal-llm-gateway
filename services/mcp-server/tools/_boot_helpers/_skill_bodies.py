"""Agent skill path helpers for boot briefing (index only — bodies via fs md_*)."""

from __future__ import annotations

from typing import Any

from agent_seat.guidance_entity import entity_slug_from_id
from implement_admission.skill_catalog_resolver import (
    SkillCatalogResolveError,
    resolve_canonical_source_uri,
)


def skill_slug(skill: dict[str, Any]) -> str:
    """Return the on-disk filesystem slug for a boot-view skill/rule row."""
    entity_id = skill.get("id") or skill.get("entity_id")
    if isinstance(entity_id, str) and entity_id.strip():
        slug = entity_slug_from_id(entity_id.strip())
        if slug:
            return slug
    name = skill.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "?"


def _source_uri_for_skill(skill: dict[str, Any]) -> str:
    raw = skill.get("source_uri")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    slug = skill_slug(skill)
    return resolve_canonical_source_uri(slug)


def skill_relpath(skill: dict[str, Any]) -> str:
    """Resolve boot-card skill pointer via entity ``source_uri`` (D1), not phantom paths."""
    uri = _source_uri_for_skill(skill)
    if uri.startswith("workspaces://"):
        rel = uri.split("universal-llm-gateway/", 1)[-1]
        return rel
    if uri.startswith(".cursor/skills/"):
        return f"universal-llm-gateway/{uri}"
    raise SkillCatalogResolveError(f"unsupported boot source_uri: {uri!r}")
