"""entities_fts R4 reconciler — id-set diff, repair, and interval defaults."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from cortex_store.entity_crud import create_entity_impl
from cortex_store.entity_fts import entities_fts_stats, reindex_entity_fts_conn
from cortex_store.entity_fts_reconcile import (
    _FULL_COMPARE_INTERVAL_S,
    _ID_DIFF_INTERVAL_S,
    aliases_index_text_matches_renderer,
    id_diff_sets,
    reconcile_id_diff_pass,
)


def test_id_diff_finds_live_entity_without_fts_row(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:reconcile-missing-fts",
            "type": "note",
            "name": "reconcile missing witness",
            "description": "",
        },
        commit=True,
    )
    migrated_conn.execute(
        "DELETE FROM entities_fts WHERE entity_id = ?",
        ("note:reconcile-missing-fts",),
    )
    migrated_conn.commit()

    _live, _fts, missing_live, _stale = id_diff_sets(migrated_conn)
    assert "note:reconcile-missing-fts" in missing_live


def test_id_diff_pass_repairs_when_stats_stale_is_zero(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:reconcile-stats-stale-zero",
            "type": "note",
            "name": "stale stat gate witness",
            "description": "",
        },
        commit=True,
    )
    migrated_conn.execute(
        "DELETE FROM entities_fts WHERE entity_id = ?",
        ("note:reconcile-stats-stale-zero",),
    )
    migrated_conn.commit()

    stats = entities_fts_stats(migrated_conn)
    assert stats["stale"] == 0

    with patch("cortex_store.entity_fts_reconcile.record") as mock_record:
        result = reconcile_id_diff_pass(migrated_conn)

    assert result.missing == 1
    assert result.repaired >= 1
    row = migrated_conn.execute(
        "SELECT entity_id FROM entities_fts WHERE entity_id = ?",
        ("note:reconcile-stats-stale-zero",),
    ).fetchone()
    assert row is not None
    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "cortex.entities_fts.reconciled"
    payload = mock_record.call_args.kwargs
    assert payload["pass"] == "id_diff"
    assert payload["missing"] == 1


def test_default_reconcile_intervals() -> None:
    assert _ID_DIFF_INTERVAL_S == 60
    assert _FULL_COMPARE_INTERVAL_S == 900


def test_alias_map_renderer_matches_write_path(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:reconcile-alias-compare",
            "type": "note",
            "name": "Alias Compare",
            "aliases": ["ac-alias"],
            "description": "",
        },
        commit=True,
    )
    row = migrated_conn.execute(
        "SELECT name, aliases FROM entities WHERE id = ?",
        ("note:reconcile-alias-compare",),
    ).fetchone()
    assert aliases_index_text_matches_renderer(
        migrated_conn,
        "note:reconcile-alias-compare",
        row[0],
        row[1],
    )


def test_full_compare_repairs_alias_drift(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "note:reconcile-full-compare",
            "type": "note",
            "name": "full compare witness",
            "description": "",
        },
        commit=True,
    )
    reindex_entity_fts_conn(migrated_conn, "note:reconcile-full-compare")
    migrated_conn.execute(
        "UPDATE entities_fts SET aliases = ? WHERE entity_id = ?",
        ("stale-alias-text", "note:reconcile-full-compare"),
    )
    migrated_conn.commit()

    from cortex_store.entity_fts_reconcile import reconcile_full_compare_pass

    with patch("cortex_store.entity_fts_reconcile.record"):
        result = reconcile_full_compare_pass(migrated_conn)

    assert result.mismatched == 1
    assert result.repaired == 1
    fts_aliases = migrated_conn.execute(
        "SELECT aliases FROM entities_fts WHERE entity_id = ?",
        ("note:reconcile-full-compare",),
    ).fetchone()[0]
    assert fts_aliases != "stale-alias-text"
    assert "full compare witness" in fts_aliases
