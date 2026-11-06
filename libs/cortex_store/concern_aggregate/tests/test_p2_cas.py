"""Phase-2 position CAS acceptance (B1–B3, B6–B7)."""

from __future__ import annotations

import sqlite3
import threading

import pytest
from cortex_store._test_db_bootstrap import open_migrated_connection
from cortex_store.concern_aggregate.cas import ConcernCasError, apply_position_head
from cortex_store.concern_aggregate.fault_injection import set_after_write_intent_insert
from cortex_store.concern_aggregate.journal import append_transition
from cortex_store.concern_aggregate.tests.conftest import append_position_head
from cortex_store.concern_aggregate.views import read_head

pytestmark = pytest.mark.offline

AUTH = "decision:test-auth"


def test_b1_two_writers_one_wins_409(tmp_path) -> None:
    """B1: concurrent writers — one commit, loser 409 with current_head."""
    db_path = tmp_path / "b1.db"
    template = tmp_path / "template.db"
    from cortex_store._test_db_bootstrap import (
        copy_template_db,
        materialize_head_schema_template,
    )

    materialize_head_schema_template(template)
    copy_template_db(template, db_path)
    cid = "concern:b1"
    results: list[dict] = []
    lock = threading.Lock()

    def attempt(rev: int, tag: str) -> None:
        conn = open_migrated_connection(db_path)
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
            (AUTH, "decision", "auth"),
        )
        conn.commit()
        try:
            out = apply_position_head(
                conn,
                concern_id=cid,
                expected_revision=0,
                write_intent_id=f"wi:{tag}",
                material={"tag": tag},
                record_ref=f"rec-{tag}",
                authorized_by=AUTH,
            )
            with lock:
                results.append({"ok": True, **out})
        except ConcernCasError as exc:
            with lock:
                results.append({"ok": False, **exc.payload})
        finally:
            conn.close()

    threads = [
        threading.Thread(target=attempt, args=(0, "a")),
        threading.Thread(target=attempt, args=(0, "b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [r for r in results if r.get("ok")]
    losses = [r for r in results if not r.get("ok")]
    assert len(wins) == 1
    assert len(losses) == 1
    assert losses[0]["error"] == "revision_conflict"
    assert losses[0]["current_head"]["revision"] == wins[0]["revision"]


def test_b2_write_intent_replay_after_head_advance(concern_db: sqlite3.Connection) -> None:
    """B2: stale expected_revision replays original result."""
    cid = "concern:b2"
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
    assert replay["replayed"] is True
    assert replay["revision"] == first["revision"]
    assert replay["record_ref"] == first["record_ref"]
    count = concern_db.execute(
        "SELECT COUNT(*) FROM concern_write_intents WHERE concern_id = ?", (cid,)
    ).fetchone()[0]
    assert count == 2


def test_b3_material_mismatch_and_composite_pk(concern_db: sqlite3.Connection) -> None:
    """B3: fingerprint mismatch 422; same wi on different concern ok."""
    cid_a = "concern:b3a"
    cid_b = "concern:b3b"
    append_position_head(
        concern_db,
        cid_a,
        record_ref="rec-a",
        write_intent_id="wi:shared",
        material={"x": 1},
    )
    with pytest.raises(ConcernCasError) as exc:
        append_transition(
            concern_db,
            concern_id=cid_a,
            kind="position_head",
            record_ref="rec-a2",
            authorized_by=AUTH,
            expected_revision=1,
            write_intent_id="wi:shared",
            material={"x": 2},
        )
    assert exc.value.payload["error"] == "write_intent_material_mismatch"
    append_position_head(
        concern_db,
        cid_b,
        record_ref="rec-b",
        write_intent_id="wi:shared",
        material={"x": 2},
    )


def test_b6_fault_after_write_intent_rolls_back(concern_db: sqlite3.Connection) -> None:
    """B6: hook between write_intents insert and journal rolls back both."""
    cid = "concern:b6"

    def _boom() -> None:
        raise RuntimeError("between intent and journal")

    set_after_write_intent_insert(_boom)
    with pytest.raises(RuntimeError):
        append_position_head(
            concern_db,
            cid,
            record_ref="rec-b6",
            write_intent_id="wi:b6",
        )
    assert (
        concern_db.execute(
            "SELECT COUNT(*) FROM concern_write_intents WHERE concern_id = ?",
            (cid,),
        ).fetchone()[0]
        == 0
    )
    assert (
        concern_db.execute(
            "SELECT COUNT(*) FROM concern_journal WHERE concern_id = ?", (cid,)
        ).fetchone()[0]
        == 0
    )


def test_b7_revision_tracks_head_not_control(concern_db: sqlite3.Connection) -> None:
    """B7: ETag revision unchanged by control_set; bumps on position_head."""
    cid = "concern:b7"
    assert read_head(concern_db, cid) is None
    append_transition(
        concern_db,
        concern_id=cid,
        kind="control_set",
        payload={"control_key": "k", "hold": {}},
        record_ref="rec-ctl",
        authorized_by=AUTH,
    )
    assert read_head(concern_db, cid) is None
    first = append_position_head(
        concern_db,
        cid,
        record_ref="rec-h1",
        expected_revision=0,
        write_intent_id="wi:b7-1",
    )
    assert read_head(concern_db, cid)["revision"] == 1
    append_transition(
        concern_db,
        concern_id=cid,
        kind="control_set",
        payload={"control_key": "k2", "hold": {}},
        record_ref="rec-ctl2",
        authorized_by=AUTH,
    )
    assert read_head(concern_db, cid)["revision"] == 1
    second = append_position_head(
        concern_db,
        cid,
        record_ref="rec-h2",
        expected_revision=1,
        write_intent_id="wi:b7-2",
    )
    assert second["revision"] == 2
    assert read_head(concern_db, cid)["revision"] == 2
    assert first["revision"] == 1
