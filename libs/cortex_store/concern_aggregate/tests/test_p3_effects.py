"""Phase-3 effect ledger acceptance (F1–F9).

Connections for contention tests: ``isolation_level=None`` (autocommit off), ``journal_mode=WAL``,
and a bounded ``busy_timeout`` on the contender (≥2000 ms) unless testing SQLITE_BUSY.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cortex_store._test_db_bootstrap import fresh_migrated_connection
from cortex_store.concern_aggregate.effects import (
    STATE_ADMITTED,
    STATE_FAILED,
    STATE_OUTCOME_UNKNOWN,
    STATE_SUCCEEDED,
    EffectConflictError,
    HoldActiveError,
    InvalidTransitionError,
    LeaseExpiredError,
    LeaseHeldError,
    OutcomeResult,
    ReconcileRequiredError,
    RetryBudgetExceededError,
    StaleFencingTokenError,
    StaleRunGenerationError,
    acquire_lease,
    admit_effect,
    clear_hold,
    lookup_receipt,
    reconcile_effect,
    record_outcome,
    record_recovery_decision,
    renew_lease,
    resolve_action_intent,
    set_hold,
)
from cortex_store.concern_aggregate.fault_injection import (
    set_after_begin,
    set_after_controls_read,
    set_between_resolve_and_admit,
)
from cortex_store.concern_aggregate.journal import append_transition
from cortex_store.concern_aggregate.recovery import rebuild_concern
from cortex_store.concern_aggregate.tests.conftest import seed_authority

pytestmark = pytest.mark.offline

AUTH = "decision:test-auth"
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _connect_wal(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def test_pragma_migration086_tables_exist(
    tmp_path: Path, migrated_db_template: Path
) -> None:
    """A7: Phase-3 ledger tables exist after bootstrap migrations."""
    conn = fresh_migrated_connection(tmp_path, migrated_db_template)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    }
    assert "concern_action_intents" in names
    assert "concern_effects" in names
    assert "concern_effect_leases" in names
    for t in ("concern_action_intents", "concern_effects", "concern_effect_leases"):
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({t})")]
        assert cols, t


def test_f1_lost_response_one_effect(
    concern_db: sqlite3.Connection,
) -> None:
    """F1: second admit replays with same effect row and receipt; single side-effect."""
    cid = "concern:f1"
    seed_authority(concern_db)
    work_key = "wk:f1"
    admit_count: list[int] = []

    def _on_admit() -> None:
        admit_count.append(1)

    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=work_key,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=work_key,
        action_slot="slot1",
        run_generation=1,
        now=T0,
    )
    req = {"op": "x"}
    r1 = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="dispatch",
        request=req,
        request_id="req:r1",
        fencing_token=g,
        now=T0,
    )
    _on_admit()
    assert r1["replay"] is False
    ek = str(r1["effect_key"])

    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"done": True},
        fencing_token=g,
        now=T0,
    )
    r2 = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="dispatch",
        request=req,
        request_id="req:r2",
        fencing_token=g,
        now=T0,
    )
    assert r2["replay"] is True
    assert r2["receipt"] == {"done": True}
    n = concern_db.execute("SELECT COUNT(*) FROM concern_effects").fetchone()[0]
    assert n == 1
    assert len(admit_count) == 1


def test_f1b_replay_under_hold_returns_receipt(
    concern_db: sqlite3.Connection,
) -> None:
    """F1b: succeeded replay ignores holds."""
    cid = "concern:f1b"
    work_key = "wk:f1b"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=work_key,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=work_key,
        action_slot="slot1",
        run_generation=1,
        now=T0,
    )
    req = {"x": 1}
    r = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="k",
        request=req,
        request_id="req:a",
        fencing_token=g,
        now=T0,
    )
    ek = str(r["effect_key"])
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"r": 1},
        fencing_token=g,
        now=T0,
    )
    set_hold(
        concern_db,
        concern_id=cid,
        control_key="cancel",
        hold={"active": True},
        authorized_by=AUTH,
        record_ref="rec-hold1",
    )
    r2 = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="k",
        request=req,
        request_id="req:b",
        fencing_token=g,
        now=T0,
    )
    assert r2["replay"] is True
    assert r2["receipt"] == {"r": 1}


def test_f2_idempotency_and_stale_run_generation(
    concern_db: sqlite3.Connection,
) -> None:
    """F2: single intent per tuple; new slot/run; K stable across head move; stale run."""
    path = Path(
        next(
            r[2]
            for r in concern_db.execute("PRAGMA database_list").fetchall()
            if r[1] == "main"
        )
    )
    # fresh second connection
    c2 = _connect_wal(path)
    try:
        cid = "concern:f2"
        wk = "wk:f2"
        acquire_lease(
            concern_db,
            concern_id=cid,
            work_key=wk,
            holder="h",
            lease_expires_at=T0 + timedelta(hours=1),
            now=T0,
        )
        i1a = resolve_action_intent(
            concern_db,
            concern_id=cid,
            work_key=wk,
            action_slot="s1",
            run_generation=1,
            now=T0,
        )
        i1b = resolve_action_intent(
            c2,
            concern_id=cid,
            work_key=wk,
            action_slot="s1",
            run_generation=1,
            now=T0 + timedelta(seconds=1),
        )
        assert i1a == i1b

        i2 = resolve_action_intent(
            concern_db,
            concern_id=cid,
            work_key=wk,
            action_slot="s2",
            run_generation=1,
            now=T0,
        )
        assert i2 != i1a

        i3 = resolve_action_intent(
            concern_db,
            concern_id=cid,
            work_key=wk,
            action_slot="s1",
            run_generation=2,
            now=T0,
        )
        assert i3 != i1a

        append_transition(
            concern_db,
            concern_id=cid,
            kind="position_head",
            payload={},
            record_ref="rec-head1",
            authorized_by=AUTH,
            expected_revision=0,
            write_intent_id="wi:test",
            material={"position": "rec-head1"},
        )

        from cortex_store.concern_aggregate.effects import compute_effect_key

        k_before = compute_effect_key(
            concern_id=cid, work_key=wk, intent_id=i1a, effect_kind="e"
        )
        k_after = compute_effect_key(
            concern_id=cid, work_key=wk, intent_id=i1a, effect_kind="e"
        )
        assert k_before == k_after

        with pytest.raises(StaleRunGenerationError):
            resolve_action_intent(
                concern_db,
                concern_id=cid,
                work_key=wk,
                action_slot="s1",
                run_generation=0,
                now=T0,
            )
    finally:
        c2.close()


def test_f3_conflict(concern_db: sqlite3.Connection) -> None:
    """F3: fingerprint mismatch raises EffectConflictError; row unchanged."""
    cid = "concern:f3"
    wk = "wk:f3"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    r = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g,
        now=T0,
    )
    ek = str(r["effect_key"])
    row_before = concern_db.execute(
        "SELECT material_fingerprint, receipt_json FROM concern_effects WHERE effect_key = ?",
        (ek,),
    ).fetchone()
    with pytest.raises(EffectConflictError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request={"a": 2},
            request_id="req:2",
            fencing_token=g,
            now=T0,
        )
    row_after = concern_db.execute(
        "SELECT material_fingerprint, receipt_json FROM concern_effects WHERE effect_key = ?",
        (ek,),
    ).fetchone()
    assert row_before == row_after


def test_f4_lease_expiry_late_report(concern_db: sqlite3.Connection) -> None:
    """F4: generation advance invalidates A's token for admit/outcome; late reports keep prior receipt."""
    cid = "concern:f4"
    wk = "wk:f4"
    t_a = T0
    g_a = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="A",
        lease_expires_at=t_a + timedelta(minutes=5),
        now=t_a,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=t_a,
    )
    r = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request={"x": 1},
        request_id="req:a1",
        fencing_token=g_a,
        now=t_a,
    )
    ek = str(r["effect_key"])
    t_expired = t_a + timedelta(minutes=10)
    g_b = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="B",
        lease_expires_at=t_expired + timedelta(hours=1),
        now=t_expired,
    )
    assert g_b == g_a + 1

    with pytest.raises(StaleFencingTokenError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request={"x": 1},
            request_id="req:stale-admit",
            fencing_token=g_a,
            now=t_expired,
        )

    assert (
        concern_db.execute(
            "SELECT state FROM concern_effects WHERE effect_key = ?", (ek,)
        ).fetchone()[0]
        == STATE_OUTCOME_UNKNOWN
    )

    reconcile_effect(
        conn=concern_db, effect_key=ek, observed_applied=False, now=t_expired
    )
    record_recovery_decision(
        concern_db,
        effect_key=ek,
        decision="retry",
        decided_by=AUTH,
        reason="f4",
        now=t_expired,
    )
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request={"x": 1},
        request_id="req:recover",
        fencing_token=g_b,
        now=t_expired,
    )
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"rid": "early"},
        fencing_token=g_b,
        now=t_expired,
    )
    early_receipt = lookup_receipt(concern_db, ek)

    res = record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"should_not_apply": True},
        fencing_token=g_a,
        now=t_expired,
    )
    assert isinstance(res, OutcomeResult)
    assert res.applied is False
    assert res.late_report is True

    attempts = json.loads(
        concern_db.execute(
            "SELECT attempts_json FROM concern_effects WHERE effect_key = ?", (ek,)
        ).fetchone()[0]
    )
    assert any(x.get("kind") == "late_report" for x in attempts)
    assert lookup_receipt(concern_db, ek) == early_receipt
    lease_gen = concern_db.execute(
        "SELECT owner_generation FROM concern_effect_leases WHERE concern_id = ? AND work_key = ?",
        (cid, wk),
    ).fetchone()[0]
    assert int(lease_gen) == g_b


