"""Entity alias index helpers.

Aliases remain stored on ``entities.aliases`` for the public entity payload.
This module maintains the normalized lookup table used for uniqueness checks
and relationship alias resolution.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

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

    Includes ``entities.name`` (primary display label) unioned with JSON
    ``aliases``.  Excludes entities whose ``lifecycle`` is ``merged``,
    ``deprecated``, or ``reaped``.  NULL ``lifecycle`` is the active default.
    """
    alias_rows = [
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
    name_rows: list[tuple[str, str, str]] = []
    try:
        name_rows = [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in conn.execute(
                f"""
                SELECT entities.id, entities.type, entities.name
                FROM entities
                WHERE entities.name IS NOT NULL
                  AND trim(entities.name) != ''
                  AND {_LIVE_LIFECYCLE_SQL}
                """
            ).fetchall()
        ]
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc):
            raise
    return alias_rows + name_rows


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
    *,
    resolve_cross_entity_collisions: bool = False,
) -> tuple[list[tuple[str, str, str]], list[dict[str, object]]]:
    """Select rows to insert; hard-error on within-entity duplicate aliases.

    When ``resolve_cross_entity_collisions`` is True (migration 056 backfill
    while ``UNIQUE (entity_type, alias)`` still holds), cross-entity collisions
    resolve by lexicographically smallest ``entity_id`` (first-wins).  After
    migration 075 the default keeps all rows for ambiguous lookup at read time.
    """
    duplicates = _duplicate_aliases(rows)
    within = [d for d in duplicates if d["kind"] == "within_entity"]
    if within:
        raise RuntimeError(
            "Cannot create entity_aliases until within-entity alias "
            f"collisions are resolved: {within[:20]}"
        )

    cross_collisions = [d for d in duplicates if d["kind"] == "cross_entity"]
    if resolve_cross_entity_collisions:
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

    seen: set[tuple[str, str]] = set()
    selected = []
    for row in rows:
        key = (row[0], row[2])
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected, cross_collisions


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


def _indexed_aliases(
    name: str | None,
    aliases: list[str] | None,
) -> list[str]:
    """Build ``[name] ∪ aliases`` with name first and within-list dedup."""
    indexed: list[str] = []
    if name and str(name).strip():
        indexed.append(str(name).strip())
    if aliases:
        for alias in aliases:
            text = str(alias).strip()
            if text and text not in indexed:
                indexed.append(text)
    return indexed


def sync_entity_aliases(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    name: str | None = None,
    aliases: list[str] | None = None,
    lifecycle: str | None = None,
) -> None:
    """Replace the normalized alias rows for one entity.

    Indexes ``[name] ∪ aliases`` (name first).  Non-live entities have their
    rows cleared and no new rows are inserted.
    """
    try:
        conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (entity_id,))
        indexed = _indexed_aliases(name, aliases)
        if not indexed or lifecycle in _NON_LIVE_LIFECYCLE:
            return
        rows = [(entity_id, entity_type, alias) for alias in indexed]
        conn.executemany(
            "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, ?, ?)",
            rows,
        )
    except sqlite3.OperationalError as exc:
        if _is_missing_alias_table(exc):
            return
        raise


def _merged_into_target(attributes_raw: str | None) -> str | None:
    if not attributes_raw:
        return None
    try:
        attrs: dict[str, Any] = json.loads(attributes_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    merged_into = attrs.get("merged_into")
    return str(merged_into) if merged_into else None


def resolve_entity_reference(
    conn: sqlite3.Connection,
    ref: str,
    *,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    label: str = "entity",
) -> ResolvedEntityRef:
    """Return the canonical entity ID for *ref* or raise a precise 4xx error.

    Resolution order (unless ``raw_id``):
      1. exact active entity row
      2. exact merged tombstone → ``merged_into`` redirect
      3. alias lookup (when ``resolve_aliases``)
    """
    rows = query(
        conn,
        "SELECT id, lifecycle, attributes FROM entities WHERE id = ?",
        (ref,),
    )
    if rows:
        row = rows[0]
        if raw_id:
            return ResolvedEntityRef(entity_id=str(row["id"]))
        lifecycle = row.get("lifecycle")
        if lifecycle == "merged":
            target = _merged_into_target(
                str(row["attributes"]) if row.get("attributes") else None
            )
            if target:
                return ResolvedEntityRef(entity_id=target)
        return ResolvedEntityRef(entity_id=str(row["id"]))

    if raw_id or not resolve_aliases:
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
        from .event_publisher import cortex_entity_alias_ambiguous

        cortex_entity_alias_ambiguous(
            ref=ref,
            entity_type=type_hint or str(rows[0]["entity_type"]),
            match_count=len(entity_ids),
        )
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
