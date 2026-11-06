"""Rebuild concern_write_intents from the journal (pre-Phase3 gate b)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from cortex_store.concern_aggregate.checkpoint import write_checkpoint
from cortex_store.concern_aggregate.journal import append_transition
from cortex_store.concern_aggregate.recovery import rebuild_concern
from cortex_store.concern_aggregate.tests.conftest import append_position_head
from cortex_store.concern_aggregate.views import read_cursor

pytestmark = pytest.mark.offline

AUTH = "decision:test-auth"


def _wipe_intents(conn: sqlite3.Connection, concern_id: str) -> None:
    conn.execute(
        "DELETE FROM concern_write_intents WHERE concern_id = ?", (concern_id,)
    )
    conn.commit()


def _intent_rows(conn: sqlite3.Connection, concern_id: str) -> list[tuple]:
    rows = conn.execute(
        "SELECT write_intent_id, material_fingerprint, result_record_ref, "
        "result_revision, result_seq FROM concern_write_intents "
        "WHERE concern_id = ? ORDER BY result_seq",
        (concern_id,),
    ).fetchall()
    return [tuple(row) for row in rows]


def _journal_intents(conn: sqlite3.Connection, concern_id: str, through: int) -> list[tuple]:
    rows = conn.execute(
        "SELECT write_intent_id, material_fingerprint, record_ref, revision, seq "
        "FROM concern_journal WHERE concern_id = ? AND committed_at IS NOT NULL "
        "AND write_intent_id IS NOT NULL AND material_fingerprint IS NOT NULL "
        "AND seq <= ? ORDER BY seq",
        (concern_id, through),
    ).fetchall()
    return [tuple(row) for row in rows]


def test_rebuild_restores_write_intents_from_journal(
    concern_db: sqlite3.Connection,
) -> None:
    """Wiped intents table is restored from journal columns through the cursor."""
    cid = "concern:rebuild-intents"
    append_position_head(
        concern_db,
        cid,
        record_ref="rec-1",
        write_intent_id="wi:restore",
        material={"step": 1},
    )
    before = _intent_rows(concern_db, cid)
    assert before
    _wipe_intents(concern_db, cid)
    rebuild_concern(concern_db, cid)
    through, _ = read_cursor(concern_db, cid)
    restored = _intent_rows(concern_db, cid)
    assert restored
    assert restored == before
    assert restored == _journal_intents(concern_db, cid, through)


def test_b2_replay_survives_rebuild(concern_db: sqlite3.Connection) -> None:
    """B2 replay still hits the rebuilt intent and does not append a journal row."""
    cid = "concern:b2-rebuild"
    first = append_position_head(
        concern_db,
        cid,
        record_ref="rec-1",
        expected_revision=0,
        write_intent_id="wi:b2",
        material={"step": 1},
    )
    append_position_head(
        concern_db,
        cid,
        record_ref="rec-2",
        expected_revision=1,
        write_intent_id="wi:b2-advance",
        material={"step": 2},
    )
    before = concern_db.execute(
        "SELECT COUNT(*) FROM concern_journal WHERE concern_id = ?", (cid,)
    ).fetchone()[0]
    _wipe_intents(concern_db, cid)
    rebuild_concern(concern_db, cid)
    replay = append_transition(
        concern_db,
        concern_id=cid,
        kind="position_head",
        record_ref="rec-ignored",
        authorized_by=AUTH,
        expected_revision=0,
        write_intent_id="wi:b2",
        material={"step": 1},
    )
    after = concern_db.execute(
        "SELECT COUNT(*) FROM concern_journal WHERE concern_id = ?", (cid,)
    ).fetchone()[0]
    assert replay["replayed"] is True
    assert replay["revision"] == first["revision"]
    assert replay["record_ref"] == first["record_ref"]
    assert after == before


def test_rebuild_write_intents_respects_checkpoint_and_suffix(
    concern_db: sqlite3.Connection,
) -> None:
    """Intents on both sides of a checkpoint are present after rebuild."""
    cid = "concern:ckpt-intents"
    append_position_head(
        concern_db, cid, record_ref="rec-1", write_intent_id="wi:pre", material={"n": 1}
    )
    write_checkpoint(concern_db, concern_id=cid)
    append_position_head(
        concern_db,
        cid,
        record_ref="rec-2",
        expected_revision=1,
        write_intent_id="wi:post",
        material={"n": 2},
    )
    _wipe_intents(concern_db, cid)
    rebuild_concern(concern_db, cid)
    ids = [row[0] for row in _intent_rows(concern_db, cid)]
    assert ids == ["wi:pre", "wi:post"]


def test_rebuild_write_intents_stops_with_seq_gap(
    concern_db: sqlite3.Connection,
) -> None:
    """Intents past the first seq gap are not rebuilt (same ceiling as the cursor)."""
    cid = "concern:intent-gap"
    append_position_head(
        concern_db, cid, record_ref="rec-1", write_intent_id="wi:1", material={"n": 1}
    )
    concern_db.execute(
        "UPDATE concern_seq_alloc SET next_seq = 4 WHERE concern_id = ?", (cid,)
    )
    now = datetime.now(UTC).isoformat()
    concern_db.execute(
        "INSERT INTO concern_journal "
        "(concern_id, seq, record_ref, kind, payload, authorized_by, "
        "fencing_generation, revision, fold_version, schema_version, committed_at, "
        "write_intent_id, material_fingerprint) "
        "VALUES (?, 3, 'rec-3', 'position_head', ?, ?, 1, 2, 1, 1, ?, 'wi:gap', 'fp-gap')",
        (cid, json.dumps({"material_fingerprint": "fp-gap"}), AUTH, now),
    )
    concern_db.commit()
    rebuild_concern(concern_db, cid)
    through, _ = read_cursor(concern_db, cid)
    rows = _intent_rows(concern_db, cid)
    assert through == 1
    assert [row[0] for row in rows] == ["wi:1"]
    assert all(row[4] <= through for row in rows)


def test_rebuild_empty_suffix_after_checkpoint(concern_db: sqlite3.Connection) -> None:
    """Checkpoint at the tip still restores intents; there is no suffix to replay."""
    cid = "concern:empty-suffix"
    append_position_head(
        concern_db, cid, record_ref="rec-1", write_intent_id="wi:tip", material={"n": 1}
    )
    write_checkpoint(concern_db, concern_id=cid)
    before = _intent_rows(concern_db, cid)
    assert before
    _wipe_intents(concern_db, cid)
    rebuild_concern(concern_db, cid)
    through, _ = read_cursor(concern_db, cid)
    assert through == 1
    assert _intent_rows(concern_db, cid) == before


def test_rebuild_duplicate_write_intent_keeps_earliest(
    concern_db: sqlite3.Connection,
) -> None:
    """Two journal rows with one write_intent_id: the lower seq survives."""
    cid = "concern:dup-intent"
    append_position_head(
        concern_db, cid, record_ref="rec-first", write_intent_id="wi:dup", material={"n": 1}
    )
    now = datetime.now(UTC).isoformat()
    fp = concern_db.execute(
        "SELECT material_fingerprint FROM concern_journal WHERE concern_id = ?",
        (cid,),
    ).fetchone()[0]
    concern_db.execute(
        "UPDATE concern_seq_alloc SET next_seq = 3 WHERE concern_id = ?", (cid,)
    )
    concern_db.execute(
        "INSERT INTO concern_journal "
        "(concern_id, seq, record_ref, kind, payload, authorized_by, "
        "fencing_generation, revision, fold_version, schema_version, committed_at, "
        "write_intent_id, material_fingerprint) "
        "VALUES (?, 2, 'rec-later', 'position_head', '{}', ?, 1, 2, 1, 1, ?, 'wi:dup', ?)",
        (cid, AUTH, now, fp),
    )
    concern_db.commit()
    _wipe_intents(concern_db, cid)
    rebuild_concern(concern_db, cid)
    rows = _intent_rows(concern_db, cid)
    assert len(rows) == 1
    assert rows[0][0] == "wi:dup"
    assert rows[0][2] == "rec-first"
    assert rows[0][4] == 1


def test_rebuild_skips_null_material_fingerprint(concern_db: sqlite3.Connection) -> None:
    """A write_intent_id with a NULL fingerprint is not inserted."""
    cid = "concern:null-fp"
    append_position_head(
        concern_db, cid, record_ref="rec-1", write_intent_id="wi:ok", material={"n": 1}
    )
    now = datetime.now(UTC).isoformat()
    concern_db.execute(
        "UPDATE concern_seq_alloc SET next_seq = 3 WHERE concern_id = ?", (cid,)
    )
    concern_db.execute(
        "INSERT INTO concern_journal "
        "(concern_id, seq, record_ref, kind, payload, authorized_by, "
        "fencing_generation, revision, fold_version, schema_version, committed_at, "
        "write_intent_id, material_fingerprint) "
        "VALUES (?, 2, 'rec-null', 'position_head', '{}', ?, 1, 2, 1, 1, ?, 'wi:null', NULL)",
        (cid, AUTH, now),
    )
    concern_db.commit()
    _wipe_intents(concern_db, cid)
    rebuild_concern(concern_db, cid)
    ids = [row[0] for row in _intent_rows(concern_db, cid)]
    assert ids == ["wi:ok"]


def test_rebuild_write_intents_is_idempotent(concern_db: sqlite3.Connection) -> None:
    """A second rebuild yields the same intent rows."""
    cid = "concern:idem"
    append_position_head(
        concern_db, cid, record_ref="rec-1", write_intent_id="wi:idem", material={"n": 1}
    )
    _wipe_intents(concern_db, cid)
    rebuild_concern(concern_db, cid)
    first = _intent_rows(concern_db, cid)
    rebuild_concern(concern_db, cid)
    assert first
    assert _intent_rows(concern_db, cid) == first
