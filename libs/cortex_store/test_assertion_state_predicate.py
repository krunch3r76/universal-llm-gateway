"""Hermetic regression suite for assertion_state dispatch op."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.dispatch_ops import _OPS, execute_op
from cortex_store.dispatch_ops.ops_assertions import _op_assertion_state
from cortex_store.routes.assertions._list_filters import append_assertion_list_filters


@contextmanager
def _patched_conn(conn: sqlite3.Connection):
    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *a: object) -> None:
            return None

    with patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn", _Ctx):
        yield


def _reference_confirmed_count(conn: sqlite3.Connection, entity_id: str) -> int:
    clauses = ["1=1"]
    params: list[str | int] = []
    append_assertion_list_filters(
        clauses,
        params,
        entity_id=entity_id,
        confidence="confirmed",
        superseded=False,
    )
    where = " AND ".join(clauses)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM assertions a WHERE {where}",
        tuple(params),
    ).fetchone()
    return int(row["n"] if row else 0)


def _insert_assertion_ex(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    confidence: str = "believed",
    superseded_by: int | None = None,
    review_status: str = "committed",
    created_at: str | None = None,
) -> int:
    if created_at is None:
        return insert_assertion(
            conn,
            entity_id=entity_id,
            claim=claim,
            confidence=confidence,
            superseded_by=superseded_by,
        )
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by, "
        "review_status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (entity_id, claim, confidence, superseded_by, review_status, created_at),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


@pytest.mark.offline
def test_op_registered() -> None:
    assert "assertion_state" in _OPS
    assert _OPS["assertion_state"] is _op_assertion_state


@pytest.mark.offline
def test_missing_entity_id_returns_error() -> None:
    result = _op_assertion_state()
    assert result == {"error": "entity_id is required"}


@pytest.mark.offline
def test_ratified_false_when_no_assertions(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="decision:empty", entity_type="decision")
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id="decision:empty")
    assert result["entity_id"] == "decision:empty"
    assert result["ratified"] is False
    assert result["confirmed_count"] == 0
    assert result["latest_confirmed_assertion_id"] is None


@pytest.mark.offline
def test_ratified_false_when_only_believed(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="decision:believed-only", entity_type="decision")
    insert_assertion(
        conn, entity_id="decision:believed-only", claim="maybe", confidence="believed"
    )
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id="decision:believed-only")
    assert result["ratified"] is False
    assert result["confirmed_count"] == 0


@pytest.mark.offline
def test_ratified_true_when_confirmed_active(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="decision:ratified", entity_type="decision")
    aid = insert_assertion(
        conn,
        entity_id="decision:ratified",
        claim="ratified claim",
        confidence="confirmed",
    )
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id="decision:ratified")
    assert result["ratified"] is True
    assert result["confirmed_count"] == 1
    assert result["latest_confirmed_assertion_id"] == aid


@pytest.mark.offline
def test_staged_confirmed_still_ratified(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="decision:staged", entity_type="decision")
    _insert_assertion_ex(
        conn,
        entity_id="decision:staged",
        claim="staged but confirmed",
        confidence="confirmed",
        review_status="staged",
    )
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id="decision:staged")
    assert result["ratified"] is True
    assert result["confirmed_count"] == 1


@pytest.mark.offline
def test_superseded_confirmed_excluded(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="decision:superseded", entity_type="decision")
    old_id = insert_assertion(
        conn,
        entity_id="decision:superseded",
        claim="old",
        confidence="confirmed",
    )
    new_id = insert_assertion(
        conn,
        entity_id="decision:superseded",
        claim="replacement",
        confidence="believed",
    )
    conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?", (new_id, old_id)
    )
    conn.commit()
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id="decision:superseded")
    assert result["ratified"] is False
    assert result["confirmed_count"] == 0


@pytest.mark.offline
def test_latest_id_is_newest_confirmed(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="decision:latest", entity_type="decision")
    older = _insert_assertion_ex(
        conn,
        entity_id="decision:latest",
        claim="older",
        confidence="confirmed",
        created_at="2026-01-01T00:00:00+00:00",
    )
    newer = _insert_assertion_ex(
        conn,
        entity_id="decision:latest",
        claim="newer",
        confidence="confirmed",
        created_at="2026-06-01T00:00:00+00:00",
    )
    assert older != newer
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id="decision:latest")
    assert result["confirmed_count"] == 2
    assert result["latest_confirmed_assertion_id"] == newer


@pytest.mark.offline
def test_confirmed_count_matches_reference_query(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "decision:count-ref"
    insert_entity(conn, entity_id=entity_id, entity_type="decision")
    insert_assertion(conn, entity_id=entity_id, claim="b1", confidence="believed")
    insert_assertion(conn, entity_id=entity_id, claim="c1", confidence="confirmed")
    insert_assertion(conn, entity_id=entity_id, claim="c2", confidence="confirmed")
    expected = _reference_confirmed_count(conn, entity_id)
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id=entity_id)
    assert result["confirmed_count"] == expected == 2


@pytest.mark.offline
def test_ratified_matches_preflight_semantics(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "decision:preflight"
    insert_entity(conn, entity_id=entity_id, entity_type="decision")
    insert_assertion(
        conn, entity_id=entity_id, claim="active confirmed", confidence="confirmed"
    )
    ref = _reference_confirmed_count(conn, entity_id)
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id=entity_id)
    assert result["ratified"] == (ref >= 1)


@pytest.mark.offline
def test_alias_resolves_to_canonical_id(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    canonical = "decision:canonical"
    alias = "unified-admission-alias"
    insert_entity(conn, entity_id=canonical, entity_type="decision")
    conn.execute(
        "UPDATE entities SET aliases = ? WHERE id = ?",
        (json.dumps([alias]), canonical),
    )
    conn.execute(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, 'decision', ?)",
        (canonical, alias),
    )
    insert_assertion(
        conn, entity_id=canonical, claim="ratified", confidence="confirmed"
    )
    conn.commit()
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id=alias)
    assert result["entity_id"] == canonical
    assert result["ratified"] is True


@pytest.mark.offline
def test_idempotent_read_no_mutation(migrated_conn: sqlite3.Connection) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="decision:idempotent", entity_type="decision")
    insert_assertion(
        conn, entity_id="decision:idempotent", claim="c", confidence="confirmed"
    )
    before = conn.execute("SELECT COUNT(*) AS n FROM assertions").fetchone()["n"]
    with _patched_conn(conn):
        first = _op_assertion_state(entity_id="decision:idempotent")
        second = _op_assertion_state(entity_id="decision:idempotent")
    after = conn.execute("SELECT COUNT(*) AS n FROM assertions").fetchone()["n"]
    assert before == after
    assert first == second


@pytest.mark.offline
def test_payload_under_2kb_on_motivating_decision(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "decision:unified-implement-admission"
    insert_entity(
        conn, entity_id=entity_id, entity_type="decision", name="Unified admission"
    )
    fat_claim = "x" * 1200
    for i in range(5):
        _insert_assertion_ex(
            conn,
            entity_id=entity_id,
            claim=f"{fat_claim}-{i}",
            confidence="confirmed",
            created_at=f"2026-06-0{i + 1}T00:00:00+00:00",
        )
    with _patched_conn(conn):
        result = _op_assertion_state(entity_id=entity_id)
    payload = json.dumps(result, separators=(",", ":"))
    assert len(payload.encode()) < 2048
    assert result["confirmed_count"] == 5


@pytest.mark.offline
def test_hallucination_alias_suggests_assertion_state() -> None:
    result = execute_op("decision_status", {"entity_id": "decision:missing"})
    assert "error" in result
    assert "Did you mean 'assertion_state'?" in result.get("hint", "")
