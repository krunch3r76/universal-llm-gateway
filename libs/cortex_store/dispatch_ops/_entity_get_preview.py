"""entity_get preview projection — sparse default for omitted intent."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException

from ..db import query

_PREVIEW_OMITTED = ("attributes", "assertions", "journal", "relations", "content")
_SUMMARY_CAP = 500


def read_live_entity_revision(
    conn: sqlite3.Connection, entity_id: str
) -> int | None:
    """Return entities.revision when the column exists, else None."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()}
    if "revision" not in cols:
        return None
    row = conn.execute(
        "SELECT revision FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if not row:
        return None
    val = row["revision"]
    return int(val) if val is not None else 0


def _clip_summary(text: str) -> tuple[str, bool, str]:
    raw = text.strip()
    if len(raw) <= _SUMMARY_CAP:
        return raw, False, "description"
    return raw[:_SUMMARY_CAP], True, "description"


def _freshest_assertion(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    rows = query(
        conn,
        "SELECT id, claim, observed_at, confidence FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL "
        "ORDER BY COALESCE(observed_at, created_at) DESC, id ASC LIMIT 5",
        (entity_id,),
    )
    return rows[0] if rows else None


def _conflict_slot(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    rows = query(
        conn,
        "SELECT id, claim, confidence FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL AND confidence = 'contradicts' "
        "ORDER BY id DESC LIMIT 1",
        (entity_id,),
    )
    if not rows:
        return {}
    row = rows[0]
    all_rows = query(
        conn,
        "SELECT COUNT(*) AS n FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL AND confidence = 'contradicts'",
        (entity_id,),
    )
    count = int(all_rows[0]["n"]) if all_rows else 0
    freshest = _freshest_assertion(conn, entity_id)
    conflict_id = int(row["id"])
    if freshest and int(freshest["id"]) == conflict_id:
        return {}
    return {
        "contradiction_count": count,
        "conflict_assertion_id": conflict_id,
        "conflict_marker": str(row["claim"])[:200],
    }


def build_entity_preview(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
) -> dict[str, Any]:
    rows = query(
        conn,
        "SELECT id, type, name, description, workflow_state, confidence_band, "
        "lifecycle, updated_at, content_hash, attributes FROM entities WHERE id = ?",
        (entity_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")
    row = rows[0]
    summary, truncated, summary_source = _clip_summary(
        str(row.get("description") or row.get("name") or "")
    )
    attr_count = 0
    raw_attrs = row.get("attributes")
    if isinstance(raw_attrs, str) and raw_attrs.strip():
        try:
            import json

            parsed = json.loads(raw_attrs)
            if isinstance(parsed, dict):
                attr_count = len(parsed)
            elif isinstance(parsed, list):
                attr_count = len(parsed)
        except json.JSONDecodeError:
            attr_count = 0
    assertion_rows = query(
        conn,
        "SELECT COUNT(*) AS n FROM assertions WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    assertion_count = int(assertion_rows[0]["n"]) if assertion_rows else 0
    rel_rows = query(
        conn,
        "SELECT COUNT(*) AS n FROM relationships "
        "WHERE (from_entity = ? OR to_entity = ?) AND active = 1",
        (entity_id, entity_id),
    )
    rel_count = int(rel_rows[0]["n"]) if rel_rows else 0
    child_rows = query(
        conn,
        "SELECT COUNT(*) AS n FROM relationships "
        "WHERE from_entity = ? AND type = 'archives_to' AND active = 1",
        (entity_id,),
    )
    child_count = int(child_rows[0]["n"]) if child_rows else 0
    freshest = _freshest_assertion(conn, entity_id)
    preview: dict[str, Any] = {
        "entity_id": entity_id,
        "id": entity_id,
        "type": row["type"],
        "title": row.get("name"),
        "name": row.get("name"),
        "current_state": row.get("workflow_state")
        or row.get("lifecycle")
        or row.get("confidence_band"),
        "short_summary": summary,
        "summary_truncated": truncated,
        "summary_source": summary_source,
        "updated_at": row.get("updated_at"),
        "revision": read_live_entity_revision(conn, entity_id),
        "content_hash": row.get("content_hash"),
        "freshest_assertion_id": int(freshest["id"]) if freshest else None,
        "facet_counts": {
            "attributes": attr_count,
            "assertions": assertion_count,
            "relations": rel_count,
            "children": child_count,
        },
        "expansion": {
            "intent=card": {"remaining_budget": "card"},
            "intent=body": {"remaining_budget": "body"},
            "entity_ids": {"remaining_budget": assertion_count},
        },
        "intent": "preview",
        "selection": "preview",
        "omitted": list(_PREVIEW_OMITTED),
    }
    preview.update(_conflict_slot(conn, entity_id))
    return preview
