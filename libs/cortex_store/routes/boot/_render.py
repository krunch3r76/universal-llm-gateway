"""Salience section rendering: helpers, full sections, one-line sections, slow-state."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from ...db import query as db_query
from ...salience import SalienceResult

router = APIRouter(tags=["boot"])


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    rows = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return rows is not None


def _relative_time(iso_str: str | None, now: datetime) -> str:
    """Format an ISO timestamp as a human-readable relative time."""
    if not iso_str:
        return "unknown"
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta_s = (now - ts).total_seconds()
        if delta_s < 0:
            return "just now"
        if delta_s < 3600:
            return f"{int(delta_s / 60)}m ago"
        if delta_s < 86400:
            return f"{int(delta_s / 3600)}h ago"
        return f"{int(delta_s / 86400)}d ago"
    except (ValueError, TypeError):
        return "unknown"


def _render_full_section(
    r: SalienceResult,
    assertions: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    now: datetime,
    tags: list[dict[str, Any]] | None = None,
) -> str:
    """Render a full entity section as Markdown."""
    last_active = "unknown"
    if assertions:
        latest = assertions[0].get("observed_at") or assertions[0].get("created_at")
        last_active = _relative_time(latest, now)

    header = f"### {r.entity_name} ({r.entity_type})"
    if r.domain:
        header += f" — {r.domain}"

    lines = [
        header,
        f"**Salience**: {r.salience_score:.2f} | **Last active**: {last_active}",
        "",
    ]

    non_current_tags = [t for t in (tags or []) if t["tag_name"] != "current"]
    if non_current_tags:
        tag_parts = [
            f"{t['tag_name']} (assertion #{t['assertion_id']})"
            for t in non_current_tags
        ]
        lines.append(f"**Tags**: {', '.join(tag_parts)}")
        lines.append("")

    for a in assertions:
        conf = a.get("confidence", "?")
        ent_score = a.get("entrenchment_score")
        score_suffix = f" (e={ent_score:.2f})" if ent_score else ""
        lines.append(f"- [{conf}]{score_suffix} {a.get('claim', '')}")
        prospective = a.get("prospective_summary")
        if prospective:
            lines.append(f"  *Prospective*: {prospective}")

    if relationships:
        connected: list[str] = []
        for rel in relationships:
            if rel["from_entity"] == r.entity_id:
                name = rel.get("target_name") or rel["to_entity"]
            else:
                name = rel.get("source_name") or rel["from_entity"]
            rel_desc = rel.get("type_name")
            connected.append(f"{name} ({rel_desc})" if rel_desc else name)
        lines.append(f"\n**Connected**: {', '.join(connected)}")

    return "\n".join(lines)


def _resolve_current_assertion_id(
    conn: sqlite3.Connection, entity_id: str
) -> int | None:
    """Check for a 'current' tag; return its assertion_id or None to fall back."""
    rows = db_query(
        conn,
        "SELECT assertion_id FROM tag_assignments "
        "WHERE tag_name = 'current' AND entity_id = ?",
        (entity_id,),
    )
    return rows[0]["assertion_id"] if rows else None


def _build_full_sections(
    conn: sqlite3.Connection,
    results: list[SalienceResult],
    now: datetime,
) -> list[dict[str, Any]]:
    """Fetch assertions/relationships and render full sections."""
    _has_tags_table = _table_exists(conn, "tag_assignments")

    sections: list[dict[str, Any]] = []
    for r in results:
        current_aid: int | None = None
        if _has_tags_table:
            current_aid = _resolve_current_assertion_id(conn, r.entity_id)

        if current_aid is not None:
            assertions = db_query(
                conn,
                "SELECT entity_id, claim, confidence, evidence, "
                "prospective_summary, entrenchment_score, observed_at, created_at "
                "FROM assertions WHERE id = ?",
                (current_aid,),
            )
        else:
            assertions = db_query(
                conn,
                "SELECT entity_id, claim, confidence, evidence, "
                "prospective_summary, entrenchment_score, observed_at, created_at "
                "FROM assertions "
                "WHERE entity_id = ? AND superseded_by IS NULL "
                "ORDER BY COALESCE(entrenchment_score, 0.0) DESC, created_at DESC "
                "LIMIT 5",
                (r.entity_id,),
            )

        entity_tags: list[dict[str, Any]] = []
        if _has_tags_table:
            entity_tags = db_query(
                conn,
                "SELECT tag_name, assertion_id FROM tag_assignments "
                "WHERE entity_id = ? ORDER BY tag_name",
                (r.entity_id,),
            )

        rel_rows = db_query(
            conn,
            "SELECT r.from_entity, r.to_entity, r.type, "
            "rt.description AS type_name, "
            "se.name AS source_name, te.name AS target_name "
            "FROM relationships r "
            "LEFT JOIN relationship_types rt ON rt.type = r.type "
            "LEFT JOIN entities se ON se.id = r.from_entity "
            "LEFT JOIN entities te ON te.id = r.to_entity "
            "WHERE r.from_entity = ? OR r.to_entity = ? "
            "ORDER BY COALESCE(r.strength, 0) DESC LIMIT 5",
            (r.entity_id, r.entity_id),
        )
        sections.append(
            {
                "entity_id": r.entity_id,
                "entity_name": r.entity_name,
                "entity_type": r.entity_type,
                "salience_score": round(r.salience_score, 3),
                "surprise": round(r.surprise, 3),
                "domain": r.domain,
                "section_markdown": _render_full_section(
                    r, assertions, rel_rows, now, tags=entity_tags
                ),
            }
        )
    return sections


def _build_oneline_sections(
    conn: sqlite3.Connection,
    results: list[SalienceResult],
    now: datetime,
) -> list[dict[str, Any]]:
    """Build one-line summary entries with batch queries."""
    if not results:
        return []

    entity_ids = [r.entity_id for r in results]
    placeholders = ",".join("?" * len(entity_ids))

    count_rows = db_query(
        conn,
        f"SELECT entity_id, COUNT(*) AS cnt FROM assertions "
        f"WHERE entity_id IN ({placeholders}) GROUP BY entity_id",
        tuple(entity_ids),
    )
    assertion_counts = {r["entity_id"]: r["cnt"] for r in count_rows}

    activity_rows = db_query(
        conn,
        f"SELECT entity_id, "
        f"MAX(COALESCE(observed_at, created_at)) AS last_active "
        f"FROM assertions WHERE entity_id IN ({placeholders}) "
        f"GROUP BY entity_id",
        tuple(entity_ids),
    )
    last_activities = {r["entity_id"]: r["last_active"] for r in activity_rows}

    sections: list[dict[str, Any]] = []
    for r in results:
        cnt = assertion_counts.get(r.entity_id, 0)
        last_active = _relative_time(last_activities.get(r.entity_id), now)
        sections.append(
            {
                "entity_id": r.entity_id,
                "entity_name": r.entity_name,
                "entity_type": r.entity_type,
                "salience_score": round(r.salience_score, 3),
                "summary": (
                    f"**{r.entity_name}** ({r.entity_type}): "
                    f"Last active {last_active} | {cnt} assertions"
                ),
            }
        )
    return sections


def _advance_slow_state(conn: sqlite3.Connection) -> int:
    """Advance slow_state_hash to fast_state_hash for all cached entities.

    Boot is the observation point. Resets last_surprise=0 and
    boot_section_cache='one_line' so next boot's cache hits reflect the
    advanced observation: unchanged entities show surprise=0, only entities
    with new assertions/relationships (fingerprint change) trigger recompute
    with meaningful surprise on the next boot.
    """
    cur = conn.execute(
        "UPDATE entity_salience_cache "
        "SET slow_state_hash = fast_state_hash, "
        "last_surprise = 0.0, "
        "boot_section_cache = 'one_line' "
        "WHERE fast_state_hash IS NOT NULL"
    )
    conn.commit()
    return cur.rowcount
