"""GET /boot-skills — compact agent_skill entity projection for boot briefings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...db import cortex_conn
from ...db import query as db_query

router = APIRouter(tags=["boot"])

# `applicable_agents` is a JSON-list attribute on each agent_skill entity that
# names which agent slugs should see the skill on their boot card. The list
# may contain `"*"` (visible to every agent) and/or specific slugs. Skills
# without the attribute are treated as `["*"]` via COALESCE so the default
# behaviour pre-backfill is "show to everyone" — no silent narrowing.
_BOOT_SKILLS_SQL = """
    SELECT id, name, description
    FROM entities
    WHERE type = 'agent_skill'
      AND (status IS NULL OR status != 'deprecated')
      {for_agent_filter}
    ORDER BY name ASC
    LIMIT ?
"""

_FOR_AGENT_CLAUSE = """
    AND EXISTS (
        SELECT 1 FROM json_each(
            COALESCE(
                json_extract(attributes, '$.applicable_agents'),
                json_array('*')
            )
        )
        WHERE value IN ('*', ?)
    )
"""

_UNPARTITIONED_COUNT_SQL = """
    SELECT COUNT(*) AS n
    FROM entities
    WHERE type = 'agent_skill'
      AND (status IS NULL OR status != 'deprecated')
      AND json_extract(attributes, '$.applicable_agents') IS NULL
"""


def _first_sentence(text: str | None) -> str:
    """Return the first sentence of `text`, stripped of trailing whitespace.

    Boot card renders skills as `**slug** — <trigger>`. The full SKILL.md is
    behind a `fs read` on trigger match, so the briefing only needs the first
    sentence — the trigger condition. Splitting on `. ` matches the pre-existing
    renderer's behaviour (`_briefing_card.py`); shipping pre-split saves the
    rest of the description bytes on the wire.
    """
    if not text:
        return ""
    return text.split(". ", 1)[0].rstrip(".").strip()


@router.get("/boot-skills")
def get_boot_skills(
    limit: int = Query(50, ge=1, le=200, description="Max skill entries"),
    for_agent: str | None = Query(
        None,
        description=(
            "Filter to skills whose `applicable_agents` list contains "
            "either `*` (universal) or this agent slug. Skills without "
            "the attribute are treated as universal — pre-backfill safe "
            "default. Pass the agent slug used by cortex_boot (e.g. "
            "'cursor', 'web', 'orion') to get the per-agent partition."
        ),
    ),
) -> dict[str, Any]:
    """Compact agent_skill projection: id, name, first sentence of description.

    Replaces the wider `/entities?type=agent_skill` fetch on the boot path.
    Each row is reduced to the three fields the briefing card actually
    renders, with the description trimmed to its first sentence (the trigger
    condition). Full SKILL.md is loaded on demand via `fs read` once an
    agent's task matches a trigger.
    """
    params: list[Any] = []
    if for_agent:
        for_agent_filter = _FOR_AGENT_CLAUSE
        params.append(for_agent)
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
        unpartitioned_rows = db_query(conn, _UNPARTITIONED_COUNT_SQL, ())
    finally:
        conn.close()
    items = [
        {
            "id": r["id"],
            "name": r["name"],
            "description_first_sentence": _first_sentence(r["description"]),
        }
        for r in rows
    ]
    unpartitioned = (
        int(unpartitioned_rows[0]["n"]) if unpartitioned_rows else 0
    )
    return {"items": items, "unpartitioned_count": unpartitioned}