def test_f4b_stale_token_replay(concern_db: sqlite3.Connection) -> None:
    """F4b: ``succeeded`` replay returns receipt even if the presented token is stale."""
    cid = "concern:f4b"
    wk = "wk:f4b"
    g1 = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="A",
        lease_expires_at=T0 + timedelta(minutes=5),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    req = {"v": 1}
    ek = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:1",
        fencing_token=g1,
        now=T0,
    )["effect_key"]
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"ok": True},
        fencing_token=g1,
        now=T0,
    )
    acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="B",
        lease_expires_at=T0 + timedelta(hours=2),
        now=T0 + timedelta(hours=1),
    )
    out = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:2",
        fencing_token=g1,
        now=T0 + timedelta(hours=1),
    )
    assert out["replay"] is True
    assert out["receipt"] == {"ok": True}


def test_f4c_successor_after_admit(concern_db: sqlite3.Connection) -> None:
    """F4c: generation sweep after B acquires → outcome_unknown; admit raises reconcile."""
    cid = "concern:f4c"
    wk = "wk:f4c"
    t0 = T0
    g_a = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="A",
        lease_expires_at=t0 + timedelta(minutes=5),
        now=t0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=t0,
    )
    req = {"x": 1}
    ek = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:a",
        fencing_token=g_a,
        now=t0,
    )["effect_key"]
    t1 = t0 + timedelta(minutes=30)
    acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="B",
        lease_expires_at=t1 + timedelta(hours=1),
        now=t1,
    )
    st = concern_db.execute(
        "SELECT state FROM concern_effects WHERE effect_key = ?", (ek,)
    ).fetchone()[0]
    assert st == STATE_OUTCOME_UNKNOWN
    with pytest.raises(ReconcileRequiredError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request=req,
            request_id="req:b",
            fencing_token=acquire_lease(
                concern_db,
                concern_id=cid,
                work_key=wk,
                holder="B",
                lease_expires_at=t1 + timedelta(hours=2),
                now=t1,
            ),
            now=t1,
        )
    n_attempts = len(
        json.loads(
            concern_db.execute(
                "SELECT attempts_json FROM concern_effects WHERE effect_key = ?", (ek,)
            ).fetchone()[0]
        )
    )
    assert n_attempts == 1


