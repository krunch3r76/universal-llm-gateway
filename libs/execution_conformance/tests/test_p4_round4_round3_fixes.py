"""Round-3/5 — blocked read-only reconcile, concurrency, identity validation."""

from __future__ import annotations

import threading

import pytest
from execution_conformance.adapters import (
    AgentBusAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
)
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


def test_p4_blocked_unknown_reconcile_read_only(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    probe = CountingAdmitWrapper(AgentBusAdapter())
    key = "blocked-readonly-key-abcdef12"
    ledger.reserve(
        intent_id="intent-ro",
        actuator="agent_bus",
        identity_key=key,
        store_epoch=AgentBusAdapter().store_epoch(),
    )
    ledger.set_state("intent-ro", ExecutionState.outcome_unknown)
    ledger.set_state("intent-ro", ExecutionState.blocked_unknown)
    ledger.merge_payload("intent-ro", {"blocked_reason": "owner_miss"})
    before = probe.admit_count
    for _ in range(3):
        r = reconcile(ledger, CountingAdmitWrapper(AgentBusAdapter()), "intent-ro")
        assert r.blocked is True
    assert probe.admit_count == before


def test_p4_concurrent_dispatch_and_reconcile_single_owner(bus_env, tmp_path) -> None:
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "concurrent-owner-key-abcdef12"
    dispatch_intent(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-conc",
        identity_key=key,
        admit_kwargs={"slug": "conc"},
    )

    def _reconcile() -> None:
        reconcile(
            ledger.reopen(),
            CountingAdmitWrapper(AgentBusAdapter()),
            "intent-conc",
        )

    def _dispatch_again() -> None:
        dispatch_intent(
            ledger.reopen(),
            CountingAdmitWrapper(AgentBusAdapter()),
            intent_id="intent-conc",
            identity_key=key,
            admit_kwargs={"slug": "conc"},
        )

    threads = [
        threading.Thread(target=_reconcile),
        threading.Thread(target=_dispatch_again),
        threading.Thread(target=_reconcile),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    row = ledger.get("intent-conc")
    assert row is not None
    assert row.state == ExecutionState.accepted
    assert row.actuator_ref is not None
    with connect() as conn:
        thread_id = thread_id_for_idempotency_key(conn, key)
    assert thread_id is not None
    assert str(thread_id) == row.actuator_ref


def test_p4_newline_identity_key_rejected_before_reserve(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    with pytest.raises(ValueError):
        dispatch_intent(
            ledger,
            AgentBusAdapter(),
            intent_id="intent-bad",
            identity_key="short\nkey",
            admit_kwargs={"slug": "bad"},
        )


def test_p4_cursor_bridge_newline_key_rejected(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    with pytest.raises(ValueError):
        dispatch_intent(
            ledger,
            CursorBridgeBusModelAdapter(),
            intent_id="intent-bridge-bad",
            identity_key="bridge\nkey-abcdef12",
            admit_kwargs={"slug": "bad"},
        )
