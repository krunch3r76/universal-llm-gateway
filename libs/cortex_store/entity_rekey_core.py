"""Core entity-id rewrite engine shared by rekey and merge."""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any

from fastapi import HTTPException, status

from .db import json_encode, query
from .enrichment import reindex_assertions_fts_batch
from .entity_aliases import sync_entity_aliases
from .entity_id_norm import canonicalize_entity_id
from .entity_id_registry import _ENTITY_ID_REFERENCES
from .salience import compute_all_salience
from .type_taxonomy import MATTER_SPECIES

_NOW_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> str:
    return datetime.datetime.now(tz=datetime.UTC).strftime(_NOW_FMT)


def _parse_attributes(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid entity attributes JSON: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Entity attributes must be a JSON object",
        )
    return parsed


def _load_entity(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Entity not found: {entity_id}")
    return rows[0]


def _preflight_new_id_available(
    conn: sqlite3.Connection, new_id: str, entity_type: str, old_id: str
) -> None:
    if query(conn, "SELECT id FROM entities WHERE id = ?", (new_id,)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "detail": f"Entity already exists: {new_id}",
                "hint": "Use entity_merge to fold into an existing id",
            },
        )
    alias_rows = query(
        conn,
        "SELECT entity_id FROM entity_aliases WHERE entity_type = ? AND alias = ?",
        (entity_type, new_id),
    )
    for row in alias_rows:
        if str(row["entity_id"]) != old_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "detail": f"new_id {new_id!r} is already an alias of "
                    f"{row['entity_id']!r}",
                },
            )


def begin_identity_txn(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA defer_foreign_keys=ON")


def check_foreign_keys_global(conn: sqlite3.Connection) -> None:
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            {
                "detail": "foreign_key_check failed",
                "violations": [tuple(v) for v in violations],
            },
        )


def check_foreign_keys(conn: sqlite3.Connection) -> None:
    tables = sorted({ref.table for ref in _ENTITY_ID_REFERENCES})
    violations: list[tuple[Any, ...]] = []
    for table in tables:
        violations.extend(
            tuple(row) for row in conn.execute(f"PRAGMA foreign_key_check({table})")
        )
    if violations:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            {
                "detail": "foreign_key_check failed",
                "violations": violations,
            },
        )


def rewrite_simple_column(
    conn: sqlite3.Connection, table: str, column: str, old_id: str, new_id: str
) -> None:
    conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
        (new_id, old_id),
    )


def rewrite_session_edge_nodes(
    conn: sqlite3.Connection, old_id: str, new_id: str
) -> None:
    conn.execute(
        "UPDATE session_edges SET from_node = ? WHERE from_node = ?",
        (new_id, old_id),
    )
    conn.execute(
        "UPDATE session_edges SET to_node = ? WHERE to_node = ?",
        (new_id, old_id),
    )


def rewrite_session_journal_entity_ids(
    conn: sqlite3.Connection, old_id: str, new_id: str
) -> None:
    rows = conn.execute(
        "SELECT id, entity_ids FROM session_journals WHERE entity_ids IS NOT NULL"
    ).fetchall()
    for row in rows:
        raw = row[1]
        try:
            ids = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"session_journals.id={row[0]} entity_ids is not valid JSON: {exc}",
            ) from exc
        if not isinstance(ids, list):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"session_journals.id={row[0]} entity_ids must be a JSON array",
            )
        if old_id not in ids:
            continue
        updated = [new_id if item == old_id else item for item in ids]
        conn.execute(
            "UPDATE session_journals SET entity_ids = ? WHERE id = ?",
            (json.dumps(updated), row[0]),
        )


def _rewrite_evidence_token(token: str, old_id: str, new_id: str) -> str:
    if token == old_id:
        return new_id
    for prefix in ("cortex:", "entity:"):
        if token == f"{prefix}{old_id}":
            return f"{prefix}{new_id}"
    return token


def rewrite_assertion_evidence_uris(
    conn: sqlite3.Connection, old_id: str, new_id: str
) -> None:
    rows = conn.execute(
        "SELECT id, evidence_uris FROM assertions WHERE evidence_uris IS NOT NULL"
    ).fetchall()
    for row in rows:
        raw = row[1]
        try:
            uris = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"assertions.id={row[0]} evidence_uris is not valid JSON: {exc}",
            ) from exc
        if not isinstance(uris, list):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"assertions.id={row[0]} evidence_uris must be a JSON array",
            )
        changed = False
        rewritten: list[str] = []
        for item in uris:
            if not isinstance(item, str):
                rewritten.append(item)
                continue
            new_item = _rewrite_evidence_token(item, old_id, new_id)
            changed = changed or new_item != item
            rewritten.append(new_item)
        if changed:
            conn.execute(
                "UPDATE assertions SET evidence_uris = ? WHERE id = ?",
                (json.dumps(rewritten), row[0]),
            )


