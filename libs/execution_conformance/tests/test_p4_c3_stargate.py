"""C3 — Stargate tracker lookup + restart → unknown, zero redispatch."""

from __future__ import annotations

from execution_conformance.adapters import CountingAdmitWrapper, StargatePipelineAdapter
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


def test_p4_c3_stargate_tracker_restart_unknown(tmp_path) -> None:
    tracker = StargatePipelineAdapter().tracker
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(StargatePipelineAdapter(tracker))
    exec_id = "exec-p4-c3-001"
    intent_id = "intent-c3"

    dispatch_intent(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=exec_id,
        admit_kwargs={"pipeline": "probe"},
    )
    live = wrapped.lookup(exec_id)
    assert live.found is True
    assert live.state == ExecutionState.running

    fresh = StargatePipelineAdapter()
    ledger2 = ledger.reopen()
    result = reconcile(ledger2, fresh, intent_id)
    assert result.blocked is True
    assert result.state == ExecutionState.blocked_unknown
    assert result.redispatched is False
    assert wrapped.admit_count == 1
