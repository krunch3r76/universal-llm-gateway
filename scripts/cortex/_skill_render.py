"""Canonical deterministic stub renderer for cortex agent_skill projections."""

from __future__ import annotations

import json
from typing import Any

from _skill_constants import (
    GENERATED_HEADER,
    GENERATOR_VERSION,
    normalize_slug,
    slug_to_name,
)


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_list(values: list[str]) -> str:
    inner = ", ".join(json.dumps(v) for v in values)
    return f"[{inner}]"


def extract_renderer_fields(entity: dict[str, Any], slug: str) -> dict[str, Any]:
    """Project only renderer-input fields from a live entity row."""
    attrs = entity.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    outgoing_refs = _outgoing_reference_slugs(entity, slug)
    aliases_raw = entity.get("aliases")
    aliases: list[str] = []
    if isinstance(aliases_raw, list):
        aliases = sorted(str(v) for v in aliases_raw)
    elif isinstance(aliases_raw, str) and aliases_raw.strip():
        aliases = sorted(str(v) for v in aliases_raw.split(","))
    related = attrs.get("related_skills")
    related_skills = (
        sorted(str(v).removeprefix("agent_skill:") for v in related)
        if isinstance(related, list)
        else []
    )
    terms = attrs.get("trigger_match_terms")
    trigger_match_terms = (
        sorted(str(v) for v in terms) if isinstance(terms, list) else []
    )
    pointer = attrs.get("paired_rule_pointer")
    if pointer is None:
        pointer = entity.get("paired_rule_pointer")
    return {
        "description": str(entity.get("description") or "").strip(),
        "trigger_match_terms": trigger_match_terms,
        "related_skills": related_skills,
        "references": outgoing_refs,
        "aliases": aliases,
        "source_uri": str(entity.get("source_uri") or "").strip(),
        "paired_rule_pointer": str(pointer).strip() if pointer else "",
    }


def _outgoing_reference_slugs(entity: dict[str, Any], slug: str) -> list[str]:
    source_id = f"agent_skill:{normalize_slug(slug)}"
    out: list[str] = []
    for rel in entity.get("relationships") or []:
        if str(rel.get("type_id") or "") != "references":
            continue
        if str(rel.get("source_id") or "") != source_id:
            continue
        target = str(rel.get("target_id") or "")
        if not target.startswith("agent_skill:"):
            continue
        target_slug = target.removeprefix("agent_skill:")
        if target_slug and target_slug not in out:
            out.append(target_slug)
    return sorted(out)


def render_stub(slug: str, fields: dict[str, Any]) -> str:
    """Render a byte-stable ``SKILL.md`` stub from renderer-input fields only."""
    slug = normalize_slug(slug)
    lines = [
        "---",
        f"name: {slug}",
        f"description: {_yaml_quote(str(fields['description']))}",
    ]
    terms = fields.get("trigger_match_terms") or []
    if terms:
        lines.append(f"trigger_match_terms: {_yaml_list(list(terms))}")
    lines.append(f"generator_version: {_yaml_quote(GENERATOR_VERSION)}")
    lines.append("---")
    lines.append("")
    if fields.get("related_skills"):
        lines.append("## Related skills")
        lines.append("")
        for related in fields["related_skills"]:
            lines.append(f"- {related}")
        lines.append("")
    title = slug_to_name(slug)
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"<!-- {GENERATED_HEADER} -->")
    lines.append("")
    source_uri = str(fields.get("source_uri") or "")
    if source_uri:
        lines.extend([f"**Source:** `{source_uri}`", ""])
    pointer = str(fields.get("paired_rule_pointer") or "").strip()
    if pointer:
        lines.extend([f"**Rule (shared tree):** `{pointer}`", ""])
    lines.append(f"Entity: `agent_skill:{slug}`.")
    lines.append("")
    return "\n".join(lines)


def renderer_field_values(fields: dict[str, Any]) -> tuple[Any, ...]:
    """Tuple of field values in renderer-input order for hashing."""
    from _skill_constants import RENDERER_INPUT_FIELDS

    return tuple(fields.get(name) for name in RENDERER_INPUT_FIELDS)