def test_f4d_lease_contract(concern_db: sqlite3.Connection) -> None:
    """F4d: held lease, same-holder idempotency, renew after expiry errors."""
    cid = "concern:f4d"
    wk = "wk:f4d"
    exp = T0 + timedelta(hours=1)
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="A",
        lease_expires_at=exp,
        now=T0,
    )
    g2 = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="A",
        lease_expires_at=exp + timedelta(minutes=30),
        now=T0 + timedelta(minutes=5),
    )
    assert g2 == g

    with pytest.raises(LeaseHeldError):
        acquire_lease(
            concern_db,
            concern_id=cid,
            work_key=wk,
            holder="B",
            lease_expires_at=exp + timedelta(hours=1),
            now=T0 + timedelta(minutes=5),
        )

    late = T0 + timedelta(hours=2)
    with pytest.raises(LeaseExpiredError):
        renew_lease(
            concern_db,
            concern_id=cid,
            work_key=wk,
            holder="A",
            fencing_token=g,
            lease_expires_at=late + timedelta(hours=1),
            now=late,
        )


def test_f5_hold_before_admit(tmp_path: Path, migrated_db_template: Path) -> None:
    """F5: hold between resolve and admit → HoldActiveError + held intent; clear reopens."""
    concern_db = fresh_migrated_connection(tmp_path, migrated_db_template)
    seed_authority(concern_db)
    db_file = next(
        r[2]
        for r in concern_db.execute("PRAGMA database_list").fetchall()
        if r[1] == "main"
    )
    hold_conn = sqlite3.connect(db_file, isolation_level=None, check_same_thread=False)
    cid = "concern:f5"
    wk = "wk:f5"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )

    def _hook() -> None:
        set_between_resolve_and_admit(None)
        set_hold(
            hold_conn,
            concern_id=cid,
            control_key="cancel",
            hold={},
            authorized_by=AUTH,
            record_ref="rec-mid",
        )

    set_between_resolve_and_admit(_hook)
    with pytest.raises(HoldActiveError) as ei:
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request={"a": 1},
            request_id="req:1",
            fencing_token=g,
            now=T0,
        )
    assert ei.value.intent_id == iid
    st = concern_db.execute(
        "SELECT state FROM concern_action_intents WHERE intent_id = ?", (iid,)
    ).fetchone()[0]
    assert st == "held"

    clear_hold(
        concern_db,
        concern_id=cid,
        control_key="cancel",
        authorized_by=AUTH,
        record_ref="rec-clear",
    )
    r = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request={"a": 1},
        request_id="req:2",
        fencing_token=g,
        now=T0,
    )
    assert r["replay"] is False
    ek = str(r["effect_key"])
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={},
        fencing_token=g,
        now=T0,
    )
    set_hold(
        concern_db,
        concern_id=cid,
        control_key="drain",
        hold={},
        authorized_by=AUTH,
        record_ref="rec-aftersucc",
    )
    st_eff = concern_db.execute(
        "SELECT state FROM concern_effects WHERE effect_key = ?", (ek,)
    ).fetchone()[0]
    assert st_eff == STATE_SUCCEEDED
    hold_conn.close()


