"""Entity alias index helpers.

Aliases remain stored on ``entities.aliases`` for the public entity payload.
This module maintains the normalized lookup table used for uniqueness checks
and relationship alias resolution.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import HTTPException, status

from .db import query


def _is_missing_alias_table(exc: sqlite3.OperationalError) -> bool:
    return "no such table: entity_aliases" in str(exc)


@dataclass(frozen=True)
class ResolvedEntityRef:
    """Canonical entity reference plus optional alias provenance."""

    entity_id: str
    resolved_alias: dict[str, str] | None = None


def sync_entity_aliases(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    aliases: list[str] | None,
) -> None:
    """Replace the normalized alias rows for one entity."""
    try:
        conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (entity_id,))
        if not aliases:
            return
        rows = [(entity_id, entity_type, alias) for alias in aliases]
        conn.executemany(
            "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, ?, ?)",
            rows,
        )
    except sqlite3.OperationalError as exc:
        if _is_missing_alias_table(exc):
            return
        raise


def resolve_entity_reference(
    conn: sqlite3.Connection,
    ref: str,
    *,
    resolve_aliases: bool,
    label: str,
) -> ResolvedEntityRef:
    """Return the canonical entity ID for *ref* or raise a precise 4xx error."""
    if query(conn, "SELECT id FROM entities WHERE id = ?", (ref,)):
        return ResolvedEntityRef(entity_id=ref)
    if not resolve_aliases:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{label.title()} entity not found: {ref}",
        )

    alias_candidates = [ref]
    type_hint: str | None = None
    if ":" in ref:
        type_hint, alias_part = ref.split(":", 1)
        if alias_part:
            alias_candidates.append(alias_part)

    clauses = ["alias IN (" + ", ".join("?" for _ in alias_candidates) + ")"]
    params: list[object] = list(alias_candidates)
    if type_hint:
        clauses.append("entity_type = ?")
        params.append(type_hint)

    try:
        rows = query(
            conn,
            "SELECT entity_id, entity_type, alias FROM entity_aliases "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY entity_type, entity_id",
            tuple(params),
        )
    except sqlite3.OperationalError as exc:
        if not _is_missing_alias_table(exc):
            raise
        rows = []
    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{label.title()} entity not found: {ref}",
        )
    entity_ids = {str(row["entity_id"]) for row in rows}
    if len(entity_ids) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "detail": f"Ambiguous {label} entity alias: {ref}",
                "matches": rows,
            },
        )
    row = rows[0]
    return ResolvedEntityRef(
        entity_id=str(row["entity_id"]),
        resolved_alias={
            "input": ref,
            "entity_id": str(row["entity_id"]),
            "entity_type": str(row["entity_type"]),
            "alias": str(row["alias"]),
        },
    )
