"""GET /boot-recent-mentions — entities recently mentioned in session work."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...compaction import POINTER_SQL_LIKE
from ...confidence_field import lifecycle_not_value_sql_predicate
from ...db import cortex_conn
from ...db import query as db_query

_DEPRECATED_EXCLUDE = lifecycle_not_value_sql_predicate("deprecated", "e")

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
# §projection-fidelity split (agent-bus thread 908):
# `active_insert_count`  — assertions with created_at in window (genuine INSERTs)
# `pointer_insert_count` — same, compaction-pointer subset
# `enriched_count`       — assertions with updated_at in window but created_at before
#                          window (predicate_form backfill / Tier-1 writeback / etc.)
#
# Boot card renders only `inserted_count` (derived from active_insert_count).
# `enriched_count` is informational and NOT surfaced by default — it prevents
# predicate_form UPDATEs on old assertions from appearing as phantom activity.
#
# Param order (positional ?):
#   [0] POINTER_SQL_LIKE — active_insert_count CASE (NOT LIKE)
#   [1] POINTER_SQL_LIKE — pointer_insert_count CASE (LIKE)
#   [2] days_arg         — enriched_count subquery: updated_at > window
#   [3] days_arg         — enriched_count subquery: created_at <= window boundary
#   [4] days_arg         — LEFT JOIN: created_at > window
#   [5] days_arg         — WHERE: entity created_at > window
#   [6..N] excluded types (variable)
#   [N+1] days_arg       — HAVING: entity created_at > window
#   [N+2] include_flag
#   [N+3] limit
_RECENT_MENTIONS_SQL = f"""
    SELECT
        e.id AS entity_id,
        e.name AS entity_name,
        e.type AS entity_type,
        e.created_at AS entity_created_at,
        SUM(CASE
            WHEN a.id IS NOT NULL AND a.claim NOT LIKE ? THEN 1 ELSE 0
        END) AS active_insert_count,
        SUM(CASE
            WHEN a.id IS NOT NULL AND a.claim LIKE ? THEN 1 ELSE 0
        END) AS pointer_insert_count,
        MAX(COALESCE(a.created_at, e.created_at)) AS last_mentioned_at,
        (SELECT COUNT(*) FROM assertions a2
            WHERE a2.entity_id = e.id
              AND a2.updated_at > datetime('now', ?)
              AND a2.created_at <= datetime('now', ?)
              AND a2.superseded_by IS NULL) AS enriched_count
    FROM entities e
    LEFT JOIN assertions a
        ON a.entity_id = e.id
        AND a.created_at > datetime('now', ?)
        AND a.superseded_by IS NULL
    WHERE (
            e.created_at > datetime('now', ?)
            OR a.id IS NOT NULL
          )
      AND {_DEPRECATED_EXCLUDE}
      {{type_filter}}
    GROUP BY e.id
    HAVING (
        e.created_at > datetime('now', ?)
        OR ? = 1
        OR active_insert_count > 0
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
    boot agent recognizes them without re-derivation.

    Response items carry two distinct activity counts (§projection-fidelity,
    agent-bus thread 908):

    - ``inserted_count``: assertions with ``created_at`` in the window
      (genuine new content). Boot card renders this as the "N new" badge.
    - ``enriched_count``: assertions created *before* the window whose
      ``updated_at`` falls within it (predicate_form backfill / Tier-1
      writeback / other field updates on old rows). Not surfaced by the boot
      card — prevents enrichment activity from appearing as phantom new content.

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
    # Param order matches _RECENT_MENTIONS_SQL (see comment on that constant).
    params: list[Any] = [
        POINTER_SQL_LIKE,  # active_insert_count CASE
        POINTER_SQL_LIKE,  # pointer_insert_count CASE
        days_arg,  # enriched_count subquery: updated_at > window
        days_arg,  # enriched_count subquery: created_at <= window
        days_arg,  # LEFT JOIN: created_at > window
        days_arg,  # WHERE: entity created_at > window
        "deprecated",
        "deprecated",  # lifecycle_not_value predicate (trait-native)
    ]
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
        active = r["active_insert_count"] or 0
        pointer = r["pointer_insert_count"] or 0
        # inserted_count: genuine new assertions (created_at in window).
        # When include_compaction_pointers, pointer inserts are included — they
        # ARE real inserts, just bookkeeping-class ones.
        inserted = active + pointer if include_compaction_pointers else active
        enriched = r["enriched_count"] or 0
        items.append(
            {
                "entity_id": r["entity_id"],
                "entity_name": r["entity_name"],
                "entity_type": r["entity_type"],
                "inserted_count": inserted,
                "enriched_count": enriched,
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
