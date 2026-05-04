"""GET /boot-recent-mentions — entities recently mentioned in session work."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...compaction import POINTER_SQL_LIKE
from ...db import cortex_conn
from ...db import query as db_query

router = APIRouter(tags=["boot"])

# Recent mentions: entities with new assertions OR newly-created entities within
# trailing window. Surfaces names that came up in session work so boot agents
# recognize them without re-derivation. Noisy system types are excluded by
# default; callers may override via type_exclude.
#
# Renderable types should be the things a person would *recognize as a name*
# (people, cases, decisions, plans, services, agents) — not internal structure
# nodes. Structural accumulators (`plan_phase`, `boot_session`) and types
# canonically surfaced elsewhere on the boot card (`agent_skill` → Skills
# section, `transcript`/`todo`/`journal`/`assertion` → dedicated surfaces) are
# pruned at the API default so every consumer benefits.
_RECENT_MENTIONS_DEFAULT_EXCLUDE = (
    "transcript",
    "todo",
    "journal",
    "assertion",
    "plan_phase",
    "agent_skill",
    "boot_session",
)

# Compaction-pointer assertions are pure bookkeeping noise on the boot card —
# an entity that just received N pointer-writes appears as "N new" while no
# actual session activity occurred (todo:cortex-aggregate-compaction-filter §1).
# Default behaviour: strict-exclude pointers from `recent_mention_count` and
# from the inclusion criterion (entities whose only recent activity was pointer
# writes drop off the list). The pointer count is reported as a sibling field
# so the prior unfiltered total is reconstructable on demand (§4 dual-count).
_RECENT_MENTIONS_SQL = """
    SELECT
        e.id AS entity_id,
        e.name AS entity_name,
        e.type AS entity_type,
        e.created_at AS entity_created_at,
        SUM(CASE
            WHEN a.id IS NOT NULL AND a.claim NOT LIKE ? THEN 1 ELSE 0
        END) AS active_mention_count,
        SUM(CASE
            WHEN a.id IS NOT NULL AND a.claim LIKE ? THEN 1 ELSE 0
        END) AS pointer_mention_count,
        MAX(COALESCE(a.created_at, e.created_at)) AS last_mentioned_at
    FROM entities e
    LEFT JOIN assertions a
        ON a.entity_id = e.id
        AND a.created_at > datetime('now', ?)
        AND a.superseded_by IS NULL
    WHERE (
            e.created_at > datetime('now', ?)
            OR a.id IS NOT NULL
          )
      AND (e.status IS NULL OR e.status != 'deprecated')
      {type_filter}
    GROUP BY e.id
    HAVING (
        e.created_at > datetime('now', ?)
        OR ? = 1
        OR active_mention_count > 0
    )
    ORDER BY last_mentioned_at DESC
    LIMIT ?
"""


@router.get("/boot-recent-mentions")
def get_boot_recent_mentions(
    days: int = Query(7, ge=1, le=30, description="Trailing window in days"),
    limit: int = Query(10, ge=1, le=30, description="Max entities"),
    type_exclude: str | None = Query(
        None,
        description=(
            "Comma-separated entity types to exclude. Defaults to "
            "'transcript,todo,journal,assertion,plan_phase,agent_skill,"
            "boot_session' — system/meta and structural types already "
            "surfaced elsewhere on the boot card. Pass an explicit list "
            "(possibly empty) to override."
        ),
    ),
    include_compaction_pointers: bool = Query(
        False,
        description=(
            "When true, count compaction-pointer assertions as recent activity "
            "and surface entities whose only recent activity was pointer writes. "
            "Default false — boot card focuses on substantive session activity. "
            "Pointer counts are always reported separately as `pointer_count`."
        ),
    ),
) -> dict[str, Any]:
    """Entities recently mentioned via new assertions or new entity creation.

    Surfaces a roster of names that came up in trailing session work so the
    boot agent recognizes them without re-derivation. Covers the case where
    Kaywan references a person/organization that was introduced in a prior
    session — the entity exists in the graph, but the boot card previously
    had no way to surface it unless it appeared in another section (deadlines,
    todos, etc.).

    Default window: 7 days. Default exclusions: transcript, todo, journal,
    assertion (already surfaced elsewhere or noisy). Compaction-pointer
    assertions are excluded from the recent-activity criterion by default
    (todo:cortex-aggregate-compaction-filter); pass
    ``include_compaction_pointers=true`` to restore the prior pre-filter
    behaviour for structural-audit consumers.
    """
    if type_exclude is None:
        excluded = list(_RECENT_MENTIONS_DEFAULT_EXCLUDE)
    else:
        excluded = [t.strip() for t in type_exclude.split(",") if t.strip()]

    type_filter = ""
    days_arg = f"-{days} days"
    include_flag = 1 if include_compaction_pointers else 0
    # Param order matches placeholders in _RECENT_MENTIONS_SQL:
    # 1: pointer LIKE (active CASE), 2: pointer LIKE (pointer CASE),
    # 3: window (LEFT JOIN), 4: window (WHERE), [type_filter excludes…],
    # 5: window (HAVING), 6: include flag, 7: limit.
    params: list[Any] = [POINTER_SQL_LIKE, POINTER_SQL_LIKE, days_arg, days_arg]
    if excluded:
        placeholders = ",".join("?" * len(excluded))
        type_filter = f"AND e.type NOT IN ({placeholders})"
        params.extend(excluded)
    params.extend([days_arg, include_flag, limit])

    sql = _RECENT_MENTIONS_SQL.format(type_filter=type_filter)
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
    finally:
        conn.close()

    items = []
    for r in rows:
        active = r["active_mention_count"] or 0
        pointer = r["pointer_mention_count"] or 0
        # Default surface counts only active assertions; on override, restore
        # the prior unfiltered total so callers asking for raw history get it.
        recent_count = active + pointer if include_compaction_pointers else active
        items.append(
            {
                "entity_id": r["entity_id"],
                "entity_name": r["entity_name"],
                "entity_type": r["entity_type"],
                "recent_mention_count": recent_count,
                "pointer_count": pointer,
                "last_mentioned_at": r["last_mentioned_at"],
                "entity_created_at": r["entity_created_at"],
            }
        )
    return {
        "items": items,
        "window_days": days,
        "excluded_types": excluded,
        "include_compaction_pointers": include_compaction_pointers,
    }
