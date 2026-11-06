"""Round-6 — claim_retry lease TTL, admit errors, compensate re-block."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from execution_conformance.adapters import (
    AdmitOutcome,
    AgentBusAdapter,
    CountingAdmitWrapper,
)
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import (
    CompensatingAction,
    compensate_unknown,
    dispatch_intent,
    reconcile,
)
from execution_conformance.states import ExecutionState


@dataclass
class BoomAdmitAdapter(AgentBusAdapter):
    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        raise RuntimeError("boom")


def test_p4_claim_retry_lease_transient_then_stale(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "claim-lease-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-lease",
        identity_key=key,
        admit_kwargs={"slug": "lease"},
    )
    assert ledger.claim_retry("intent-lease", inflight_at=1000.0)
    r1 = reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-lease",
        clock=lambda: 1005.0,
        claim_ttl_seconds=30.0,
    )
    assert r1.transient is True
    assert r1.redispatched is False
    assert r1.blocked is False
    r2 = reconcile(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        "intent-lease",
        clock=lambda: 1035.0,
        claim_ttl_seconds=30.0,
    )
    assert r2.blocked is True
    assert r2.state == ExecutionState.blocked_unknown
    assert ledger.get("intent-lease").payload.get("blocked_reason") == "retry_claim_stale"


def test_p4_reconcile_admit_error_blocks(tmp_path, bus_env) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "admit-error-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-err",
        identity_key=key,
        admit_kwargs={"slug": "err"},
    )
    probe = CountingAdmitWrapper(BoomAdmitAdapter())
    result = reconcile(ledger, probe, "intent-err")
    assert result.blocked is True
    assert probe.admit_count == 1
    row = ledger.get("intent-err")
    assert row.state == ExecutionState.blocked_unknown
    assert row.payload.get("blocked_reason") == "admit_error"


def test_p4_dispatch_admit_error_persists_then_raises(tmp_path, bus_env) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "dispatch-err-key-abcdef12"
    with pytest.raises(RuntimeError, match="boom"):
        dispatch_intent(
            ledger,
            CountingAdmitWrapper(BoomAdmitAdapter()),
            intent_id="intent-disp-err",
            identity_key=key,
            admit_kwargs={"slug": "err"},
        )
    row = ledger.get("intent-disp-err")
    assert row.state == ExecutionState.blocked_unknown
    assert row.payload.get("blocked_reason") == "admit_error"


def test_p4_compensate_owner_miss_reblocks(bus_env, tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "comp-reblock-key-abcdef12"
    ledger.reserve(
        intent_id="intent-comp",
        actuator="agent_bus",
        identity_key=key,
        store_epoch=AgentBusAdapter().store_epoch(),
    )
    ledger.set_state("intent-comp", ExecutionState.outcome_unknown)
    ledger.set_state("intent-comp", ExecutionState.blocked_unknown)
    ledger.merge_payload("intent-comp", {"blocked_reason": "owner_miss"})
    compensate_unknown(
        ledger,
        "intent-comp",
        CompensatingAction(authorized_by="reviewer", reason="x"),
    )
    result = reconcile(ledger, AgentBusAdapter(), "intent-comp")
    assert result.blocked is True
    assert result.transient is False
