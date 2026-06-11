"""Tests for friction_close dispatch op (F5)."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store.dispatch_ops._friction_close_impl import (
    close_friction_assertion,
    validate_resolution_kind,
)
from cortex_store.dispatch_ops.ops_assertions_write import _op_friction_close

_SERVICE = "service:test-friction-close"
_FRICTION_CLAIM = "[tool_error] dispatch intake accepts -mcp suffix"


def _seed_service_entity(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
        (_SERVICE, "test-friction-close"),
    )
    conn.commit()


def _insert_friction(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, derivation_type)"
        " VALUES (?, ?, 'hypothesized', 'agent_observation')",
        (_SERVICE, _FRICTION_CLAIM),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _patch_db(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr("cortex_store.db.cortex_conn", lambda: conn)


def _patch_supersede(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    class _NoCloseConn:
        def __init__(self, c: sqlite3.Connection) -> None:
            self._c = c

        def __getattr__(self, name: str) -> object:
            return getattr(self._c, name)

        def close(self) -> None:
            return None

    wrapper = _NoCloseConn(conn)
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.cortex_conn", lambda: wrapper
    )
    for target in (
        "enrich_background",
        "enrich_old_assertion_events",
        "reindex_assertion_fts",
        "_embed_assertion_background",
    ):
        monkeypatch.setattr(
            f"cortex_store.routes.assertions._supersede.{target}", lambda *a, **k: None
        )

    class _FakeVS:
        @staticmethod
        def is_initialized() -> bool:
            return False

        @staticmethod
        def delete_assertion_embedding(_id: int) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.vector_store", _FakeVS
    )

    class _FakeImpact:
        likely_supersedes: list[int] = []
        touched_assertions: list[object] = []

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.analyze_assertion_impact",
        lambda *a, **k: _FakeImpact(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.dispatch_predicate_extract_background",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.recompute_entity_substantiation_status",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._friction_close_impl.record", lambda *a, **k: None
    )


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_service_entity(migrated_conn)
    return migrated_conn


def test_validate_resolution_kind_accepts_workflow_slug() -> None:
    assert validate_resolution_kind("workflow:bug-fix") is None


def test_validate_resolution_kind_rejects_unknown() -> None:
    assert validate_resolution_kind("bogus") is not None


def test_friction_close_supersedes_open_friction(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    friction_id = _insert_friction(conn)
    _patch_db(monkeypatch, conn)
    _patch_supersede(monkeypatch, conn)

    result = _op_friction_close(
        assertion_id=friction_id,
        resolution_kind="workflow:bug-fix",
        agent="cursor",
        session_id="test-session",
        evidence="pytest friction_close",
    )

    assert result.get("status") == "closed"
    fulfillment_id = result.get("fulfillment_assertion_id")
    assert isinstance(fulfillment_id, int)

    row = conn.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (friction_id,)
    ).fetchone()
    assert dict(row)["superseded_by"] == fulfillment_id


def test_friction_close_idempotent_when_already_superseded(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    friction_id = _insert_friction(conn)
    fulfillment_id = _insert_friction(conn)
    conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (fulfillment_id, friction_id),
    )
    conn.commit()
    _patch_db(monkeypatch, conn)

    result = close_friction_assertion(
        friction_id,
        "workflow:bug-fix",
        agent="cursor",
        session_id="test-session",
    )

    assert result.get("status") == "already_closed"
    assert result.get("fulfillment_assertion_id") == fulfillment_id
