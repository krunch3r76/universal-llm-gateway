"""assertion_update re-embeds when enrichment fields change."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from cortex_store.routes.assertions import _update_assertion_impl

_TEST_ENTITY = "test:entity-enrich-reembed"


def _seed_entity(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'test', ?)",
        (_TEST_ENTITY, "test-entity"),
    )
    conn.commit()


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_entity(migrated_conn)
    return migrated_conn


def _insert_assertion(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence) VALUES (?, ?, ?)",
        (_TEST_ENTITY, "Enrichment re-embed claim.", "believed"),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def test_enrichment_update_triggers_reembed(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update.cortex_conn",
        lambda: conn,
    )
    reindex = MagicMock()
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update.reindex_assertion_fts", reindex
    )
    embed = MagicMock()
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update._embed_assertion_background", embed
    )

    aid = _insert_assertion(conn)
    _update_assertion_impl(
        aid,
        {"prospective_summary": "Relevant when the gateway restarts after deploy."},
    )
    reindex.assert_called_once_with(aid)
    embed.assert_called_once()
    assert embed.call_args[0][0] == aid
