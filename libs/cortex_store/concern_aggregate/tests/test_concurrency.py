"""Concurrency and fencing tests for concern journal."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from cortex_store._test_db_bootstrap import copy_template_db, open_migrated_connection
from cortex_store.concern_aggregate.cas import ConcernCasError
from cortex_store.concern_aggregate.fault_injection import (
    FaultInjectionDisabledError,
    clear_all,
    set_after_journal_insert,
)
from cortex_store.concern_aggregate.journal import (
    TransitionRejectedError,
    append_transition,
)
from cortex_store.concern_aggregate.views import read_head

pytestmark = pytest.mark.offline

AUTH = "decision:test-auth"


def test_concurrent_append_revisions_unique_monotonic(
    tmp_path: Path,
    migrated_db_template: Path,
) -> None:
    """N1/F2: concurrent writers produce unique monotonic revisions; fencing never regresses."""
    db_path = tmp_path / "shared.db"
    copy_template_db(migrated_db_template, db_path)
    seed = open_migrated_connection(db_path)
    seed.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (AUTH, "decision", "test authority"),
    )
    seed.commit()
    seed.close()

    cid = "concern:conc"
    writers = 2
    appends_per_writer = 50
    errors: list[BaseException] = []
    revisions_lock = threading.Lock()
    revisions_seen: list[int] = []
    fences_seen: list[int] = []

    def worker(worker_id: int) -> None:
        conn = open_migrated_connection(db_path)
        try:
            for i in range(appends_per_writer):
                for _attempt in range(20):
                    head = read_head(conn, cid)
                    expected = int(head["revision"]) if head else 0
                    try:
                        result = append_transition(
                            conn,
                            concern_id=cid,
                            kind="position_head",
                            payload={},
                            record_ref=f"rec-{worker_id}-{i}",
                            authorized_by=AUTH,
                            expected_revision=expected,
                            write_intent_id=f"wi:{worker_id}:{i}",
                            material={"worker": worker_id, "i": i},
                        )
                    except ConcernCasError:
                        continue
                    with revisions_lock:
                        revisions_seen.append(int(result["revision"]))
                        fences_seen.append(int(result["fencing_generation"]))
                    break
                else:
                    errors.append(RuntimeError(f"worker {worker_id} stuck on i={i}"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    total = writers * appends_per_writer
    assert sorted(revisions_seen) == list(range(1, total + 1))
    assert fences_seen == sorted(fences_seen)
    head_conn = open_migrated_connection(db_path)
    head = read_head(head_conn, cid)
    assert head is not None
    assert head["revision"] == total
    head_conn.close()


def test_fault_hooks_inert_when_disabled(
    concern_db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N2/F5: hooks cannot be registered when fault injection gate is off."""
    monkeypatch.delenv("CORTEX_CONCERN_FAULT_INJECTION", raising=False)
    import cortex_store.concern_aggregate.fault_injection as fi

    fi._enabled = None
    clear_all()
    with pytest.raises(FaultInjectionDisabledError):
        set_after_journal_insert(lambda: None)
    fi._enabled = None
    append_transition(
        concern_db,
        concern_id="concern:gate-off",
        kind="control_set",
        payload={"control_key": "k", "hold": {}},
        record_ref="rec-ok",
        authorized_by=AUTH,
    )


def test_rebuild_stops_at_seq_gap(concern_db: sqlite3.Connection) -> None:
    """N3/F3: rebuild does not apply committed rows past the first seq gap."""
    from datetime import UTC, datetime

    from cortex_store.concern_aggregate.journal import append_transition
    from cortex_store.concern_aggregate.recovery import rebuild_concern
    from cortex_store.concern_aggregate.views import read_controls, read_cursor

    cid = "concern:gap"
    auth = AUTH
    for i, key in enumerate(("k1", "k2", "k3"), start=1):
        append_transition(
            concern_db,
            concern_id=cid,
            kind="control_set",
            payload={"control_key": key, "hold": {"n": i}},
            record_ref=f"rec-{i}",
            authorized_by=auth,
        )
    concern_db.execute(
        "INSERT OR IGNORE INTO concern_seq_alloc (concern_id, next_seq) VALUES (?, 4)",
        (cid,),
    )
    concern_db.execute(
        "UPDATE concern_seq_alloc SET next_seq = 6 WHERE concern_id = ?",
        (cid,),
    )
    concern_db.execute(
        "INSERT INTO concern_journal "
        "(concern_id, seq, record_ref, kind, payload, authorized_by, "
        "fencing_generation, revision, fold_version, schema_version, committed_at) "
        "VALUES (?, 4, 'rec-4', 'control_set', ?, ?, 0, 4, 1, 1, NULL)",
        (cid, json.dumps({"control_key": "k4", "hold": {"n": 4}}), auth),
    )
    now = datetime.now(UTC).isoformat()
    concern_db.execute(
        "INSERT INTO concern_journal "
        "(concern_id, seq, record_ref, kind, payload, authorized_by, "
        "fencing_generation, revision, fold_version, schema_version, committed_at) "
        "VALUES (?, 5, 'rec-5', 'control_set', ?, ?, 0, 5, 1, 1, ?)",
        (cid, json.dumps({"control_key": "k5", "hold": {"n": 5}}), auth, now),
    )
    concern_db.commit()
    rebuilt = rebuild_concern(concern_db, cid)
    through, _ = read_cursor(concern_db, cid)
    assert through == 3
    assert rebuilt["through_seq"] == 3
    assert "k5" not in read_controls(concern_db, cid)
    concern_db.execute(
        "UPDATE concern_journal SET committed_at = ? WHERE concern_id = ? AND seq = 4",
        (now, cid),
    )
    concern_db.commit()
    rebuild_concern(concern_db, cid)
    through2, _ = read_cursor(concern_db, cid)
    assert through2 == 5
    assert "k5" in read_controls(concern_db, cid)


def test_stale_fence_rejected_under_contention(
    concern_db: sqlite3.Connection,
) -> None:
    """N1: explicit fencing_generation below current head is rejected."""
    cid = "concern:stale-fence"
    append_transition(
        concern_db,
        concern_id=cid,
        kind="position_head",
        payload={"fencing_generation": 5},
        record_ref="rec-head",
        authorized_by=AUTH,
        expected_revision=0,
        write_intent_id="wi:stale-fence-head",
        material={"fencing_generation": 5},
    )
    head = read_head(concern_db, cid)
    assert head is not None
    assert head["fencing_generation"] == 5
    with pytest.raises(TransitionRejectedError, match="stale fencing_generation"):
        append_transition(
            concern_db,
            concern_id=cid,
            kind="control_set",
            payload={"control_key": "k", "hold": {}, "fencing_generation": 3},
            record_ref="rec-stale",
            authorized_by=AUTH,
        )
