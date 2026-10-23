"""entities_fts — migration, sync-on-write, search, and backfill idempotence (40072)."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from cortex_store.entity_crud import create_entity_impl, update_entity_impl
from cortex_store.entity_fts import (
    backfill_entities_fts_conn,
    entities_fts_drift,
    entities_fts_stats,
    reindex_entity_fts_conn,
    search_entities_fts,
)
from cortex_store.status_trait_write import write_entity_reaped


def _count_entities_fts(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM entities_fts").fetchone()
    return int(row[0]) if row else 0


def test_search_without_fts_table_does_not_scan_entities(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-absent-scan-guard",
            "type": "note",
            "name": "unique absent fts token xyzzy",
            "description": "must not appear without fts",
        },
        commit=True,
    )
    migrated_conn.execute("DROP TABLE IF EXISTS entities_fts")
    migrated_conn.commit()
    hits = search_entities_fts(
        migrated_conn,
        "unique absent fts token xyzzy",
        limit=5,
    )
    assert hits == []


def test_entity_write_and_fts_row_commit_together(
    migrated_conn: sqlite3.Connection,
) -> None:
    migrated_conn.execute("BEGIN IMMEDIATE")
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-sync-commit",
            "type": "note",
            "name": "fts sync commit witness",
            "description": "transactional index",
        },
        commit=False,
    )
    row = migrated_conn.execute(
        "SELECT entity_id FROM entities_fts WHERE entity_id = ?",
        ("note:fts-sync-commit",),
    ).fetchone()
    assert row is not None
    migrated_conn.commit()
    hits = search_entities_fts(migrated_conn, "fts sync commit witness", limit=3)
    assert any(h["entity_id"] == "note:fts-sync-commit" for h in hits)


def test_failed_entity_write_leaves_fts_unchanged(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-rollback-prior",
            "type": "note",
            "name": "rollback prior",
            "description": "stable",
        },
        commit=True,
    )
    before = _count_entities_fts(migrated_conn)
    migrated_conn.execute("BEGIN IMMEDIATE")
    with patch(
        "cortex_store.entity_crud.sync_entity_aliases",
        side_effect=RuntimeError("injected alias failure"),
    ):
        with pytest.raises(RuntimeError, match="injected alias failure"):
            update_entity_impl(
                migrated_conn,
                entity_id="note:fts-rollback-prior",
                updates={"name": "rollback mutated"},
                commit=False,
            )
    migrated_conn.rollback()
    after = _count_entities_fts(migrated_conn)
    assert after == before
    row = migrated_conn.execute(
        "SELECT name FROM entities WHERE id = ?",
        ("note:fts-rollback-prior",),
    ).fetchone()
    assert row[0] == "rollback prior"


def test_backfill_second_run_is_noop(migrated_conn: sqlite3.Connection) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-backfill-idempotent",
            "type": "note",
            "name": "backfill idempotent",
            "description": "once",
        },
        commit=True,
    )
    first = backfill_entities_fts_conn(migrated_conn)
    assert first >= 1
    count_after_first = _count_entities_fts(migrated_conn)
    snapshot = migrated_conn.execute(
        "SELECT entity_id, name, aliases, slug, src_fname, description "
        "FROM entities_fts ORDER BY entity_id"
    ).fetchall()
    second = backfill_entities_fts_conn(migrated_conn)
    assert second >= 1
    assert _count_entities_fts(migrated_conn) == count_after_first
    after_second = migrated_conn.execute(
        "SELECT entity_id, name, aliases, slug, src_fname, description "
        "FROM entities_fts ORDER BY entity_id"
    ).fetchall()
    assert after_second == snapshot


def test_reindex_updates_fts_after_entity_rename(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-reindex-rename",
            "type": "note",
            "name": "alpha beta",
            "description": "",
        },
        commit=True,
    )
    update_entity_impl(
        migrated_conn,
        entity_id="note:fts-reindex-rename",
        updates={"name": "gamma delta renamed"},
        commit=True,
    )
    reindex_entity_fts_conn(migrated_conn, "note:fts-reindex-rename")
    hits = search_entities_fts(migrated_conn, "gamma delta renamed", limit=3)
    assert hits and hits[0]["entity_id"] == "note:fts-reindex-rename"


def test_drift_probe_live_equals_fts(migrated_conn: sqlite3.Connection) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-drift-probe",
            "type": "note",
            "name": "drift witness",
            "description": "",
        },
        commit=True,
    )
    live, fts = entities_fts_drift(migrated_conn)
    assert live == fts


def test_strict_lane_prefers_name_over_description_only(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-strict-name-win",
            "type": "note",
            "name": "zebra alpha token",
            "description": "unrelated",
        },
        commit=True,
    )
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-strict-desc-only",
            "type": "note",
            "name": "other entity",
            "description": "zebra alpha token appears only here in description",
        },
        commit=True,
    )
    hits = search_entities_fts(migrated_conn, "zebra alpha token", limit=5)
    assert hits
    assert hits[0]["entity_id"] == "note:fts-strict-name-win"


def test_search_entities_by_query_removed() -> None:
    import cortex_store.entity_discover as ed

    assert not hasattr(ed, "search_entities_by_query")


def test_backfill_removes_stale_fts_rows_then_second_run_noop(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-backfill-stale-cleanup",
            "type": "note",
            "name": "live backfill witness",
            "description": "",
        },
        commit=True,
    )
    migrated_conn.execute(
        "INSERT INTO entities_fts "
        "(name, aliases, slug, src_fname, description, entity_id, etype) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("orphan", "", "orphan", "", "", "note:fts-orphan-nonlive", "note"),
    )
    assert _count_entities_fts(migrated_conn) >= 2
    backfill_entities_fts_conn(migrated_conn)
    orphan = migrated_conn.execute(
        "SELECT 1 FROM entities_fts WHERE entity_id = ?",
        ("note:fts-orphan-nonlive",),
    ).fetchone()
    assert orphan is None
    count_after_first = _count_entities_fts(migrated_conn)
    snapshot = migrated_conn.execute(
        "SELECT entity_id, name, aliases, slug, src_fname, description "
        "FROM entities_fts ORDER BY entity_id"
    ).fetchall()
    backfill_entities_fts_conn(migrated_conn)
    assert _count_entities_fts(migrated_conn) == count_after_first
    after_second = migrated_conn.execute(
        "SELECT entity_id, name, aliases, slug, src_fname, description "
        "FROM entities_fts ORDER BY entity_id"
    ).fetchall()
    assert after_second == snapshot


def test_write_entity_reaped_drops_fts_row(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "note:fts-reap-sync"
    create_entity_impl(
        migrated_conn,
        {
            "id": entity_id,
            "type": "note",
            "name": "reap fts witness",
            "description": "",
        },
        commit=True,
    )
    row = migrated_conn.execute(
        "SELECT entity_id FROM entities_fts WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    assert row is not None
    write_entity_reaped(migrated_conn, entity_id, "2026-10-23T09:00:00Z")
    gone = migrated_conn.execute(
        "SELECT entity_id FROM entities_fts WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    assert gone is None


def test_entities_fts_stats_live_rows_stale(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:fts-stats-live",
            "type": "note",
            "name": "stats witness",
            "description": "",
        },
        commit=True,
    )
    stats = entities_fts_stats(migrated_conn)
    assert set(stats.keys()) == {"live", "rows", "stale"}
    assert stats["stale"] == 0
    assert stats["live"] == stats["rows"]
    migrated_conn.execute(
        "INSERT INTO entities_fts "
        "(name, aliases, slug, src_fname, description, entity_id, etype) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("stale", "", "stale", "", "", "note:fts-stats-stale-only", "note"),
    )
    stats_with_stale = entities_fts_stats(migrated_conn)
    assert stats_with_stale["stale"] == 1
    assert stats_with_stale["rows"] == stats["rows"] + 1