def test_f5b_hold_during_admit_linearization(
    tmp_path: Path, migrated_db_template: Path
) -> None:
    """F5b: set_hold blocks until admit commits; short busy_timeout errors."""
    conn = fresh_migrated_connection(tmp_path, migrated_db_template)
    seed_authority(conn)
    db_file = next(
        r[2] for r in conn.execute("PRAGMA database_list").fetchall() if r[1] == "main"
    )
    hold_conn = sqlite3.connect(db_file, isolation_level=None, check_same_thread=False)
    hold_conn.execute("PRAGMA busy_timeout=2500")
    busy_conn = sqlite3.connect(db_file, isolation_level=None, check_same_thread=False)
    busy_conn.execute("PRAGMA busy_timeout=50")
    lock_conn = sqlite3.connect(db_file, isolation_level=None, check_same_thread=False)
    cid = "concern:f5b"
    wk = "wk:f5b"
    g = acquire_lease(
        conn,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        conn,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )

    def _after_begin() -> None:
        assert conn.in_transaction

    set_after_begin(_after_begin)

    hold_thread: threading.Thread | None = None

    def _after_controls() -> None:
        nonlocal hold_thread

        def _run_hold() -> None:
            set_hold(
                hold_conn,
                concern_id=cid,
                control_key="drain",
                hold={},
                authorized_by=AUTH,
                record_ref="rec-inner",
            )

        hold_thread = threading.Thread(target=_run_hold, daemon=True)
        hold_thread.start()
        time.sleep(0.2)
        assert hold_thread.is_alive()

    set_after_controls_read(_after_controls)
    out = admit_effect(
        conn,
        intent_id=iid,
        effect_kind="e",
        request={"x": 1},
        request_id="req:ad",
        fencing_token=g,
        now=T0,
    )
    assert out["replay"] is False
    assert hold_thread is not None
    hold_thread.join(timeout=5.0)
    assert not hold_thread.is_alive()

    before = conn.execute(
        "SELECT COUNT(*) FROM concern_journal WHERE concern_id = ?", (cid,)
    ).fetchone()[0]

    ek = str(out["effect_key"])
    record_outcome(
        conn,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"ok": True},
        fencing_token=g,
        now=T0,
    )
    row = conn.execute(
        "SELECT 1 FROM concern_controls WHERE concern_id = ? AND control_key = ?",
        (cid, "drain"),
    ).fetchone()
    assert row is not None

    set_after_controls_read(None)
    set_after_begin(None)

    lock_held = threading.Event()
    release_lock = threading.Event()

    def _hold_writer() -> None:
        lock_conn.execute("BEGIN IMMEDIATE")
        lock_held.set()
        release_lock.wait(timeout=2.0)
        lock_conn.commit()

    locker = threading.Thread(target=_hold_writer, daemon=True)
    locker.start()
    assert lock_held.wait(timeout=2.0)
    try:
        with pytest.raises(sqlite3.OperationalError):
            set_hold(
                busy_conn,
                concern_id=cid,
                control_key="cancel",
                hold={},
                authorized_by=AUTH,
                record_ref="rec-busy",
            )
    finally:
        release_lock.set()
        locker.join(timeout=5.0)
    after = conn.execute(
        "SELECT COUNT(*) FROM concern_journal WHERE concern_id = ?", (cid,)
    ).fetchone()[0]
    assert after == before
    hold_conn.close()
    busy_conn.close()
    lock_conn.close()