def assertion_ids_for_entity(conn: sqlite3.Connection, entity_id: str) -> list[int]:
    return [
        int(row["id"])
        for row in query(
            conn, "SELECT id FROM assertions WHERE entity_id = ?", (entity_id,)
        )
    ]


def rekey_child_surfaces(
    conn: sqlite3.Connection, old_id: str, new_id: str
) -> list[int]:
    """Repoint every registered child surface for a pure relabel."""
    rewrite_simple_column(conn, "assertions", "entity_id", old_id, new_id)
    rewrite_simple_column(conn, "relationships", "from_entity", old_id, new_id)
    rewrite_simple_column(conn, "relationships", "to_entity", old_id, new_id)
    rewrite_simple_column(conn, "entity_aliases", "entity_id", old_id, new_id)
    rewrite_simple_column(conn, "surface_forms", "entity_id", old_id, new_id)
    rewrite_simple_column(conn, "tag_assignments", "entity_id", old_id, new_id)
    rewrite_simple_column(conn, "entity_access_log", "entity_id", old_id, new_id)
    rewrite_simple_column(conn, "entity_access_summary", "entity_id", old_id, new_id)
    rewrite_simple_column(conn, "assertions_fts", "entity_id", old_id, new_id)
    rewrite_simple_column(conn, "event_chain_members", "event_id", old_id, new_id)
    rewrite_simple_column(conn, "event_chains", "root_event_id", old_id, new_id)
    rewrite_simple_column(conn, "journal_links", "to_entity", old_id, new_id)
    rewrite_session_edge_nodes(conn, old_id, new_id)
    rewrite_session_journal_entity_ids(conn, old_id, new_id)
    rewrite_assertion_evidence_uris(conn, old_id, new_id)
    return assertion_ids_for_entity(conn, new_id)


def drop_salience_cache(conn: sqlite3.Connection, entity_id: str) -> None:
    conn.execute("DELETE FROM entity_salience_cache WHERE entity_id = ?", (entity_id,))


def recompute_salience_after_commit(conn: sqlite3.Connection, entity_id: str) -> None:
    """Salience recompute may commit — call only after the identity txn commits."""
    compute_all_salience(conn, entity_id_filter=entity_id, force=True)


def seed_alias_and_sync(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    aliases: list[str] | None,
    lifecycle: str | None,
) -> None:
    sync_entity_aliases(
        conn,
        entity_id=entity_id,
        entity_type=entity_type,
        aliases=aliases,
        lifecycle=lifecycle,
    )


