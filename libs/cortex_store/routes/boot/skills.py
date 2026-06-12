"""GET /boot-skills — compact agent_skill entity projection for boot briefings."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query

from ...confidence_field import lifecycle_not_value_sql_predicate
from ...db import cortex_conn
from ...db import query as db_query
from ...seat_applicability import FOR_AGENT_CLAUSE, canonical_seat_or_422
from ._skill_trigger import skill_trigger_text

_DEPRECATED_EXCLUDE = lifecycle_not_value_sql_predicate("deprecated")

router = APIRouter(tags=["boot"])

# `applicable_agents` is a JSON-list attribute on each agent_skill entity that
# names which agent slugs should see the skill on their boot card. The list
# may contain `"*"` (visible to every agent) and/or specific slugs. Skills
# without the attribute are withheld from every seat (default-deny); universal
# visibility requires an explicit `["*"]`.
_BOOT_SKILLS_SQL = f"""
    SELECT id, name, description, source_uri,
           json_extract(attributes, '$.skill_binding') AS skill_binding_json,
           json_extract(attributes, '$.trigger_short') AS trigger_short,
           json_extract(attributes, '$.skill_category') AS skill_category,
           json_extract(attributes, '$.trigger_match_terms') AS trigger_match_terms_json,
           json_extract(attributes, '$.boot_importance') AS boot_importance
    FROM entities
    WHERE type = 'agent_skill'
      AND {_DEPRECATED_EXCLUDE}
      {{for_agent_filter}}
    ORDER BY name ASC
    LIMIT ?
"""

_UNPARTITIONED_COUNT_SQL = f"""
    SELECT COUNT(*) AS n
    FROM entities
    WHERE type = 'agent_skill'
      AND {_DEPRECATED_EXCLUDE}
      AND json_extract(attributes, '$.applicable_agents') IS NULL
"""


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


def _boot_skill_row(row: dict[str, Any]) -> dict[str, Any]:
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
        "skill_class": skill_class,
        "binding_kind": _derive_binding_kind(skill_class, tool_binding),
    }
    if tool_binding is not None:
        item["tool_binding"] = tool_binding
    return item


@router.get("/boot-skills")
def get_boot_skills(
    limit: int = Query(50, ge=1, le=200, description="Max skill entries"),
    for_agent: str | None = Query(
        None,
        description=(
            "Filter to skills whose `applicable_agents` list contains "
            "either `*` (universal) or this seat slug. Canonical seat slug "
            "(e.g. `claude-web`, `claude-cursor`, `cursor-sdk`); legacy "
            "spellings are normalized; skills with no `applicable_agents` "
            "are withheld (default-deny) — universal skills carry explicit "
            "`['*']`."
        ),
    ),
) -> dict[str, Any]:
    """Compact agent_skill projection for boot briefings.

    Replaces the wider `/entities?type=agent_skill` fetch on the boot path.
    Each row ships id/entity_id, name, description_first_sentence (projected
    from on-disk frontmatter / **Trigger:** when source_uri resolves), and when
    present the skill_binding axes (skill_class, tool_binding, binding_kind).
    Full SKILL.md bodies are loaded on demand via fs md_* (manifest-only card).
    """
    params: list[Any] = ["deprecated"]
    if for_agent:
        canonical = canonical_seat_or_422(for_agent)
        for_agent_filter = FOR_AGENT_CLAUSE
        params.append(canonical)
    else:
        for_agent_filter = ""
    params.append(limit)
    sql = _BOOT_SKILLS_SQL.format(for_agent_filter=for_agent_filter)
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
        # Count of skills missing applicable_agents — the boot card surfaces
        # this as a drift reminder so the partition script doesn't go stale
        # silently as Kaywan adds new and temp skills. Single SQL query, no
        # row data, ~30 bytes on the wire.
        unpartitioned_rows = db_query(conn, _UNPARTITIONED_COUNT_SQL, ("deprecated",))
    finally:
        conn.close()
    items = [_boot_skill_row(r) for r in rows]
    unpartitioned = int(unpartitioned_rows[0]["n"]) if unpartitioned_rows else 0
    return {"items": items, "unpartitioned_count": unpartitioned}
