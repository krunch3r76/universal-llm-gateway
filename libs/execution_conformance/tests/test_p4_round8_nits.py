"""Round-8 review nits — sticky reasons, epoch timer reset, promote guards."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing

import pytest
from execution_conformance.adapters import AgentBusAdapter, CountingAdmitWrapper
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import (
    STICKY_BLOCKED_REASONS,
    ReferenceSqliteIntentLedger,
)
from execution_conformance.reconciler import _block_unknown, dispatch_intent, reconcile
from execution_conformance.states import ExecutionState
from execution_conformance.store_epoch import read_store_epoch


def test_p4_running_block_promote_stays_running(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "l.db")
    ledger.reserve(
        intent_id="run1",
        actuator="agent_bus",
        identity_key="sticky-run-key-abcdef12",
        store_epoch="e1",
    )
    ledger.set_state("run1", ExecutionState.accepted)
    ledger.set_state("run1", ExecutionState.running)
    _block_unknown(ledger, "run1", reason="owner_miss")
    row = ledger.promote_blocked_adoption(
        "run1",
        target=ExecutionState.accepted,
        actuator_ref="t1",
    )
    assert row is not None
    assert row.state == ExecutionState.running


def test_p4_block_intent_cas_terminal_noop(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "l.db")
    ledger.reserve(
        intent_id="term1",
        actuator="agent_bus",
        identity_key="term-key-abcdef1234",
        store_epoch="e1",
        payload={"marker": 1},
    )
    ledger.set_state("term1", ExecutionState.completed)
    before = ledger.get("term1").payload
    assert ledger.block_intent_cas("term1", reason="owner_miss") is None
    after = ledger.get("term1")
    assert after.state == ExecutionState.completed
    assert after.payload == before


@pytest.mark.parametrize(
    "initial_reason,weak_reason",
    [
        ("ref_conflict", "owner_miss"),
        ("duplicate_msg", "owner_miss"),
        ("epoch_mismatch", "owner_miss"),
        ("epoch_unverified", "epoch_unavailable_stale"),
    ],
)
def test_p4_sticky_blocked_reason_preserved(
    tmp_path, initial_reason: str, weak_reason: str
) -> None:
    assert initial_reason in STICKY_BLOCKED_REASONS
    ledger = ReferenceSqliteIntentLedger(tmp_path / "l.db")
    ledger.reserve(
        intent_id="sticky",
        actuator="agent_bus",
        identity_key=f"sticky-{initial_reason}-abcdef12",
        store_epoch="e1",
    )
    _block_unknown(ledger, "sticky", reason=initial_reason)
    _block_unknown(ledger, "sticky", reason=weak_reason)
    row = ledger.get("sticky")
    assert row.payload.get("blocked_reason") == initial_reason


def test_p4_epoch_unavailable_stale_boundary(
    tmp_path, bus_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from execution_conformance.store_epoch import EpochReadResult

    monkeypatch.setattr(
        "execution_conformance.adapters.read_store_epoch",
        lambda *_a, **_k: EpochReadResult(status="unavailable", epoch=None),
    )
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "epoch-stale-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-stale",
        identity_key=key,
        admit_kwargs={"slug": "stale"},
    )
    t0 = 1000.0
    reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-stale",
        clock=lambda: t0,
        unavailable_max_age_seconds=300.0,
    )
    row = ledger.get("intent-stale")
    assert row.payload.get("epoch_unavailable_since") == t0
    mid = reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-stale",
        clock=lambda: t0 + 299.0,
        unavailable_max_age_seconds=300.0,
    )
    assert mid.transient is True
    assert mid.blocked is False
    late = reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-stale",
        clock=lambda: t0 + 301.0,
        unavailable_max_age_seconds=300.0,
    )
    assert late.blocked is True
    assert late.epoch_unavailable_stale is True


def test_p4_epoch_unavailable_since_cleared_after_recovery(
    bus_env, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = bus_env
    with closing(sqlite3.connect(path, timeout=5.0)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "epoch-reset-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-reset",
        identity_key=key,
        admit_kwargs={"slug": "reset"},
    )
    def _run_lock(ready: threading.Event, release: threading.Event) -> None:
        conn = sqlite3.connect(path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN EXCLUSIVE")
        ready.set()
        release.wait(timeout=10.0)
        conn.commit()
        conn.close()

    lock_ready = threading.Event()
    release = threading.Event()
    t = threading.Thread(target=_run_lock, args=(lock_ready, release))
    t.start()
    lock_ready.wait(timeout=5.0)
    t0 = 1000.0
    r1 = reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-reset",
        clock=lambda: t0,
        unavailable_max_age_seconds=300.0,
    )
    assert r1.transient is True and not r1.blocked
    release.set()
    t.join()
    r2 = reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-reset",
        clock=lambda: t0 + 1.0,
        unavailable_max_age_seconds=300.0,
    )
    assert r2.redispatched is True
    row = ledger.get("intent-reset")
    assert "epoch_unavailable_since" not in (row.payload or {})
    lock_ready2 = threading.Event()
    release2 = threading.Event()
    t_lock = threading.Thread(target=_run_lock, args=(lock_ready2, release2))
    t_lock.start()
    lock_ready2.wait(timeout=5.0)
    r3 = reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-reset",
        clock=lambda: t0 + 400.0,
        unavailable_max_age_seconds=300.0,
    )
    assert r3.transient is True
    assert r3.blocked is False
    release2.set()
    t_lock.join()


def test_p4_dispatch_skips_reserve_when_epoch_unavailable(
    bus_env, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = bus_env

    def _always_unavailable(db_path, *, timeout=0.2):
        from execution_conformance.store_epoch import EpochReadResult

        return EpochReadResult(status="unavailable", epoch=None)

    monkeypatch.setattr(
        "execution_conformance.adapters.read_store_epoch",
        _always_unavailable,
    )
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "no-reserve-key-abcdef12"
    out = dispatch_intent(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-skip",
        identity_key=key,
        admit_kwargs={"slug": "skip"},
    )
    assert out.transient is True
    assert ledger.get("intent-skip") is None
