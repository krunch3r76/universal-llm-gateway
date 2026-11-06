"""C9 — compensation without authorization raises."""

from __future__ import annotations

import pytest
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import CompensatingAction, compensate_unknown
from execution_conformance.states import ExecutionState


def test_p4_c9_compensation_falsifier(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    ledger.reserve(
        intent_id="intent-c9",
        actuator="agent_bus",
        identity_key="c9-key-abcdef12",
        store_epoch="test-epoch",
    )
    ledger.set_state("intent-c9", ExecutionState.outcome_unknown)
    ledger.set_state("intent-c9", ExecutionState.blocked_unknown)
    with pytest.raises(PermissionError):
        compensate_unknown(ledger, "intent-c9", None)

    row = compensate_unknown(
        ledger,
        "intent-c9",
        CompensatingAction(authorized_by="reviewer", reason="test"),
    )
    assert row.state == ExecutionState.outcome_unknown
    assert row.state == ExecutionState.outcome_unknown
