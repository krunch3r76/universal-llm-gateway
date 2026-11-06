"""C2 — agent-bus idempotency + reconciler adopt."""

from __future__ import annotations

from execution_conformance.adapters import AgentBusAdapter, CountingAdmitWrapper
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


def test_p4_c2_agent_bus_idempotency_reconcile(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CountingAdmitWrapper(AgentBusAdapter())
    key = "idem-key-phase4-c2-abcdef12"
    intent_id = "intent-c2-a"

    dispatch_intent(
        ledger,
        adapter,
        intent_id=intent_id,
        identity_key=key,
        admit_kwargs={"slug": "p4-c2"},
    )
    ledger_b = ledger.reopen()
    adapter_b = AgentBusAdapter()
    result = reconcile(ledger_b, adapter_b, intent_id)
    assert result.adopted is True
    assert result.redispatched is False
    assert adapter.admit_count == 1

    first_ref = ledger_b.get(intent_id).actuator_ref
    replay = CountingAdmitWrapper(AgentBusAdapter())
    outcome = replay.admit(key, slug="p4-c2-replay")
    assert replay.admit_count == 1
    assert outcome.actuator_ref == first_ref
    assert outcome.state == ExecutionState.accepted
