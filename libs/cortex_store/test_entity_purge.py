"""Hermetic tests for guarded disposable entity purge (thread 1533 Rec #2)."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.entity_crud import create_entity_impl
from cortex_store.entity_purge import purge_disposable_entity


def _entity_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])


def _seed_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    create_entity_impl(
        conn,
        {
            "id": entity_id,
            "type": entity_id.split(":", 1)[0],
            "name": entity_id.split(":", 1)[-1],
        },
    )


def _seed_assertion(conn: sqlite3.Connection, entity_id: str) -> int:
    claim = f"claim for {entity_id}"
    claim_hash = compute_claim_hash(entity_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, entrenchment_score) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, 0.01)",
        (entity_id, claim, claim_hash),
    )
    return int(cur.lastrowid)


def _seed_happy_probe(
    conn: sqlite3.Connection, entity_id: str = "decision:rekey-probe-z"
) -> int:
    _seed_entity(conn, entity_id)
    assertion_id = _seed_assertion(conn, entity_id)
    conn.execute(
        "INSERT INTO tag_assignments (tag_name, entity_id, assertion_id, assigned_by) "
        "VALUES ('probe-tag', ?, ?, 'test')",
        (entity_id, assertion_id),
    )
    conn.execute(
        "INSERT INTO surface_forms (mention, entity_id, context_hash) "
        "VALUES ('probe mention', ?, 'ctx-probe')",
        (entity_id,),
    )
    conn.execute(
        "INSERT INTO near_duplicate_flags (assertion_id, duplicate_of, score) "
        "VALUES (?, ?, 0.9)",
        (assertion_id, assertion_id),
    )
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score, fingerprint) "
        "VALUES (?, 0.1, 'fp-probe')",
        (entity_id,),
    )
    conn.commit()
    return assertion_id


@pytest.mark.offline
def test_happy_path_purge_disposable_probe(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "decision:rekey-probe-z"
    _seed_happy_probe(migrated_conn, entity_id)
    record_mock = MagicMock()
    monkeypatch.setattr("cortex_store.entity_purge.record", record_mock)

    result = purge_disposable_entity(
        migrated_conn,
        entity_id,
        actor="test",
        reason="probe cleanup",
    )

    assert result.entity_id == entity_id
    assert result.assertions_deleted == 1
    assert all(v == 0 for v in result.orphan_sweep.values())
    assert (
        migrated_conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()[0]
        == 0
    )
    assert migrated_conn.execute("PRAGMA foreign_key_check").fetchall() == []
    record_mock.assert_called_once()
    assert record_mock.call_args.args[0] == "cortex.entity.purged"
    assert record_mock.call_args.kwargs["entity_id"] == entity_id


@pytest.mark.offline
def test_reject_non_disposable_entity(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "decision:keeper-1"
    before = _entity_count(migrated_conn)
    _seed_entity(migrated_conn, entity_id)
    migrated_conn.commit()
    before += 1

    with pytest.raises(HTTPException) as exc:
        purge_disposable_entity(
            migrated_conn,
            entity_id,
            actor="test",
            reason="should reject",
        )

    assert exc.value.status_code == 422
    assert _entity_count(migrated_conn) == before
    assert (
        migrated_conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.offline
def test_confirmed_band_requires_force(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "decision:rekey-probe-confirmed"
    _seed_entity(migrated_conn, entity_id)
    migrated_conn.execute(
        "UPDATE entities SET confidence_band = 'confirmed' WHERE id = ?",
        (entity_id,),
    )
    migrated_conn.commit()

    with pytest.raises(HTTPException) as exc:
        purge_disposable_entity(
            migrated_conn,
            entity_id,
            actor="test",
            reason="confirmed reject",
        )
    assert exc.value.status_code == 422

    purge_disposable_entity(
        migrated_conn,
        entity_id,
        actor="test",
        reason="forced purge",
        force=True,
    )
    assert (
        migrated_conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.offline
def test_inbound_reference_guard_blocks_purge(
    migrated_conn: sqlite3.Connection,
) -> None:
    probe_id = "decision:probe-x"
    keeper_id = "decision:keeper-2"
    _seed_entity(migrated_conn, probe_id)
    _seed_entity(migrated_conn, keeper_id)
    a1 = _seed_assertion(migrated_conn, probe_id)
    a2 = _seed_assertion(migrated_conn, keeper_id)
    migrated_conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (a1, a2),
    )
    migrated_conn.commit()
    before = _entity_count(migrated_conn)

    with pytest.raises(HTTPException) as exc:
        purge_disposable_entity(
            migrated_conn,
            probe_id,
            actor="test",
            reason="inbound guard",
            force=True,
        )

    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["inbound_assertion_ids"] == [a2]
    assert _entity_count(migrated_conn) == before


@pytest.mark.offline
def test_fk_off_connection_rejected(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "decision:rekey-probe-fk-off"
    _seed_entity(migrated_conn, entity_id)
    migrated_conn.commit()
    migrated_conn.execute("PRAGMA foreign_keys=OFF")

    with pytest.raises(HTTPException) as exc:
        purge_disposable_entity(
            migrated_conn,
            entity_id,
            actor="test",
            reason="fk off",
        )

    assert exc.value.status_code == 422
    migrated_conn.execute("PRAGMA foreign_keys=ON")
