"""Round-5 Minor 4 — running local + advisory accepted stays running."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from execution_conformance.adapters import AdmitOutcome, OwnerLookup
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState


@dataclass
class AdvisoryAcceptedAdapter:
    name: str = "agent_bus"
    admit_idempotent: bool = False

    def store_epoch(self) -> str:
        return "epoch-test-abcdef01"

    def health(self) -> bool:
        return True

    def lookup(self, identity_key: str) -> OwnerLookup:
        return OwnerLookup(
            found=True,
            actuator_ref="owner-ref-1",
            state=ExecutionState.accepted,
            store_epoch="epoch-test-abcdef01",
            unknown_reason="open_leg_without_board_proof",
        )

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        raise RuntimeError("no admit")


def test_p4_advisory_unknown_keeps_running_rank(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    ledger.reserve(
        intent_id="intent-run",
        actuator="agent_bus",
        identity_key="advisory-run-key-abcdef12",
        store_epoch="epoch-test-abcdef01",
    )
    ledger.set_state("intent-run", ExecutionState.running)
    ledger.record_receipt(
        "intent-run", actuator_ref="owner-ref-1", state=ExecutionState.running
    )
    result = reconcile(ledger, AdvisoryAcceptedAdapter(), "intent-run")
    assert result.state == ExecutionState.running
    assert result.blocked is False