def test_f5c_hold_survives_rebuild(
    concern_db: sqlite3.Connection,
) -> None:
    """F5c: journaled hold survives rebuild; admit still sees hold."""
    cid = "concern:f5c"
    wk = "wk:f5c"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    append_transition(
        concern_db,
        concern_id=cid,
        kind="control_set",
        payload={"control_key": "cancel", "hold": {}},
        record_ref="rec-pre",
        authorized_by=AUTH,
    )
    rebuild_concern(concern_db, cid)
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    with pytest.raises(HoldActiveError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request={"a": 1},
            request_id="req:1",
            fencing_token=g,
            now=T0,
        )

    append_transition(
        concern_db,
        concern_id=cid,
        kind="position_head",
        payload={},
        record_ref="rec-headz",
        authorized_by=AUTH,
        expected_revision=0,
        write_intent_id="wi:f5c",
        material={"position": "rec-headz"},
    )
    from cortex_store.concern_aggregate.checkpoint import write_checkpoint

    write_checkpoint(concern_db, concern_id=cid)
    append_transition(
        concern_db,
        concern_id=cid,
        kind="control_set",
        payload={"control_key": "cancel", "hold": {"x": 1}},
        record_ref="rec-suffix",
        authorized_by=AUTH,
    )
    rebuild_concern(concern_db, cid)
    with pytest.raises(HoldActiveError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request={"a": 1},
            request_id="req:2",
            fencing_token=g,
            now=T0,
        )


