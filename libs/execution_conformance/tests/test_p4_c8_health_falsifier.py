"""C8 — health true while owner lookup misses must block, not adopt."""

from __future__ import annotations

from execution_conformance.adapters import HealthOnlyAdapter
from execution_conformance.ledger import IntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState
from execution_conformance.test_hooks import dispatch_crash_after_reserve


def test_p4_c8_health_alone_falsifier(tmp_path) -> None:
    adapter = HealthOnlyAdapter()
    assert adapter.health() is True
    ledger = IntentLedger(tmp_path / "intents.db")
    dispatch_crash_after_reserve(
        ledger,
        adapter,
        intent_id="intent-c8",
        identity_key="health-probe-key-12",
    )
    result = reconcile(ledger, adapter, "intent-c8")
    assert result.blocked is True
    assert result.state == ExecutionState.blocked_unknown
    assert result.adopted is False
