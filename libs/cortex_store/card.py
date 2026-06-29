"""Card v0 read path (Cortex v2.4 §6.2 / §6.3).

Projection-aware fetch plan: identity columns + top-K active assertions
+ relationship-type aggregates + archives_to count + section counts.
NOT a load-and-trim wrapper over ``_get_entity_impl`` — that would
violate the §6.2 architectural target (shrinking wire bytes without
shrinking the fetch plan is not the win).

Compaction-pointer ordering (§6.10) is honored at the SQL level via
``SUMMARY_SQL_LIKE`` / ``POINTER_SQL_LIKE`` from ``compaction.py``.

Per-entity-type semantics (section labels, status_summary,
summary_row) are dispatched through ``card_adapters`` (§6.4) — the
fetch plan stays uniform; the rendering varies.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import HTTPException, status
from universal_logging import get_logger

from .card_adapters import CardAdapterCounts, get_adapter
from .compaction import (
    POINTER_SQL_LIKE,
    SUMMARY_SQL_LIKE,
    is_compaction_pointer,
    is_tombstone_only,
)
from .confidence_field import agent_skill_is_discoverable
from .db import json_decode, query
from .models import (
    CardAssertion,
    CardAssertionCounts,
    CardDebug,
    CardEdgeTypeCount,
    EntityCard,
)
from .predicate_summary import aggregate_predicate_summary

try:
    from .routes.assertions import _truncate_claim
except ImportError:
    from .routes.assertions._shared import _truncate_claim

logger = get_logger("cortex-api.card")

CARD_TOP_K_DEFAULT = 7
CARD_INTENTS_DEFERRED = frozenset({"cluster", "impact"})

_CARD_ASSERTION_COLS = (
    "id, claim, confidence, derivation_type, valid_from, "
    "observed_at, predicate_form, evidence_uris, entrenchment_score, "
    "prospective_summary, created_at"
)

_CARD_ORDER_BY = (
    "  (CASE WHEN LOWER(claim) LIKE LOWER(?) THEN 0 ELSE 1 END) ASC, "
    "  (CASE WHEN LOWER(claim) LIKE LOWER(?) THEN 1 ELSE 0 END) ASC, "
    "  COALESCE(entrenchment_score, 0) DESC, "
    "  created_at DESC"
)

_STATUS_CURRENT_PREDICATE_WHERE = (
    "predicate_form IS NOT NULL AND ("
    "LOWER(predicate_form) LIKE 'status(%, current)' "
    "OR LOWER(predicate_form) LIKE '%\\_status(%, current)' ESCAPE '\\'"
    ")"
)


def _card_rank_sort_key(row: dict[str, object]) -> tuple[object, ...]:
    """Python mirror of ``_CARD_ORDER_BY`` for Option E merge re-sort."""
    claim = str(row.get("claim") or "")
    summary_bucket = 0 if claim.lower().startswith("archive summary") else 1
    pointer_bucket = 1 if is_compaction_pointer(claim) else 0
    ent = float(row.get("entrenchment_score") or 0)
    created = row.get("created_at")
    try:
        created_ts = -datetime.fromisoformat(
            str(created).replace("Z", "+00:00")
        ).timestamp()
    except (TypeError, ValueError):
        created_ts = 0.0
    return (summary_bucket, pointer_bucket, -ent, created_ts)


def _fetch_main_top_k(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    top_k: int,
) -> list[dict[str, object]]:
    return query(
        conn,
        f"SELECT {_CARD_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
        "AND superseded_by IS NULL "
        f"ORDER BY {_CARD_ORDER_BY} LIMIT ?",
        (entity_id, SUMMARY_SQL_LIKE, POINTER_SQL_LIKE, top_k),
    )


def _fetch_current_status_row(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
) -> dict[str, object] | None:
    rows = query(
        conn,
        f"SELECT {_CARD_ASSERTION_COLS} FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL "
        f"AND {_STATUS_CURRENT_PREDICATE_WHERE} "
        "ORDER BY created_at DESC LIMIT 1",
        (entity_id,),
    )
    return rows[0] if rows else None


def _merge_current_status_slot(
    main: list[dict[str, object]],
    e_row: dict[str, object] | None,
    *,
    top_k: int,
) -> list[dict[str, object]]:
    if e_row is None:
        return main
    main_ids = {int(r["id"]) for r in main}
    if int(e_row["id"]) in main_ids:
        return main
    merged = list(main) + [e_row]
    merged.sort(key=_card_rank_sort_key)
    if len(merged) <= top_k:
        return merged
    e_id = int(e_row["id"])
    while len(merged) > top_k:
        drop_idx = next(
            (i for i in range(len(merged) - 1, -1, -1) if int(merged[i]["id"]) != e_id),
            None,
        )
        if drop_idx is None:
            break
        merged.pop(drop_idx)
    return merged


def get_entity_card(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    top_k: int = CARD_TOP_K_DEFAULT,
    debug: bool = False,
    source: str = "agent",
    agent: str = "web",
    session_id: str | None = None,
) -> dict[str, object]:
    """Build the Card v0 payload via projection-aware fetch + adapter dispatch."""
    rows_materialized = 0

    table_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()
    }
    ent_cols = ["id", "type", "name", "description"]
    for col in ("lifecycle", "confidence_band", "adoption"):
        if col in table_cols:
            ent_cols.append(col)
    ent_cols.extend(
        [
            "workflow_state",
            "attributes",
            "source_uri",
            "content_hash",
            "created_at",
            "updated_at",
        ]
    )
    ent_rows = query(
        conn,
        f"SELECT {', '.join(ent_cols)} FROM entities WHERE id = ?",
        (entity_id,),
    )
    rows_materialized += len(ent_rows)
    if not ent_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_id}",
        )
    e = ent_rows[0]

    # §6.10 ordering: archive summaries first, compaction pointers last.
    # predicate_form included for Slice 4 aggregate_predicate_summary (§6.7).
    # Option A: entrenchment_score breaks ties after buckets (cross-surface
    # consistency with boot/_render.py).
    a_rows = _fetch_main_top_k(conn, entity_id=entity_id, top_k=top_k)
    rows_materialized += len(a_rows)

    count_rows = query(
        conn,
        "SELECT "
        "  SUM(CASE WHEN superseded_by IS NULL THEN 1 ELSE 0 END) AS active_n, "
        "  SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END) AS superseded_n "
        "FROM assertions WHERE entity_id = ?",
        (entity_id,),
    )
    rows_materialized += len(count_rows)
    active_n = int(count_rows[0]["active_n"] or 0) if count_rows else 0
    superseded_n = int(count_rows[0]["superseded_n"] or 0) if count_rows else 0

    et_rows = query(
        conn,
        "SELECT type AS type_id, COUNT(*) AS n FROM relationships "
        "WHERE (from_entity = ? OR to_entity = ?) AND active = 1 "
        "GROUP BY type ORDER BY n DESC",
        (entity_id, entity_id),
    )
    rows_materialized += len(et_rows)
    rel_total = sum(int(r["n"]) for r in et_rows)

    arc_rows = query(
        conn,
        "SELECT to_entity FROM relationships "
        "WHERE from_entity = ? AND type = 'archives_to' AND active = 1",
        (entity_id,),
    )
    rows_materialized += len(arc_rows)
    archives_to_count = len(arc_rows)
    archives_to_children = [str(r["to_entity"]) for r in arc_rows]

    se_rows = query(
        conn,
        "SELECT COUNT(*) AS n FROM session_edges "
        "WHERE (from_node = ? OR to_node = ?) AND valid_until IS NULL",
        (entity_id, entity_id),
    )
    rows_materialized += len(se_rows)
    edges_n = int(se_rows[0]["n"]) if se_rows else 0

    if source != "boot":
        try:
            conn.execute(
                "INSERT INTO entity_access_log "
                "(entity_id, agent, operation, source, session_id) "
                "VALUES (?, ?, 'entity_get', ?, ?)",
                (entity_id, agent, source, session_id),
            )
            conn.commit()
        except Exception:
            logger.warning("Access log insert failed for %s (card)", entity_id)

    counts: CardAdapterCounts = {
        "active_n": active_n,
        "superseded_n": superseded_n,
        "rel_total": rel_total,
        "archives_to_count": archives_to_count,
        "edges_n": edges_n,
    }

    adapter = get_adapter(str(e["type"]))
    section_manifest = adapter.sections(dict(e), counts)

    # §6.10 tombstone-collapse: substitute the consolidation summary when all
    # active assertions are pointers, so the card surfaces meaningful content.
    all_active_claims = [str(r["claim"]) for r in a_rows]
    surfaced_rows: list[dict[str, object]] = []
    if active_n > 0 and is_tombstone_only(all_active_claims):
        summary_rows = query(
            conn,
            "SELECT id, claim, confidence, derivation_type, valid_from, observed_at, evidence_uris "
            "FROM assertions WHERE entity_id = ? "
            "AND LOWER(claim) LIKE LOWER(?) ORDER BY created_at DESC LIMIT 1",
            (entity_id, SUMMARY_SQL_LIKE),
        )
        rows_materialized += len(summary_rows)
        top_k_for_card = [_card_assertion(r) for r in summary_rows]
        predicate_summary: str = (
            f"archived → see children [{', '.join(archives_to_children)}]"
            if archives_to_children
            else "tombstoned"
        )
    else:
        e_row = _fetch_current_status_row(conn, entity_id=entity_id)
        if e_row is not None:
            rows_materialized += 1
        surfaced_rows = _merge_current_status_slot(a_rows, e_row, top_k=top_k)
        top_k_for_card = [_card_assertion(r) for r in surfaced_rows]
        # §6.3 / §6.7: three-tier predicate_form aggregation (Slice 4).
        # Input stays the unmodified main query — NOT the E-merged set.
        predicate_summary = aggregate_predicate_summary(
            top_k_assertions=a_rows,
            et_type_counts=[
                {"type_id": str(r["type_id"]), "count": int(r["n"])} for r in et_rows
            ],
            archives_to_children=archives_to_children,
            entity_id=entity_id,
        )

    debug_payload: CardDebug | None = None
    if debug:
        debug_payload = CardDebug(
            fetch_plan_row_volume=rows_materialized,
            prospective_summaries=[
                str(r.get("prospective_summary"))
                if r.get("prospective_summary") is not None
                else None
                for r in surfaced_rows
            ]
            if surfaced_rows
            else None,
        )

    card = EntityCard(
        id=str(e["id"]),
        type=str(e["type"]),
        name=str(e["name"]),
        summary_row=adapter.summary_row(dict(e)),
        status_summary=adapter.status_summary(dict(e)),
        top_k_assertions=top_k_for_card,
        assertion_counts=CardAssertionCounts(
            active=active_n,
            superseded=superseded_n,
        ),
        edge_type_summary=[
            CardEdgeTypeCount(type_id=str(r["type_id"]), count=int(r["n"]))
            for r in et_rows
        ],
        archives_to_count=archives_to_count,
        section_manifest=section_manifest,
        predicate_summary=predicate_summary,
        freshness={
            "created_at": str(e["created_at"]),
            "updated_at": str(e["updated_at"]),
        },
        debug=debug_payload,
    )
    payload = card.model_dump(mode="json")
    if str(e["type"]) == "agent_skill":
        payload["discoverable"] = agent_skill_is_discoverable(e.get("lifecycle"))  # type: ignore[arg-type]
    return payload


def _card_assertion(r: dict[str, object]) -> CardAssertion:
    return CardAssertion(
        id=int(r["id"]),  # type: ignore[arg-type]
        claim=_truncate_claim(str(r["claim"])),
        confidence=r["confidence"],  # type: ignore[arg-type]
        derivation_type=r.get("derivation_type"),  # type: ignore[arg-type]
        valid_from=r.get("valid_from"),  # type: ignore[arg-type]
        observed_at=r.get("observed_at"),  # type: ignore[arg-type]
        evidence_uris=json_decode(r.get("evidence_uris")),  # type: ignore[arg-type]
        entrenchment_score=(
            float(r["entrenchment_score"])  # type: ignore[arg-type]
            if r.get("entrenchment_score") is not None
            else None
        ),
    )
