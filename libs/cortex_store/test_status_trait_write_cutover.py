"""Phase 3 Option A write cutover — trait-first INSERT/UPDATE paths."""

from __future__ import annotations

import sqlite3

from cortex_store.entity_crud import create_entity_impl
from cortex_store.routes.reaper import _reap_entity
from cortex_store.status_trait_write import (
    resolve_birth_traits,
    resolve_staged_entity_traits,
    write_entity_reaped,
)

_TRAIT_DDL = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT,
    description TEXT,
    status TEXT,
    lifecycle TEXT,
    confidence_band TEXT,
    adoption TEXT,
    workflow_state TEXT,
    aliases TEXT,
    attributes TEXT,
    notes TEXT,
    source_uri TEXT,
    content_hash TEXT,
    retention_policy TEXT,
    retention_ttl_days INTEGER,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY,
    entity_id TEXT,
    superseded_by INTEGER,
    valid_until TEXT
);
CREATE TABLE session_edges (
    id INTEGER PRIMARY KEY,
    from_node TEXT,
    to_node TEXT,
    valid_until TEXT
);
"""


def _trait_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_TRAIT_DDL)
    return c


def test_create_writes_traits_not_status() -> None:
    conn = _trait_conn()
    create_entity_impl(
        conn, {"id": "project:p", "type": "project", "name": "P"}, commit=False
    )
    row = conn.execute(
        "SELECT status, confidence_band, lifecycle FROM entities WHERE id = ?",
        ("project:p",),
    ).fetchone()
    assert row["status"] is None
    assert row["confidence_band"] == "unsubstantiated"
    assert row["lifecycle"] is None


def test_create_decision_writes_band_and_adoption() -> None:
    conn = _trait_conn()
    create_entity_impl(
        conn, {"id": "decision:d", "type": "decision", "name": "D"}, commit=False
    )
    row = conn.execute(
        "SELECT status, confidence_band, adoption FROM entities WHERE id = ?",
        ("decision:d",),
    ).fetchone()
    assert row["status"] is None
    assert row["confidence_band"] == "provisional"
    assert row["adoption"] == "proposed"


def test_create_lifecycle_deprecated_writes_lifecycle_trait() -> None:
    conn = _trait_conn()
    create_entity_impl(
        conn,
        {
            "id": "project:dep",
            "type": "project",
            "name": "D",
            "status": "deprecated",
        },
        commit=False,
    )
    row = conn.execute(
        "SELECT status, lifecycle, confidence_band FROM entities WHERE id = ?",
        ("project:dep",),
    ).fetchone()
    assert row["status"] is None
    assert row["lifecycle"] == "deprecated"
    assert row["confidence_band"] == "unsubstantiated"


def test_reaper_writes_lifecycle_not_status() -> None:
    conn = _trait_conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, status, confidence_band, "
        "retention_policy, created_at, updated_at) "
        "VALUES ('ephemeral:1', 'note', 'n', 'provisional', 'provisional', "
        "'ephemeral', 't', 't')"
    )
    _reap_entity(conn, "ephemeral:1", "2026-06-02T00:00:00Z")
    row = conn.execute(
        "SELECT status, lifecycle FROM entities WHERE id = 'ephemeral:1'"
    ).fetchone()
    assert row["lifecycle"] == "reaped"
    assert row["status"] == "provisional"


def test_write_entity_reaped_legacy_status_only_db() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, status TEXT, updated_at TEXT)"
    )
    conn.execute("INSERT INTO entities VALUES ('x', 'active', 't')")
    write_entity_reaped(conn, "x", "2026-06-02T01:00:00Z")
    row = conn.execute("SELECT status FROM entities WHERE id = 'x'").fetchone()
    assert row["status"] == "reaped"


def test_resolve_staged_provisional_band() -> None:
    t = resolve_staged_entity_traits("confirmed")
    assert t.confidence_band == "provisional"
    assert t.legacy_status == "provisional"


def test_resolve_staged_lifecycle_reaped() -> None:
    t = resolve_staged_entity_traits("reaped")
    assert t.lifecycle == "reaped"
    assert t.confidence_band is None
