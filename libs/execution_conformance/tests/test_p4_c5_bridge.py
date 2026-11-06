"""C5 — cursor bridge bus model: MSG durable, no REPLY → advisory accepted."""

from __future__ import annotations

import pytest
from execution_conformance.adapters import (
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
)
from execution_conformance.ledger import IntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db.connection import init_db

    init_db()
    yield


def test_p4_c5_bridge_msg_without_reply(bus_env, tmp_path) -> None:
    ledger = IntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(CursorBridgeBusModelAdapter())
    identity = "bridge-intent-p4-c5"
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-c5",
        identity_key=identity,
        admit_kwargs={"slug": "bridge-p4"},
    )
    result = reconcile(ledger.reopen(), CursorBridgeBusModelAdapter(), "intent-c5")
    assert result.blocked is False
    assert result.state == ExecutionState.accepted
    assert result.redispatched is False
    assert wrapped.admit_count == 1
    row = ledger.get("intent-c5")
    assert row is not None
    assert row.payload.get("owner_unknown_reason") == "msg_without_correlated_reply"
