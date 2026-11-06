"""Round-5/6 — real file lock → epoch unavailable → transient reconcile."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing

from execution_conformance.adapters import AgentBusAdapter, CountingAdmitWrapper
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState
from execution_conformance.store_epoch import read_store_epoch


def _owner_fingerprint(path) -> tuple:
    with closing(sqlite3.connect(path)) as conn:
        tables = tuple(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        )
        dv = conn.execute("PRAGMA data_version").fetchone()[0]
    return tables, dv


def test_p4_epoch_lock_contention_transient_then_succeeds(bus_env, tmp_path) -> None:
    path = bus_env
    with closing(sqlite3.connect(path, timeout=5.0)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "epoch-lock-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-lock",
        identity_key=key,
        admit_kwargs={"slug": "lock"},
    )
    before = _owner_fingerprint(path)
    lock_ready = threading.Event()
    release = threading.Event()

    def _hold_exclusive() -> None:
        conn = sqlite3.connect(path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN EXCLUSIVE")
        lock_ready.set()
        release.wait(timeout=10.0)
        conn.commit()
        conn.close()

    locker = threading.Thread(target=_hold_exclusive)
    locker.start()
    lock_ready.wait(timeout=5.0)
    assert read_store_epoch(path, timeout=0.05).status == "unavailable"
    probe = CountingAdmitWrapper(AgentBusAdapter())
    transient = reconcile(ledger, probe, "intent-lock")
    assert transient.transient is True
    assert transient.blocked is False
    assert probe.admit_count == 0
    mid = ledger.get("intent-lock")
    assert mid.state == ExecutionState.reserved
    assert mid.retry_count == 0
    release.set()
    locker.join()
    after_lock = _owner_fingerprint(path)
    assert before == after_lock
    final = reconcile(ledger, CountingAdmitWrapper(AgentBusAdapter()), "intent-lock")
    assert final.state == ExecutionState.accepted
    assert final.redispatched is True


def test_p4_dispatch_under_epoch_unavailable_no_owner_writes(
    bus_env, tmp_path, monkeypatch
) -> None:
    path = bus_env
    before = _owner_fingerprint(path)

    def _always_unavailable(db_path, *, timeout=0.2):
        from execution_conformance.store_epoch import EpochReadResult

        return EpochReadResult(status="unavailable", epoch=None)

    monkeypatch.setattr(
        "execution_conformance.adapters.read_store_epoch",
        _always_unavailable,
    )
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "unavail-dispatch-key-abcdef12"
    dispatch_intent(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-unavail",
        identity_key=key,
        admit_kwargs={"slug": "u"},
    )
    after = _owner_fingerprint(path)
    assert before == after


def test_p4_read_only_connection_rejects_ddl(bus_env) -> None:
    path = bus_env
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as conn:
        conn.execute("PRAGMA query_only=ON")
        try:
            conn.execute("CREATE TABLE should_not_exist (id INTEGER)")
            raised = False
        except sqlite3.OperationalError:
            raised = True
    assert raised
