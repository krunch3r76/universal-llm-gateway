"""C4 — CDP inflight ledger durability + open-without-proof unknown."""

from __future__ import annotations

from execution_conformance.adapters import CdpInflightAdapter, CountingAdmitWrapper
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


def test_p4_c4_cdp_ledger_durable(cdp_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CountingAdmitWrapper(CdpInflightAdapter())
    exec_id = "cdp-exec-p4-c4"
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-c4-durable",
        identity_key=exec_id,
        admit_kwargs={"thread_id": "22398"},
    )
    ledger.reopen()
    fresh = CdpInflightAdapter()
    lookup = fresh.lookup(exec_id)
    assert lookup.found is True
    assert adapter.admit_count == 1


def test_p4_c4_cdp_open_without_proof(cdp_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(CdpInflightAdapter())
    exec_id = "cdp-exec-p4-c4-open"
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-c4-open",
        identity_key=exec_id,
        admit_kwargs={"thread_id": "22398"},
    )
    result = reconcile(ledger.reopen(), CdpInflightAdapter(), "intent-c4-open")
    assert result.blocked is False
    assert result.state == ExecutionState.accepted
    assert result.redispatched is False
    assert wrapped.admit_count == 1
    row = ledger.get("intent-c4-open")
    assert row is not None
    assert row.payload.get("owner_unknown_reason") == "open_leg_without_board_proof"
