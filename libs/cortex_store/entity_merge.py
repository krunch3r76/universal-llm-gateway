"""entity_merge — fold source entity into target with dedup-before-repoint."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException, status

from .claim_hash import compute_claim_hash
from .db import json_encode, query
from .dispatch_ops._shared import record
from .enrichment import reindex_assertions_fts_batch
from .entity_aliases import sync_entity_aliases
from .entity_rekey_core import (
    _load_entity,
    _parse_attributes,
    _utc_now,
    assertion_ids_for_entity,
    begin_identity_txn,
    check_foreign_keys,
    drop_salience_cache,
    recompute_salience_after_commit,
    rewrite_assertion_evidence_uris,
    rewrite_session_edge_nodes,
    rewrite_session_journal_entity_ids,
    rewrite_simple_column,
)


def _preflight_merge(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if source_id == target_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "source_id and target_id must differ",
        )
    source = _load_entity(conn, source_id)
    target = _load_entity(conn, target_id)
    if str(source["type"]) != str(target["type"]):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "detail": "Cross-type merge is not supported",
                "source_type": source["type"],
                "target_type": target["type"],
            },
        )
    return source, target


def _merge_assertions(
    conn: sqlite3.Connection, source_id: str, target_id: str, now: str
) -> set[int]:
    affected: set[int] = set()
    target_by_hash = {
        str(row["claim_hash"]): int(row["id"])
        for row in query(
            conn,
            "SELECT id, claim_hash FROM assertions "
            "WHERE entity_id = ? AND superseded_by IS NULL AND claim_hash IS NOT NULL",
            (target_id,),
        )
    }
    source_rows = query(
        conn,
        "SELECT id, claim, claim_hash FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL",
        (source_id,),
    )
    for row in source_rows:
        aid = int(row["id"])
        projected_hash = compute_claim_hash(target_id, str(row["claim"]))
        if projected_hash in target_by_hash:
            conn.execute(
                "UPDATE assertions SET superseded_by = ?, updated_at = ? WHERE id = ?",
                (target_by_hash[projected_hash], now, aid),
            )
            affected.add(aid)
    survivors = query(
        conn,
        "SELECT id, claim FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL",
        (source_id,),
    )
    for row in survivors:
        aid = int(row["id"])
        new_hash = compute_claim_hash(target_id, str(row["claim"]))
        conn.execute(
            "UPDATE assertions SET entity_id = ?, claim_hash = ?, updated_at = ? "
            "WHERE id = ?",
            (target_id, new_hash, now, aid),
        )
    for aid in assertion_ids_for_entity(conn, target_id):
        affected.add(aid)
    return affected


def _merge_relationships(
    conn: sqlite3.Connection, source_id: str, target_id: str, now: str
) -> None:
    target_keys = {
        (str(r["from_entity"]), str(r["to_entity"]), str(r["type"])): int(r["id"])
        for r in query(
            conn,
            "SELECT id, from_entity, to_entity, type FROM relationships "
            "WHERE active = 1 AND (from_entity = ? OR to_entity = ?)",
            (target_id, target_id),
        )
    }
    source_rows = query(
        conn,
        "SELECT id, from_entity, to_entity, type FROM relationships "
        "WHERE active = 1 AND (from_entity = ? OR to_entity = ?)",
        (source_id, source_id),
    )
    for row in source_rows:
        rid = int(row["id"])
        new_from = target_id if row["from_entity"] == source_id else row["from_entity"]
        new_to = target_id if row["to_entity"] == source_id else row["to_entity"]
        if new_from == new_to:
            conn.execute(
                "UPDATE relationships SET active = 0, updated_at = ? WHERE id = ?",
                (now, rid),
            )
            continue
        key = (str(new_from), str(new_to), str(row["type"]))
        if key in target_keys and target_keys[key] != rid:
            conn.execute(
                "UPDATE relationships SET active = 0, updated_at = ? WHERE id = ?",
                (now, rid),
            )
            continue
        conn.execute(
            "UPDATE relationships SET from_entity = ?, to_entity = ?, updated_at = ? "
            "WHERE id = ?",
            (new_from, new_to, now, rid),
        )
        target_keys[key] = rid


def _merge_aliases(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
    conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (source_id,))


def _merge_tags(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
    target_tags = {
        str(row["tag_name"])
        for row in query(
            conn,
            "SELECT tag_name FROM tag_assignments WHERE entity_id = ?",
            (target_id,),
        )
    }
    source_rows = query(
        conn,
        "SELECT id, tag_name FROM tag_assignments WHERE entity_id = ?",
        (source_id,),
    )
    for row in source_rows:
        if str(row["tag_name"]) in target_tags:
            conn.execute("DELETE FROM tag_assignments WHERE id = ?", (row["id"],))
        else:
            conn.execute(
                "UPDATE tag_assignments SET entity_id = ? WHERE id = ?",
                (target_id, row["id"]),
            )


def _merge_surface_forms(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> None:
    target_keys = {
        (str(r["mention"]), r.get("context_hash"))
        for r in query(
            conn,
            "SELECT mention, context_hash FROM surface_forms WHERE entity_id = ?",
            (target_id,),
        )
    }
    source_rows = query(
        conn,
        "SELECT id, mention, context_hash FROM surface_forms WHERE entity_id = ?",
        (source_id,),
    )
    for row in source_rows:
        key = (str(row["mention"]), row.get("context_hash"))
        if key in target_keys:
            conn.execute("DELETE FROM surface_forms WHERE id = ?", (row["id"],))
        else:
            conn.execute(
                "UPDATE surface_forms SET entity_id = ? WHERE id = ?",
                (target_id, row["id"]),
            )


def _merge_access_summary(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> None:
    source_rows = query(
        conn,
        "SELECT * FROM entity_access_summary WHERE entity_id = ?",
        (source_id,),
    )
    for row in source_rows:
        agent = row["agent"]
        week = row["week_start"]
        target_row = query(
            conn,
            "SELECT * FROM entity_access_summary "
            "WHERE entity_id = ? AND agent = ? AND week_start = ?",
            (target_id, agent, week),
        )
        if target_row:
            merged = target_row[0]
            conn.execute(
                "UPDATE entity_access_summary SET "
                "agent_access_count = ?, boot_access_count = ?, session_count = ? "
                "WHERE entity_id = ? AND agent = ? AND week_start = ?",
                (
                    int(merged.get("agent_access_count") or 0)
                    + int(row.get("agent_access_count") or 0),
                    int(merged.get("boot_access_count") or 0)
                    + int(row.get("boot_access_count") or 0),
                    int(merged.get("session_count") or 0)
                    + int(row.get("session_count") or 0),
                    target_id,
                    agent,
                    week,
                ),
            )
            conn.execute(
                "DELETE FROM entity_access_summary WHERE entity_id = ? AND agent = ? "
                "AND week_start = ?",
                (source_id, agent, week),
            )
        else:
            conn.execute(
                "UPDATE entity_access_summary SET entity_id = ? "
                "WHERE entity_id = ? AND agent = ? AND week_start = ?",
                (target_id, source_id, agent, week),
            )


def _merge_event_chain_members(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> None:
    conn.execute("DELETE FROM event_chain_members WHERE event_id = ?", (source_id,))
    rewrite_simple_column(conn, "event_chains", "root_event_id", source_id, target_id)


def _tombstone_source(
    conn: sqlite3.Connection,
    source: dict[str, Any],
    target_id: str,
    now: str,
) -> None:
    attrs = _parse_attributes(
        str(source["attributes"]) if source.get("attributes") else None
    )
    attrs["merged_into"] = target_id
    conn.execute(
        "UPDATE entities SET lifecycle = 'merged', attributes = ?, aliases = '[]', "
        "updated_at = ? WHERE id = ?",
        (json_encode(attrs), now, source["id"]),
    )
    sync_entity_aliases(
        conn,
        entity_id=str(source["id"]),
        entity_type=str(source["type"]),
        aliases=[],
        lifecycle="merged",
    )


def entity_merge_impl(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> dict[str, Any]:
    source, target = _preflight_merge(conn, source_id, target_id)
    now = _utc_now()

    begin_identity_txn(conn)
    try:
        affected_assertions = _merge_assertions(conn, source_id, target_id, now)
        _merge_relationships(conn, source_id, target_id, now)
        _merge_aliases(conn, source_id, target_id)
        _merge_tags(conn, source_id, target_id)
        _merge_surface_forms(conn, source_id, target_id)
        _merge_access_summary(conn, source_id, target_id)
        rewrite_simple_column(
            conn, "entity_access_log", "entity_id", source_id, target_id
        )
        rewrite_session_edge_nodes(conn, source_id, target_id)
        rewrite_simple_column(conn, "journal_links", "to_entity", source_id, target_id)
        _merge_event_chain_members(conn, source_id, target_id)
        rewrite_session_journal_entity_ids(conn, source_id, target_id)
        rewrite_assertion_evidence_uris(conn, source_id, target_id)

        drop_salience_cache(conn, source_id)
        drop_salience_cache(conn, target_id)
        _tombstone_source(conn, source, target_id, now)

        if affected_assertions:
            reindex_assertions_fts_batch(conn, sorted(affected_assertions))

        check_foreign_keys(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    recompute_salience_after_commit(conn, target_id)
    record(
        "cortex.entity.merged",
        source_id=source_id,
        target_id=target_id,
        entity_type=source["type"],
    )
    return {
        "source_id": source_id,
        "target_id": target_id,
        "assertion_ids_reindexed": sorted(affected_assertions),
    }
