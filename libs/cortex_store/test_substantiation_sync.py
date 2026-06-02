"""Phase 3 substantiation_sync — confidence_band write side (thread 1179 / 12103)."""

from __future__ import annotations

import sqlite3

from cortex_store.substantiation_sync import recompute_entity_substantiation_status


def _conn(*, with_confidence_fields: bool = True) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT,
            status TEXT,
            lifecycle TEXT,
            confidence_band TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            confidence TEXT,
            superseded_by INTEGER
        );
        """
    )
    if with_confidence_fields:
        c.executescript(
            """
            CREATE TABLE type_confidence_fields (
                entity_type TEXT PRIMARY KEY,
                confidence_field TEXT NOT NULL
            );
            """
        )
    return c


def _entity(
    c: sqlite3.Connection,
    eid: str,
    status: str,
    *,
    etype: str = "person",
    band: str | None = None,
) -> None:
    c.execute(
        "INSERT INTO entities (id, type, status, confidence_band) VALUES (?, ?, ?, ?)",
        (eid, etype, status, band),
    )
    c.commit()


def _add(c: sqlite3.Connection, eid: str, confidence: str, superseded_by=None) -> None:
    c.execute(
        "INSERT INTO assertions (entity_id, confidence, superseded_by) VALUES (?, ?, ?)",
        (eid, confidence, superseded_by),
    )
    c.commit()


def _band(c: sqlite3.Connection, eid: str) -> str | None:
    row = c.execute(
        "SELECT confidence_band, status FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    return row["confidence_band"]


def _status(c: sqlite3.Connection, eid: str) -> str:
    return c.execute("SELECT status FROM entities WHERE id = ?", (eid,)).fetchone()[0]


def test_promotes_confidence_band_on_confirmed_backing() -> None:
    c = _conn()
    _entity(c, "person:e", "unsubstantiated", band="unsubstantiated")
    _add(c, "person:e", "confirmed")
    assert recompute_entity_substantiation_status(c, "person:e") == "confirmed"
    assert _band(c, "person:e") == "confirmed"
    assert _status(c, "person:e") == "unsubstantiated"


def test_demotion_blocked_fail_closed() -> None:
    """Confirmed band without confirmed backing must not demote (production path)."""
    c = _conn()
    _entity(c, "person:d", "confirmed", band="confirmed")
    _add(c, "person:d", "believed")
    assert recompute_entity_substantiation_status(c, "person:d") is None
    assert _band(c, "person:d") == "confirmed"
    assert _status(c, "person:d") == "confirmed"


def test_noop_when_band_already_matches() -> None:
    c = _conn()
    _entity(c, "person:m", "confirmed", band="confirmed")
    _add(c, "person:m", "confirmed")
    assert recompute_entity_substantiation_status(c, "person:m") is None
    assert _band(c, "person:m") == "confirmed"


def test_superseded_assertion_does_not_promote() -> None:
    c = _conn()
    _entity(c, "person:s", "unsubstantiated", band="unsubstantiated")
    _add(c, "person:s", "confirmed", superseded_by=99)
    assert recompute_entity_substantiation_status(c, "person:s") is None
    assert _band(c, "person:s") == "unsubstantiated"


def test_lifecycle_axis_status_never_synced() -> None:
    c = _conn()
    _entity(c, "person:dep", "deprecated", band=None)
    _add(c, "person:dep", "confirmed")
    assert recompute_entity_substantiation_status(c, "person:dep") is None
    assert _band(c, "person:dep") is None
    assert _status(c, "person:dep") == "deprecated"


def test_decision_type_excluded() -> None:
    c = _conn()
    _entity(c, "decision:x", "provisional", etype="decision", band="provisional")
    _add(c, "decision:x", "confirmed")
    assert recompute_entity_substantiation_status(c, "decision:x") is None
    assert _band(c, "decision:x") == "provisional"


def test_todo_workflow_state_skips_sync_and_demotion() -> None:
    """type_confidence_fields gate: todo uses workflow_state — no band write."""
    c = _conn()
    c.execute(
        "INSERT INTO type_confidence_fields (entity_type, confidence_field) "
        "VALUES ('todo', 'workflow_state')"
    )
    c.commit()
    _entity(c, "todo:t", "confirmed", etype="todo", band="confirmed")
    _add(c, "todo:t", "believed")
    assert recompute_entity_substantiation_status(c, "todo:t") is None
    assert _band(c, "todo:t") == "confirmed"


def test_missing_entity_is_noop() -> None:
    c = _conn()
    assert recompute_entity_substantiation_status(c, "person:ghost") is None


def test_skips_without_trait_columns() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY, type TEXT, status TEXT, updated_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            confidence TEXT,
            superseded_by INTEGER
        );
        """
    )
    c.execute(
        "INSERT INTO entities (id, type, status) VALUES ('person:x', 'person', 'unsubstantiated')"
    )
    c.commit()
    _add(c, "person:x", "confirmed")
    assert recompute_entity_substantiation_status(c, "person:x") is None
