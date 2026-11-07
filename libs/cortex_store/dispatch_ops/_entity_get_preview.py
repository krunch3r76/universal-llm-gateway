"""entity_get preview projection — sparse default for omitted intent."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException

from ..db import query

_PREVIEW_OMITTED = ("attributes", "assertions", "journal", "relations", "content")
_SUMMARY_CAP = 500


def read_live_entity_revision(conn: sqlite3.Connection, entity_id: str) -> int | None:
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


def _assertion_sort_key(row: dict[str, Any]) -> tuple[str, int]:
    """Newest ``observed_at``, then lowest numeric id (via negated id under reverse)."""
    observed = row.get("observed_at")
    stamp = observed if isinstance(observed, str) else ""
    return (stamp, -int(row["id"]))


def _freshest_of(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=_assertion_sort_key, reverse=True)[0]


def _freshest_assertion(
    conn: sqlite3.Connection, entity_id: str
) -> dict[str, Any] | None:
    rows = query(
        conn,
        "SELECT id, claim, observed_at, confidence, review_status, entity_id "
        "FROM assertions WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    return _freshest_of(rows)


def _parse_assertion_node(node: object) -> int | None:
    if not isinstance(node, str) or not node.startswith("assertion:"):
        return None
    raw = node.removeprefix("assertion:")
    if not raw.isdigit():
        return None
    return int(raw)


def _conflict_slot(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    """Live ``contradicts`` edges. Assertion confidence has no ``contradicts`` value.

    A pair counts only when both endpoints are live (not superseded) assertions
    and at least one belongs to ``entity_id``. ``conflict_assertion_id`` is the
    non-freshest member of the chosen pair: newest ``observed_at``, then lowest id.
    """
    empty = {
        "contradiction_count": 0,
        "conflict_assertion_id": None,
        "conflict_marker": None,
    }
    live = query(
        conn,
        "SELECT id FROM assertions WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    live_ids = {int(row["id"]) for row in live}
    if not live_ids:
        return empty
    edges = query(
        conn,
        "SELECT from_node, to_node FROM session_edges "
        "WHERE edge_type = 'contradicts' AND valid_until IS NULL",
    )
    pair_ids: list[tuple[int, int]] = []
    needed: set[int] = set()
    for edge in edges:
        left = _parse_assertion_node(edge.get("from_node"))
        right = _parse_assertion_node(edge.get("to_node"))
        if left is None or right is None or left == right:
            continue
        if left not in live_ids and right not in live_ids:
            continue
        pair_ids.append((left, right))
        needed.add(left)
        needed.add(right)
    if not pair_ids:
        return empty
    loaded = query(
        conn,
        "SELECT id, claim, observed_at, confidence, review_status, entity_id, "
        "superseded_by FROM assertions WHERE id IN ("
        + ",".join("?" for _ in needed)
        + ")",
        tuple(needed),
    )
    by_id = {int(row["id"]): row for row in loaded if row.get("superseded_by") is None}
    freshest = _freshest_assertion(conn, entity_id)
    candidates: list[dict[str, Any]] = []
    counted = 0
    for left, right in pair_ids:
        members = [by_id[i] for i in (left, right) if i in by_id]
        if len(members) != 2:
            continue
        if not any(int(row["id"]) in live_ids for row in members):
            continue
        counted += 1
        pair_fresh = _freshest_of(members)
        if pair_fresh is None:
            continue
        others = [row for row in members if int(row["id"]) != int(pair_fresh["id"])]
        if others:
            candidates.append(others[0])
    if freshest is not None:
        # Prefer the non-freshest side relative to this entity when it is in the pair.
        narrowed = [row for row in candidates if int(row["id"]) != int(freshest["id"])]
        if narrowed:
            candidates = narrowed
    chosen = _freshest_of(candidates)
    if chosen is None or counted == 0:
        return empty
    return {
        "contradiction_count": counted,
        "conflict_assertion_id": int(chosen["id"]),
        "conflict_marker": str(chosen.get("claim") or "")[:200],
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
    description = str(row.get("description") or "")
    if description.strip():
        summary_field = "description"
        summary_text = description
    else:
        summary_field = "name"
        summary_text = str(row.get("name") or "")
    summary, truncated, _summary_field_ignored = _clip_summary(summary_text)
    revision = read_live_entity_revision(conn, entity_id)
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
        "summary_source": {"field": summary_field, "revision": revision},
        "summary_as_of": row.get("updated_at"),
        "updated_at": row.get("updated_at"),
        "revision": revision,
        "content_hash": row.get("content_hash"),
        "freshest_assertion_id": int(freshest["id"]) if freshest else None,
        "confidence": freshest.get("confidence") if freshest else None,
        "review_status": freshest.get("review_status") if freshest else None,
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
