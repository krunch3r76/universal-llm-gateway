"""Relationship-graph detectors: dangling targets."""

from __future__ import annotations

from typing import Any

from ...db import query
from ._shared import _finding


def detect_dangling_relationship_target(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Relationships pointing to non-existent entities (uses from_entity/to_entity per schema)."""
    sql = """
        SELECT r.id as rel_id, r.from_entity, r.to_entity, r.type
        FROM relationships r
        LEFT JOIN entities e ON e.id = r.to_entity
        WHERE e.id IS NULL AND r.active = 1
    """
    params: tuple = ()
    if subject:
        sql += " AND (r.from_entity = ? OR r.to_entity = ?)"
        params = (subject, subject)
    rows = query(conn, sql, params)
    return [
        _finding(
            "dangling_relationship_target",
            r.get("to_entity") or "unknown",
            f"Relationship {r.get('rel_id')} from {r.get('from_entity')} targets non-existent {r.get('to_entity')}",
        )
        for r in rows
    ]


__all__ = ["detect_dangling_relationship_target"]
