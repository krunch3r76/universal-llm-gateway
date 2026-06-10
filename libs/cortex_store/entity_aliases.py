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
from .trait_vocabulary import NON_LIVE_LIFECYCLE as _NON_LIVE_LIFECYCLE

# ``_NON_LIVE_LIFECYCLE`` (imported above) is the canonical non-live lifecycle
# set; a NULL lifecycle is the live default (Option-C trait backfill) and is
# treated as active. The SQL mirror below is table-qualified for the
# alias-index JOIN context.
_LIVE_LIFECYCLE_SQL = (
    "(entities.lifecycle IS NULL"
    " OR entities.lifecycle NOT IN ('merged','deprecated','reaped'))"
)


def _is_missing_alias_table(exc: sqlite3.OperationalError) -> bool:
    return "no such table: entity_aliases" in str(exc)


@dataclass(frozen=True)
class ResolvedEntityRef:
    """Canonical entity reference plus optional alias provenance."""

    entity_id: str
    resolved_alias: dict[str, str] | None = None


@dataclass(frozen=True)
class AliasRebuildReport:
    """Result of an alias-index rebuild.

    ``row_count`` is the number of rows inserted into ``entity_aliases``.
    ``residual_collisions`` carries the cross-entity collisions that survived
    deterministic first-wins selection, as structured data the caller can log
    or surface (replaces the previously swallowed per-collision warning).
    """

    row_count: int
    residual_collisions: list[dict[str, object]]


def live_alias_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return ``(entity_id, entity_type, alias)`` rows for live entities only.

    Excludes entities whose ``lifecycle`` is ``merged``, ``deprecated``, or
    ``reaped``.  NULL ``lifecycle`` is the active default and is included.
    Used by migration 056 backfill and migration 057 rebuild.
    """
    return [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in conn.execute(
            f"""
            SELECT entities.id, entities.type, json_each.value
            FROM entities,
                 json_each(
                     CASE
                         WHEN json_valid(entities.aliases) THEN entities.aliases
                         ELSE '[]'
                     END
                 )
            WHERE entities.aliases IS NOT NULL
              AND json_each.type = 'text'
              AND {_LIVE_LIFECYCLE_SQL}
            """
        ).fetchall()
    ]


def _duplicate_aliases(
    rows: list[tuple[str, str, str]],
) -> list[dict[str, object]]:
    by_type_alias: dict[tuple[str, str], list[str]] = {}
    by_entity_alias: dict[tuple[str, str], int] = {}
    duplicates: list[dict[str, object]] = []

    for entity_id, entity_type, alias in rows:
        by_type_alias.setdefault((entity_type, alias), []).append(entity_id)
        key = (entity_id, alias)
        by_entity_alias[key] = by_entity_alias.get(key, 0) + 1

    for (entity_type, alias), entity_ids in sorted(by_type_alias.items()):
        unique_ids = sorted(set(entity_ids))
        if len(unique_ids) > 1:
            duplicates.append(
                {
                    "kind": "cross_entity",
                    "entity_type": entity_type,
                    "alias": alias,
                    "entity_ids": unique_ids,
                }
            )
    for (entity_id, alias), count in sorted(by_entity_alias.items()):
        if count > 1:
            duplicates.append(
                {
                    "kind": "within_entity",
                    "entity_id": entity_id,
                    "alias": alias,
                    "count": count,
                }
            )
    return duplicates


def _rows_for_backfill(
    rows: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[dict[str, object]]]:
    """Select the rows to insert, applying deterministic first-wins on collisions.

    Returns ``(selected_rows, residual_collisions)``.  Within-entity duplicate
    aliases are a hard error (raises ``RuntimeError``).  Cross-entity collisions
    on ``(entity_type, alias)`` are resolved by keeping the lexicographically
    smallest ``entity_id`` (stable first-wins) and the dropped peers are
    returned as structured ``residual_collisions`` for the caller to log or
    surface — not silently discarded.
    """
    duplicates = _duplicate_aliases(rows)
    within = [d for d in duplicates if d["kind"] == "within_entity"]
    if within:
        raise RuntimeError(
            "Cannot create entity_aliases until within-entity alias "
            f"collisions are resolved: {within[:20]}"
        )

    by_pair: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for row in rows:
        by_pair.setdefault((row[1], row[2]), []).append(row)

    selected: list[tuple[str, str, str]] = []
    collisions: list[dict[str, object]] = []
    for (entity_type, alias), group in sorted(by_pair.items()):
        entity_ids = sorted({row[0] for row in group})
        if len(entity_ids) > 1:
            winner = min(group, key=lambda row: row[0])
            selected.append(winner)
            collisions.append(
                {
                    "entity_type": entity_type,
                    "alias": alias,
                    "kept": winner[0],
                    "dropped": [eid for eid in entity_ids if eid != winner[0]],
                }
            )
        else:
            selected.extend(group)
    return selected, collisions


def rebuild_entity_aliases(conn: sqlite3.Connection) -> AliasRebuildReport:
    """Rebuild ``entity_aliases`` from live entities; idempotent (DELETE + filtered reinsert).

    Returns an :class:`AliasRebuildReport` carrying the inserted row count and
    any residual cross-entity collisions resolved by first-wins.  Assumes the
    ``entity_aliases`` table already exists (created by migration 056).
    """
    rows, collisions = _rows_for_backfill(live_alias_rows(conn))
    conn.execute("DELETE FROM entity_aliases")
    conn.executemany(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, ?, ?)",
        rows,
    )
    return AliasRebuildReport(row_count=len(rows), residual_collisions=collisions)


def sync_entity_aliases(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    aliases: list[str] | None,
    lifecycle: str | None = None,
) -> None:
    """Replace the normalized alias rows for one entity.

    Non-live entities (``lifecycle`` in ``_NON_LIVE_LIFECYCLE``) have their
    rows cleared and no new rows are inserted, preventing tombstones from
    holding alias slots after a lifecycle transition.
    """
    try:
        conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (entity_id,))
        if not aliases or lifecycle in _NON_LIVE_LIFECYCLE:
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
