"""Shared skill/rule INDEX envelope helpers (source_uri + body digest)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException

from ..confidence_field import (
    DISCOVERABLE_SKILL_LIFECYCLE,
    discoverable_skill_lifecycle_sql_predicate,
)
from ..db import query as db_query
from ..guidance_entity import strip_guidance_id_prefix
from .boot._skill_trigger import skill_trigger_text

_DISCOVERABLE_SKILL_LIFECYCLE = discoverable_skill_lifecycle_sql_predicate()

LAYER_ENTITY_TYPES: dict[str, tuple[str, ...]] = {
    "skills": ("agent_skill", "skill"),
    "rules": ("rule",),
    "all": ("agent_skill", "rule", "skill"),
}

BOOT_SKILLS_SQL = f"""
    SELECT id, name, description, source_uri,
           json_extract(attributes, '$.skill_binding') AS skill_binding_json,
           json_extract(attributes, '$.trigger_short') AS trigger_short,
           json_extract(attributes, '$.skill_category') AS skill_category,
           json_extract(attributes, '$.trigger_match_terms') AS trigger_match_terms_json,
           json_extract(attributes, '$.boot_importance') AS boot_importance,
           json_extract(attributes, '$.related_skills') AS related_skills_json
    FROM entities
    WHERE type IN ({{type_placeholders}})
      AND (type NOT IN ('agent_skill', 'skill') OR {_DISCOVERABLE_SKILL_LIFECYCLE})
      {{for_agent_filter}}{{capability_filter}}
    ORDER BY name ASC
    LIMIT ?
"""

UNPARTITIONED_COUNT_SQL = f"""
    SELECT COUNT(*) AS n
    FROM entities
    WHERE type IN ('agent_skill', 'skill')
      AND {_DISCOVERABLE_SKILL_LIFECYCLE}
      AND json_extract(attributes, '$.applicable_agents') IS NULL
"""


def entity_types_for_layer(layer: str) -> tuple[str, ...]:
    """Map a discovery layer to the fixed entity-type allowlist (422 on unknown)."""
    types = LAYER_ENTITY_TYPES.get(layer)
    if types is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown layer {layer!r}; expected one of "
                f"{sorted(LAYER_ENTITY_TYPES)}."
            ),
        )
    return types


def slug_from_row(row: dict[str, Any]) -> str:
    """Resolve manifest slug from an entity row."""
    name = str(row.get("name") or "").strip()
    if name:
        return name
    entity_id = str(row.get("id") or "")
    if ":" in entity_id:
        return entity_id.split(":", 1)[1]
    return entity_id


def content_digest(data: bytes) -> str:
    """SHA-256 digest prefix shared by route body resolution and ingest projection."""
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def body_digest(source_uri: str | None, slug: str) -> str | None:
    """Content digest of the resolved skill/rule body for the INDEX envelope."""
    from .boot._skill_trigger import _resolve_skill_file

    path = _resolve_skill_file(source_uri, slug)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return content_digest(data)


def index_envelope_fields(row: dict[str, Any]) -> dict[str, str | None]:
    """Return ``source_uri`` and ``digest`` for a skill/rule manifest/boot row."""
    slug = slug_from_row(row)
    source_uri = row.get("source_uri")
    return {
        "source_uri": source_uri,
        "digest": body_digest(source_uri, slug),
    }


def _parse_skill_binding(
    raw: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    if not raw:
        return None, None
    try:
        binding = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(binding, dict):
        return None, None
    skill_class = binding.get("skill_class")
    tool_binding = binding.get("tool_binding") if skill_class == "tool_manual" else None
    return skill_class, tool_binding


def _derive_binding_kind(
    skill_class: str | None,
    tool_binding: dict[str, Any] | None,
) -> str | None:
    if skill_class is None:
        return None
    if skill_class == "tool_manual":
        exposure = (tool_binding or {}).get("exposure", "primary")
        return f"mcp_{exposure}"
    return skill_class


def _decode_match_terms(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        terms = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(t) for t in terms] if isinstance(terms, list) else []


def _decode_related_skills(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    slugs: list[str] = []
    for entry in values:
        slug = strip_guidance_id_prefix(str(entry).strip())
        if slug.split("#", 1)[0] and slug not in slugs:
            slugs.append(slug.split("#", 1)[0])
    return slugs


def boot_skill_row(row: dict[str, Any]) -> dict[str, Any]:
    skill_class, tool_binding = _parse_skill_binding(row.get("skill_binding_json"))
    item: dict[str, Any] = {
        "id": row["id"],
        "entity_id": row["id"],
        "name": row["name"],
        "description_first_sentence": skill_trigger_text(row),
        "trigger_short": row.get("trigger_short"),
        "skill_category": row.get("skill_category"),
        "trigger_match_terms": _decode_match_terms(row.get("trigger_match_terms_json")),
        "boot_importance": row.get("boot_importance"),
        "related_skills": _decode_related_skills(row.get("related_skills_json")),
        "skill_class": skill_class,
        "binding_kind": _derive_binding_kind(skill_class, tool_binding),
    }
    if tool_binding is not None:
        item["tool_binding"] = tool_binding
    item.update(index_envelope_fields(row))
    return item


def fetch_boot_skills_view(
    conn: Any,
    *,
    limit: int,
    layer: str,
    for_agent_filter: str,
    capability_filter: str,
    seat_params: list[Any],
    entity_types: tuple[str, ...],
) -> tuple[list[dict[str, Any]], int]:
    """Project boot-view items + unpartitioned_count for GET /skills?view=boot."""
    type_placeholders = ", ".join("?" * len(entity_types))
    params: list[Any] = [
        *entity_types,
        DISCOVERABLE_SKILL_LIFECYCLE,
        *seat_params,
        limit,
    ]
    sql = BOOT_SKILLS_SQL.format(
        type_placeholders=type_placeholders,
        for_agent_filter=for_agent_filter,
        capability_filter=capability_filter,
    )
    rows = db_query(conn, sql, tuple(params))
    unpartitioned = 0
    if layer == "skills":
        unpartitioned_rows = db_query(
            conn, UNPARTITIONED_COUNT_SQL, (DISCOVERABLE_SKILL_LIFECYCLE,)
        )
        unpartitioned = int(unpartitioned_rows[0]["n"]) if unpartitioned_rows else 0
    items = [boot_skill_row(r) for r in rows]
    return items, unpartitioned
