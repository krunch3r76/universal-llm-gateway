"""Gate C M6 — advisory unknown_reason steady across reconciles."""

from __future__ import annotations

import pytest
from execution_conformance.adapters import CursorBridgeBusModelAdapter
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db.connection import init_db

    init_db()


def test_p4_m6_reserved_receipt_reconcile_steady(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CursorBridgeBusModelAdapter()
    key = "steady-bridge-key-abcdef12"
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-steady",
        identity_key=key,
        admit_kwargs={"slug": "steady"},
    )
    states = []
    for _ in range(3):
        r = reconcile(ledger, adapter, "intent-steady")
        states.append(r.state)
    assert states == [
        ExecutionState.accepted,
        ExecutionState.accepted,
        ExecutionState.accepted,
    ]
    row = ledger.get("intent-steady")
    assert row is not None
    assert row.payload.get("owner_unknown_reason") == "msg_without_correlated_reply"
