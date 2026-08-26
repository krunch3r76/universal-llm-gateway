"""Registry of every cortex table/column holding entity ids.

Used by entity_rekey / entity_merge to rewrite references atomically and to
audit schema coverage so new id-bearing columns fail tests instead of silently
corrupting a rekey.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

MergeStrategy = Literal[
    "repoint",
    "repoint_node",
    "rewrite_json_entity_ids",
    "rewrite_evidence_uris",
    "dedup_assertions",
    "dedup_relationships",
    "dedup_aliases",
    "dedup_tags",
    "dedup_surface_forms",
    "merge_access_summary",
    "drop_salience_recompute",
    "drop_event_chain_member",
    "repoint_root_event",
]

ReferenceKind = Literal["fk", "functional", "fts", "json"]


@dataclass(frozen=True)
class EntityIdReference:
    table: str
    column: str
    kind: ReferenceKind
    merge_strategy: MergeStrategy
    unique_indexes: tuple[str, ...] = ()


_ENTITY_ID_REFERENCES: tuple[EntityIdReference, ...] = (
    EntityIdReference(
        "assertions",
        "entity_id",
        "fk",
        "dedup_assertions",
        ("idx_assertions_claim_dedup",),
    ),
    EntityIdReference(
        "relationships",
        "from_entity",
        "fk",
        "dedup_relationships",
        ("idx_relationships_active_dedup",),
    ),
    EntityIdReference(
        "relationships",
        "to_entity",
        "fk",
        "dedup_relationships",
        ("idx_relationships_active_dedup",),
    ),
    EntityIdReference(
        "entity_aliases",
        "entity_id",
        "fk",
        "dedup_aliases",
        ("sqlite_autoindex_entity_aliases_1",),
    ),
    EntityIdReference(
        "surface_forms",
        "entity_id",
        "fk",
        "dedup_surface_forms",
        (),
    ),
    EntityIdReference(
        "tag_assignments",
        "entity_id",
        "fk",
        "dedup_tags",
        ("sqlite_autoindex_tag_assignments_1",),
    ),
    EntityIdReference(
        "entity_salience_cache",
        "entity_id",
        "fk",
        "drop_salience_recompute",
        ("sqlite_autoindex_entity_salience_cache_1",),
    ),
    EntityIdReference(
        "event_chain_members",
        "event_id",
        "fk",
        "drop_event_chain_member",
        ("sqlite_autoindex_event_chain_members_1",),
    ),
    EntityIdReference(
        "event_chains",
        "root_event_id",
        "fk",
        "repoint_root_event",
        (),
    ),
    EntityIdReference(
        "entity_access_log",
        "entity_id",
        "functional",
        "repoint",
        (),
    ),
    EntityIdReference(
        "entity_access_summary",
        "entity_id",
        "functional",
        "merge_access_summary",
        ("sqlite_autoindex_entity_access_summary_1",),
    ),
    EntityIdReference(
        "session_edges",
        "from_node",
        "functional",
        "repoint_node",
        (),
    ),
    EntityIdReference(
        "session_edges",
        "to_node",
        "functional",
        "repoint_node",
        (),
    ),
    EntityIdReference(
        "journal_links",
        "to_entity",
        "functional",
        "repoint",
        (),
    ),
    EntityIdReference(
        "assertions_fts",
        "entity_id",
        "fts",
        "repoint",
        (),
    ),
    EntityIdReference(
        "session_journals",
        "entity_ids",
        "json",
        "rewrite_json_entity_ids",
        (),
    ),
    EntityIdReference(
        "assertions",
        "evidence_uris",
        "json",
        "rewrite_evidence_uris",
        (),
    ),
)


def entity_id_references() -> tuple[EntityIdReference, ...]:
    return _ENTITY_ID_REFERENCES


def _registered_columns() -> set[tuple[str, str]]:
    return {(r.table, r.column) for r in _ENTITY_ID_REFERENCES}


def _fk_columns_referencing_entities(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        for fk in conn.execute(f"PRAGMA foreign_key_list('{table}')"):
            if fk[2] == "entities":
                found.add((table, fk[3]))
    return found


def _unique_indexes_on_column(
    conn: sqlite3.Connection, table: str, column: str
) -> list[str]:
    names: list[str] = []
    for idx in conn.execute(f"PRAGMA index_list('{table}')"):
        idx_name = idx[1]
        is_unique = bool(idx[2])
        if not is_unique and not idx_name.startswith("sqlite_autoindex"):
            continue
        cols = [c[2] for c in conn.execute(f"PRAGMA index_info('{idx_name}')")]
        if column in cols:
            names.append(idx_name)
    return names


def audit_entity_id_registry_coverage(conn: sqlite3.Connection) -> list[str]:
    """Return human-readable errors when registry coverage is incomplete."""
    errors: list[str] = []
    registered = _registered_columns()
    fk_cols = _fk_columns_referencing_entities(conn)
    missing_fk = fk_cols - registered
    if missing_fk:
        errors.append(f"FK columns missing from registry: {sorted(missing_fk)}")

    functional_expected = {
        (r.table, r.column) for r in _ENTITY_ID_REFERENCES if r.kind == "functional"
    }
    for table, column in functional_expected:
        if (table, column) not in registered:
            errors.append(f"functional ref not registered: {table}.{column}")

    for ref in _ENTITY_ID_REFERENCES:
        if not ref.unique_indexes:
            actual = _unique_indexes_on_column(conn, ref.table, ref.column)
            for idx_name in actual:
                if idx_name not in ref.unique_indexes:
                    errors.append(
                        f"{ref.table}.{ref.column} unique index {idx_name!r} "
                        f"has no merge_strategy via unique_indexes on registry row"
                    )
        else:
            actual = set(_unique_indexes_on_column(conn, ref.table, ref.column))
            for declared in ref.unique_indexes:
                if declared.startswith("sqlite_autoindex"):
                    continue
                if declared not in actual:
                    errors.append(
                        f"registry declares index {declared!r} on "
                        f"{ref.table}.{ref.column} but schema lacks it"
                    )
    return errors
