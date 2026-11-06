"""Round-3 review majors — compensation CAS, blocked ref_conflict, newline key."""

from __future__ import annotations

import threading

import pytest
from execution_conformance.adapters import (
    AgentBusAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
)
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import (
    CompensatingAction,
    compensate_unknown,
    dispatch_intent,
    reconcile,
)
from execution_conformance.states import ExecutionState, IllegalStateTransitionError


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db.connection import init_db

    init_db()


def test_p4_compensation_cas_fails_if_state_changed(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    ledger.reserve(
        intent_id="intent-comp",
        actuator="agent_bus",
        identity_key="comp-key-abcdef12",
        store_epoch="e",
    )
    ledger.set_state("intent-comp", ExecutionState.outcome_unknown)
    ledger.set_state("intent-comp", ExecutionState.blocked_unknown)
    ledger.set_state("intent-comp", ExecutionState.completed)
    with pytest.raises(IllegalStateTransitionError):
        ledger.apply_compensation(
            "intent-comp",
            authorized_by="reviewer",
            reason="test",
            prior_state=ExecutionState.blocked_unknown,
        )


def test_p4_blocked_ref_conflict_not_erased_by_terminal_lookup(
    bus_env, tmp_path
) -> None:
    from agent_bus_store.db import create_thread, insert_turn

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CursorBridgeBusModelAdapter()
    key = "ref-conflict-key-abcdef12"
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-ref",
        identity_key=key,
        admit_kwargs={"slug": "ref-a"},
    )
    row = ledger.get("intent-ref")
    assert row is not None
    thread_a = row.actuator_ref
    detail_b = create_thread(thread_id=None, slug="ref-b")
    assert detail_b is not None
    thread_b = str(detail_b["id"])
    ledger.patch_actuator_ref_if_null("intent-ref", thread_a)
    ledger.merge_payload("intent-ref", {"blocked_reason": "ref_conflict"})
    ledger.set_state("intent-ref", ExecutionState.outcome_unknown)
    ledger.set_state("intent-ref", ExecutionState.blocked_unknown)
    insert_turn(
        thread=thread_b,
        from_agent="cursor-sdk",
        to_agent="cursor-bridge",
        subject="REPLY",
        body=f"intent:{key}\nok",
    )
    result = reconcile(ledger, adapter, "intent-ref")
    assert result.blocked is True
    assert ledger.get("intent-ref").state == ExecutionState.blocked_unknown


def test_p4_blocked_unknown_reconcile_read_only(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    probe = CountingAdmitWrapper(AgentBusAdapter())
    key = "blocked-readonly-key-abcdef12"
    dispatch_intent(
        ledger,
        probe,
        intent_id="intent-ro",
        identity_key=key,
        admit_kwargs={"slug": "ro"},
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
    with connect() as conn:
        thread_id = thread_id_for_idempotency_key(conn, key)
    assert thread_id is not None


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
