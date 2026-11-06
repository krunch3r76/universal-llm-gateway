"""Round-5/7 — real file lock → epoch unavailable → transient reconcile."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest
from execution_conformance.adapters import AgentBusAdapter, CountingAdmitWrapper
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState
from execution_conformance.store_epoch import read_store_epoch


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sidecars(path: Path) -> list[Path]:
    parent = path.parent
    stem = path.name
    return [
        p
        for p in (parent / f"{stem}-wal", parent / f"{stem}-shm")
        if p.exists()
    ]


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
    before = _file_sha256(path)
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
    assert _file_sha256(path) == before
    assert not _sidecars(path)
    final = reconcile(ledger, CountingAdmitWrapper(AgentBusAdapter()), "intent-lock")
    assert final.state == ExecutionState.accepted
    assert final.redispatched is True


def test_p4_unprovisioned_owner_zero_writes_and_no_wal(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "bare.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    from agent_bus_store.db.connection import init_db

    init_db()
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    before = _file_sha256(db)
    side_before = _sidecars(db)
    assert read_store_epoch(db).status == "unprovisioned"
    assert _file_sha256(db) == before
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    ledger.reserve(
        intent_id="intent-unprov",
        actuator="agent_bus",
        identity_key="unprov-key-abcdef12",
        store_epoch="",
        payload={"store_epoch_pending": True},
    )
    assert read_store_epoch(db).status == "unprovisioned"
    assert _file_sha256(db) == before
    assert _sidecars(db) == side_before


def test_p4_wal_db_main_file_unchanged_on_read_paths(bus_env, tmp_path) -> None:
    path = bus_env
    before = _file_sha256(path)
    assert read_store_epoch(path).status == "ok"
    assert _file_sha256(path) == before
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    ledger.reserve(
        intent_id="intent-wal",
        actuator="agent_bus",
        identity_key="wal-read-key-abcdef12",
        store_epoch="epoch-wal-test",
    )
    assert read_store_epoch(path).status == "ok"
    assert _file_sha256(path) == before


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