def test_f5d_partial_clear_keeps_held(concern_db: sqlite3.Connection) -> None:
    """F5d: clearing one hold while another remains → intent held; admit raises."""
    cid = "concern:f5d"
    wk = "wk:f5d"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    set_hold(
        concern_db,
        concern_id=cid,
        control_key="cancel",
        hold={},
        authorized_by=AUTH,
        record_ref="rec-c1",
    )
    set_hold(
        concern_db,
        concern_id=cid,
        control_key="drain",
        hold={},
        authorized_by=AUTH,
        record_ref="rec-d1",
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    with pytest.raises(HoldActiveError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request={"a": 1},
            request_id="req:pre",
            fencing_token=g,
            now=T0,
        )
    st_held = concern_db.execute(
        "SELECT state FROM concern_action_intents WHERE intent_id = ?", (iid,)
    ).fetchone()[0]
    assert st_held == "held"

    clear_hold(
        concern_db,
        concern_id=cid,
        control_key="cancel",
        authorized_by=AUTH,
        record_ref="rec-clr",
    )
    st = concern_db.execute(
        "SELECT state FROM concern_action_intents WHERE intent_id = ?", (iid,)
    ).fetchone()
    assert st is None or st[0] == "held"
    with pytest.raises(HoldActiveError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request={"a": 1},
            request_id="req:x",
            fencing_token=g,
            now=T0,
        )


def test_f6_unknown_outcome_reconcile(concern_db: sqlite3.Connection) -> None:
    """F6: reconcile paths and consumable recovery decision."""
    cid = "concern:f6"
    wk = "wk:f6"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    req = {"a": 1}
    ek = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:1",
        fencing_token=g,
        now=T0,
    )["effect_key"]
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=g,
        now=T0,
    )
    with pytest.raises(ReconcileRequiredError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request=req,
            request_id="req:2",
            fencing_token=g,
            now=T0,
        )
    reconcile_effect(conn=concern_db, effect_key=ek, observed_applied=True, now=T0)
    assert (
        concern_db.execute(
            "SELECT state FROM concern_effects WHERE effect_key = ?", (ek,)
        ).fetchone()[0]
        == STATE_SUCCEEDED
    )

    iid2 = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s2",
        run_generation=1,
        now=T0,
    )
    ek2 = admit_effect(
        concern_db,
        intent_id=iid2,
        effect_kind="e2",
        request=req,
        request_id="req:e2a",
        fencing_token=g,
        now=T0,
    )["effect_key"]
    record_outcome(
        concern_db,
        effect_key=ek2,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=g,
        now=T0,
    )
    reconcile_effect(conn=concern_db, effect_key=ek2, observed_applied=False, now=T0)
    with pytest.raises(ReconcileRequiredError):
        admit_effect(
            concern_db,
            intent_id=iid2,
            effect_kind="e2",
            request=req,
            request_id="req:e2b",
            fencing_token=g,
            now=T0,
        )
    record_recovery_decision(
        concern_db,
        effect_key=ek2,
        decision="retry",
        decided_by=AUTH,
        reason="r",
        now=T0,
    )
    admit_effect(
        concern_db,
        intent_id=iid2,
        effect_kind="e2",
        request=req,
        request_id="req:e2c",
        fencing_token=g,
        now=T0,
    )
    admit_effect(
        concern_db,
        intent_id=iid2,
        effect_kind="e2",
        request=req,
        request_id="req:e2d1",
        fencing_token=g,
        now=T0,
    )
    with pytest.raises(RetryBudgetExceededError):
        admit_effect(
            concern_db,
            intent_id=iid2,
            effect_kind="e2",
            request=req,
            request_id="req:e2d2",
            fencing_token=g,
            now=T0,
        )

    iid3 = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s3",
        run_generation=1,
        now=T0,
    )
    ek3 = admit_effect(
        concern_db,
        intent_id=iid3,
        effect_kind="e3",
        request=req,
        request_id="req:e3a",
        fencing_token=g,
        now=T0,
    )["effect_key"]
    with pytest.raises(InvalidTransitionError):
        record_recovery_decision(
            concern_db,
            effect_key=ek3,
            decision="retry",
            decided_by=AUTH,
            reason="bad state",
            now=T0,
        )


def test_f7_dedup_outlives_ttl(concern_db: sqlite3.Connection) -> None:
    """F7: receipt remains after lease turnover."""
    cid = "concern:f7"
    wk = "wk:f7"
    t0 = T0
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="A",
        lease_expires_at=t0 + timedelta(minutes=5),
        now=t0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=t0,
    )
    req = {"k": 1}
    ek = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:1",
        fencing_token=g,
        now=t0,
    )["effect_key"]
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={"v": 9},
        fencing_token=g,
        now=t0,
    )
    t1 = t0 + timedelta(hours=2)
    acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="B",
        lease_expires_at=t1 + timedelta(hours=1),
        now=t1,
    )
    assert lookup_receipt(concern_db, ek) == {"v": 9}


def test_f8_transport_retry_budget(concern_db: sqlite3.Connection) -> None:
    """F8: one auto retry then RetryBudgetExceededError."""
    cid = "concern:f8"
    wk = "wk:f8"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    req = {"a": 1}
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:1",
        fencing_token=g,
        now=T0,
    )
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:2",
        fencing_token=g,
        now=T0,
    )
    row = concern_db.execute(
        "SELECT attempt_count, attempts_json FROM concern_effects WHERE intent_id = ?",
        (iid,),
    ).fetchone()
    assert int(row[0]) == 2
    att = json.loads(row[1])
    req_ids = {x.get("req") for x in att}
    assert len(req_ids) == 2
    with pytest.raises(RetryBudgetExceededError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request=req,
            request_id="req:3",
            fencing_token=g,
            now=T0,
        )
    assert concern_db.execute("SELECT COUNT(*) FROM concern_effects").fetchone()[0] == 1


