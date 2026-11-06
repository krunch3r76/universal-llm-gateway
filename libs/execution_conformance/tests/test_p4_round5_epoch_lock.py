"""Round-5 — epoch read unavailable must not floor_retry or block reserved rows."""

from __future__ import annotations

import threading

from execution_conformance.adapters import AgentBusAdapter, CountingAdmitWrapper
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState
from execution_conformance.store_epoch import EpochReadResult, read_store_epoch


def test_p4_epoch_lock_contention_transient_then_succeeds(
    bus_env, tmp_path, monkeypatch
) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "epoch-lock-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-lock",
        identity_key=key,
        admit_kwargs={"slug": "lock"},
    )
    row_before = ledger.get("intent-lock")
    assert row_before.state == ExecutionState.reserved
    assert row_before.retry_count == 0

    real_read = read_store_epoch
    gate = threading.Event()

    def _flaky_read(path, *, timeout=0.2):
        if not gate.is_set():
            return EpochReadResult(status="unavailable", epoch=None)
        return real_read(path, timeout=timeout)

    monkeypatch.setattr(
        "execution_conformance.adapters.read_store_epoch",
        _flaky_read,
    )
    probe = CountingAdmitWrapper(AgentBusAdapter())
    transient = reconcile(ledger, probe, "intent-lock")
    assert transient.transient is True
    assert transient.blocked is False
    assert probe.admit_count == 0
    mid = ledger.get("intent-lock")
    assert mid.state == ExecutionState.reserved
    assert mid.retry_count == 0

    gate.set()
    final = reconcile(ledger, CountingAdmitWrapper(AgentBusAdapter()), "intent-lock")
    assert final.state == ExecutionState.accepted
    assert final.redispatched is True
