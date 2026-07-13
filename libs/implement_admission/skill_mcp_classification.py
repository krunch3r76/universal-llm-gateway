"""Catalog-backed MCP surface predicates (replaces binary skill_mcp_classification)."""

from __future__ import annotations

from claude_bundles.catalog import get_skill_catalog


class SkillClassificationMissingError(LookupError):
    """Canonical slug absent from the skill catalog."""


def skill_mcp_predicated(slug_or_entity_id: str) -> bool:
    """True when the skill requires any MCP surface (life or code)."""
    catalog = get_skill_catalog()
    try:
        return catalog.requires_mcp(slug_or_entity_id)
    except KeyError as exc:
        raise SkillClassificationMissingError(
            f"canonical slug absent from skill catalog: {slug_or_entity_id!r}"
        ) from exc


def skill_mcp_surface_required(slug_or_entity_id: str) -> str:
    """Return ``none`` | ``life`` | ``code`` for the slug."""
    catalog = get_skill_catalog()
    try:
        return catalog.mcp_surface_required_for(slug_or_entity_id)
    except KeyError as exc:
        raise SkillClassificationMissingError(
            f"canonical slug absent from skill catalog: {slug_or_entity_id!r}"
        ) from exc