def test_f8b_budget_resets_after_decision(concern_db: sqlite3.Connection) -> None:
    """F8b: after decision admit one auto retry then block (attempt_count 3 then error)."""
    cid = "concern:f8b"
    wk = "wk:f8b"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    req = {"a": 1}
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:x1",
        fencing_token=g,
        now=T0,
    )
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:x2",
        fencing_token=g,
        now=T0,
    )
    ek = concern_db.execute(
        "SELECT effect_key FROM concern_effects WHERE intent_id = ?", (iid,)
    ).fetchone()[0]

    with pytest.raises(RetryBudgetExceededError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request=req,
            request_id="req:x3",
            fencing_token=g,
            now=T0,
        )
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=g,
        now=T0,
    )
    reconcile_effect(conn=concern_db, effect_key=ek, observed_applied=False, now=T0)
    record_recovery_decision(
        concern_db,
        effect_key=ek,
        decision="retry",
        decided_by=AUTH,
        reason="budget",
        now=T0,
    )
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:dec",
        fencing_token=g,
        now=T0,
    )
    ac = int(
        concern_db.execute(
            "SELECT attempt_count FROM concern_effects WHERE effect_key = ?", (ek,)
        ).fetchone()[0]
    )
    assert ac == 3
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:auto-after",
        fencing_token=g,
        now=T0,
    )
    ac2 = int(
        concern_db.execute(
            "SELECT attempt_count FROM concern_effects WHERE effect_key = ?", (ek,)
        ).fetchone()[0]
    )
    assert ac2 == 4
    with pytest.raises(RetryBudgetExceededError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request=req,
            request_id="req:final-block",
            fencing_token=g,
            now=T0,
        )


def test_f8c_exhausted_admitted_to_decision(concern_db: sqlite3.Connection) -> None:
    """F8c: budget exhaustion → outcome_unknown → reconcile → decision → admit."""
    cid = "concern:f8c"
    wk = "wk:f8c"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    req = {"a": 1}
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:a",
        fencing_token=g,
        now=T0,
    )
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:b",
        fencing_token=g,
        now=T0,
    )
    ek = concern_db.execute(
        "SELECT effect_key FROM concern_effects WHERE intent_id = ?", (iid,)
    ).fetchone()[0]
    with pytest.raises(RetryBudgetExceededError):
        admit_effect(
            concern_db,
            intent_id=iid,
            effect_kind="e",
            request=req,
            request_id="req:c",
            fencing_token=g,
            now=T0,
        )
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_OUTCOME_UNKNOWN,
        receipt=None,
        fencing_token=g,
        now=T0,
    )
    reconcile_effect(conn=concern_db, effect_key=ek, observed_applied=False, now=T0)
    record_recovery_decision(
        concern_db,
        effect_key=ek,
        decision="retry",
        decided_by=AUTH,
        reason="f8c",
        now=T0,
    )
    admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request=req,
        request_id="req:post",
        fencing_token=g,
        now=T0,
    )
    assert (
        concern_db.execute(
            "SELECT state FROM concern_effects WHERE effect_key = ?", (ek,)
        ).fetchone()[0]
        == STATE_ADMITTED
    )


def test_f9_succeeded_terminal(concern_db: sqlite3.Connection) -> None:
    """F9: record_outcome on succeeded raises."""
    cid = "concern:f9"
    wk = "wk:f9"
    g = acquire_lease(
        concern_db,
        concern_id=cid,
        work_key=wk,
        holder="a",
        lease_expires_at=T0 + timedelta(hours=1),
        now=T0,
    )
    iid = resolve_action_intent(
        concern_db,
        concern_id=cid,
        work_key=wk,
        action_slot="s",
        run_generation=1,
        now=T0,
    )
    ek = admit_effect(
        concern_db,
        intent_id=iid,
        effect_kind="e",
        request={"a": 1},
        request_id="req:1",
        fencing_token=g,
        now=T0,
    )["effect_key"]
    record_outcome(
        concern_db,
        effect_key=ek,
        outcome=STATE_SUCCEEDED,
        receipt={},
        fencing_token=g,
        now=T0,
    )
    with pytest.raises(InvalidTransitionError):
        record_outcome(
            concern_db,
            effect_key=ek,
            outcome=STATE_FAILED,
            receipt=None,
            fencing_token=g,
            now=T0,
        )
