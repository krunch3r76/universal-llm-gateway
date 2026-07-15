"""Staging apply helpers — relationship/add path."""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException, status

from ..db import query


def apply_relationship_add(
    conn: sqlite3.Connection,
    pj: dict,
    *,
    chunk_id: int | None,
    now: str,
) -> str:
    """Insert a relationship row from staged proposal_json."""
    source_id = pj.get("source_id") or pj.get("from_entity")
    target_id = pj.get("target_id") or pj.get("to_entity")
    type_id = pj.get("type_id") or pj.get("type")
    if not source_id or not target_id or not type_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "relationship/add requires source_id, target_id, and type_id",
        )

    from_entity, to_entity = source_id, target_id
    try:
        cur = conn.execute(
            "INSERT INTO relationships "
            "(type, from_entity, to_entity, role, strength, evidence, "
            " chunk_id, valid_from, valid_until, source_uri, "
            " session_id, agent, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                type_id,
                from_entity,
                to_entity,
                pj.get("role"),
                pj.get("strength") if pj.get("strength") is not None else 1.0,
                pj.get("evidence"),
                chunk_id if chunk_id is not None else pj.get("chunk_id"),
                pj.get("valid_from"),
                pj.get("valid_until"),
                pj.get("source_uri"),
                pj.get("session_id"),
                pj.get("agent"),
                now,
                now,
            ),
        )
        return str(cur.lastrowid)
    except sqlite3.IntegrityError:
        rows = query(
            conn,
            "SELECT id FROM relationships "
            "WHERE from_entity = ? AND to_entity = ? AND type = ? AND active = 1",
            (from_entity, to_entity, type_id),
        )
        if not rows:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Relationship insert failed without existing row",
            ) from None
        return str(rows[0]["id"])