def entity_rekey_impl(
    conn: sqlite3.Connection,
    old_id: str,
    new_id: str,
) -> dict[str, Any]:
    old_row = _load_entity(conn, old_id)
    entity_type = str(old_row["type"])
    canonical_new = canonicalize_entity_id(new_id, entity_type)
    _preflight_new_id_available(conn, canonical_new, entity_type, old_id)

    now = _utc_now()

    begin_identity_txn(conn)
    try:
        assertion_ids = rekey_child_surfaces(conn, old_id, canonical_new)
        drop_salience_cache(conn, old_id)

        alias_list = _existing_alias_list(old_row, old_id)

        conn.execute(
            "UPDATE entities SET id = ?, aliases = ?, updated_at = ? WHERE id = ?",
            (canonical_new, json_encode(alias_list), now, old_id),
        )
        seed_alias_and_sync(
            conn,
            entity_id=canonical_new,
            entity_type=entity_type,
            aliases=alias_list,
            lifecycle=old_row.get("lifecycle"),
        )
        if assertion_ids:
            reindex_assertions_fts_batch(conn, assertion_ids)
        check_foreign_keys(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    recompute_salience_after_commit(conn, canonical_new)
    _emit_rekeyed(old_id=old_id, new_id=canonical_new)

    return {
        "old_id": old_id,
        "new_id": canonical_new,
        "assertion_ids_reindexed": assertion_ids,
    }


def _emit_rekeyed(*, old_id: str, new_id: str) -> None:
    from .dispatch_ops._shared import record

    record("cortex.entity.rekeyed", old_id=old_id, new_id=new_id)


def _existing_alias_list(old_row: dict[str, Any], old_id: str) -> list[str]:
    """Parse the entity's stored alias list and ensure old_id is retained."""
    aliases_raw = old_row.get("aliases")
    alias_list: list[str] = []
    if aliases_raw:
        try:
            parsed_aliases = json.loads(str(aliases_raw))
            if isinstance(parsed_aliases, list):
                alias_list = [str(a) for a in parsed_aliases]
        except (json.JSONDecodeError, TypeError):
            alias_list = []
    if old_id not in alias_list:
        alias_list.append(old_id)
    return alias_list


def _register_cross_type_alias(
    conn: sqlite3.Connection, entity_id: str, old_type: str, old_id: str
) -> None:
    """Register the pre-retype compound id as an alias under its original type.

    ``sync_entity_aliases`` already inserted this alias under the NEW type (the
    UNIQUE key is ``(entity_id, alias)``); replace that row so the alias is
    registered under the OLD type, which is the prefix resolve_entity_reference
    scopes on. Idempotent across re-runs.
    """
    try:
        conn.execute(
            "DELETE FROM entity_aliases WHERE entity_id = ? AND alias = ?",
            (entity_id, old_id),
        )
        conn.execute(
            "INSERT INTO entity_aliases (entity_id, entity_type, alias) "
            "VALUES (?, ?, ?)",
            (entity_id, old_type, old_id),
        )
    except sqlite3.OperationalError as exc:
        if "no such table: entity_aliases" in str(exc):
            return
        raise


def entity_retype_impl(
    conn: sqlite3.Connection,
    entity_id: str,
    new_type: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Change an entity's type, re-prefixing its id under the new type.

    Mirrors ``entity_rekey_impl`` but also rewrites the ``type`` column. The
    slug is preserved (``agent_skill:foo`` -> ``rule:foo``); the prior compound
    id is retained as a cross-type alias so existing references still resolve.
    """
    old_row = _load_entity(conn, entity_id)
    old_type = str(old_row["type"])
    if not new_type:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "new_type is required"
        )
    if new_type == old_type:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "detail": f"Entity {entity_id!r} is already type {new_type!r}",
                "hint": "entity_retype changes the type; use entity_rekey for a "
                "same-type slug change.",
            },
        )
    if not force and old_type in MATTER_SPECIES and new_type != old_type:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "error": "matter_genus_retype_blocked",
                "entity_type": old_type,
                "hint": "genus is immutable at birth (matter-playbook §4.0); "
                "use entity_merge for duplicate handles or force=true for "
                "deliberate remediation",
            },
        )
    old_id = str(old_row["id"])
    slug = old_id.split(":", 1)[-1] if ":" in old_id else old_id
    canonical_new = canonicalize_entity_id(slug, new_type)
    _preflight_new_id_available(conn, canonical_new, new_type, old_id)

    now = _utc_now()

    begin_identity_txn(conn)
    try:
        assertion_ids = rekey_child_surfaces(conn, old_id, canonical_new)
        drop_salience_cache(conn, old_id)

        alias_list = _existing_alias_list(old_row, old_id)

        conn.execute(
            "UPDATE entities SET id = ?, type = ?, aliases = ?, updated_at = ? "
            "WHERE id = ?",
            (canonical_new, new_type, json_encode(alias_list), now, old_id),
        )
        seed_alias_and_sync(
            conn,
            entity_id=canonical_new,
            entity_type=new_type,
            aliases=alias_list,
            lifecycle=old_row.get("lifecycle"),
        )
        # sync_entity_aliases registers aliases under the NEW type only; the
        # prior compound id carries the OLD type prefix and resolve_entity_reference
        # scopes alias lookups by that prefix, so register the old id as a
        # cross-type alias under the old type as well.
        _register_cross_type_alias(conn, canonical_new, old_type, old_id)
        if assertion_ids:
            reindex_assertions_fts_batch(conn, assertion_ids)
        check_foreign_keys(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    recompute_salience_after_commit(conn, canonical_new)
    _emit_retyped(
        old_id=old_id, new_id=canonical_new, old_type=old_type, new_type=new_type
    )

    return {
        "old_id": old_id,
        "new_id": canonical_new,
        "old_type": old_type,
        "new_type": new_type,
        "assertion_ids_reindexed": assertion_ids,
    }


def _emit_retyped(*, old_id: str, new_id: str, old_type: str, new_type: str) -> None:
    from .dispatch_ops._shared import record

    record(
        "cortex.entity.retyped",
        old_id=old_id,
        new_id=new_id,
        old_type=old_type,
        new_type=new_type,
    )
