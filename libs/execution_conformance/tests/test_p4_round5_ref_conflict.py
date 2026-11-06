"""Round-5 ref_conflict stickiness, stub owner lookups, resolution paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from execution_conformance.adapters import AdmitOutcome, OwnerLookup
from execution_conformance.ledger import IntentStore, ReferenceSqliteIntentLedger
from execution_conformance.reconciler import (
    CompensatingAction,
    compensate_unknown,
    reconcile,
    resolve_ref_conflict,
)
from execution_conformance.states import ExecutionState, IllegalStateTransitionError


@dataclass
class StubOwnerAdapter:
    """Deterministic owner lookup for ref_conflict matrix tests."""

    lookup_result: OwnerLookup
    name: str = "agent_bus"
    admit_idempotent: bool = False

    def store_epoch(self) -> str:
        return self.lookup_result.store_epoch

    def health(self) -> bool:
        return True

    def lookup(self, identity_key: str) -> OwnerLookup:
        return self.lookup_result

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        raise RuntimeError("stub does not admit")


def _ledger(tmp_path) -> ReferenceSqliteIntentLedger:
    return ReferenceSqliteIntentLedger(tmp_path / "intents.db")


def _reserve_accepted(
    ledger: IntentStore, *, intent_id: str, key: str, ref: str, epoch: str = "epoch-a"
) -> None:
    ledger.reserve(
        intent_id=intent_id,
        actuator="agent_bus",
        identity_key=key,
        store_epoch=epoch,
    )
    ledger.record_receipt(intent_id, actuator_ref=ref, state=ExecutionState.accepted)


def test_p4_ref_conflict_matrix(tmp_path) -> None:
    epoch = "store-epoch-abcdef01"
    key = "ref-matrix-key-abcdef12"
    adapter = StubOwnerAdapter(
        OwnerLookup(
            found=True,
            state=ExecutionState.completed,
            actuator_ref="B",
            store_epoch=epoch,
            epoch_status="ok",
        )
    )

    ledger = _ledger(tmp_path)
    _reserve_accepted(ledger, intent_id="intent-a", key=key, ref="A", epoch=epoch)
    result = reconcile(ledger, adapter, "intent-a")
    assert result.blocked is True
    row = ledger.get("intent-a")
    assert row.state == ExecutionState.blocked_unknown
    assert row.payload.get("blocked_reason") == "ref_conflict"

    result2 = reconcile(ledger, adapter, "intent-a")
    assert result2.blocked is True
    assert ledger.get("intent-a").payload.get("blocked_reason") == "ref_conflict"

    ledger = _ledger(tmp_path / "c")
    _reserve_accepted(ledger, intent_id="intent-c", key=key, ref="A", epoch=epoch)
    ledger.set_state("intent-c", ExecutionState.outcome_unknown)
    ledger.set_state("intent-c", ExecutionState.blocked_unknown)
    ledger.merge_payload("intent-c", {"blocked_reason": "owner_miss"})
    reconcile(ledger, adapter, "intent-c")
    assert ledger.get("intent-c").payload.get("blocked_reason") == "ref_conflict"

    ledger = _ledger(tmp_path / "d")
    match = StubOwnerAdapter(
        OwnerLookup(
            found=True,
            state=ExecutionState.completed,
            actuator_ref="A",
            store_epoch=epoch,
            epoch_status="ok",
        )
    )
    _reserve_accepted(ledger, intent_id="intent-d", key=key, ref="A", epoch=epoch)
    ledger.set_state("intent-d", ExecutionState.outcome_unknown)
    ledger.set_state("intent-d", ExecutionState.blocked_unknown)
    ledger.merge_payload("intent-d", {"blocked_reason": "owner_miss"})
    r = reconcile(ledger, match, "intent-d")
    assert r.blocked is False
    assert ledger.get("intent-d").state == ExecutionState.completed

    ledger = _ledger(tmp_path / "e")
    _reserve_accepted(ledger, intent_id="intent-e", key=key, ref="A", epoch=epoch)
    reconcile(ledger, adapter, "intent-e")
    compensate_unknown(
        ledger,
        "intent-e",
        CompensatingAction(authorized_by="reviewer", reason="retry"),
    )
    assert ledger.get("intent-e").state == ExecutionState.outcome_unknown
    reconcile(ledger, adapter, "intent-e")
    assert ledger.get("intent-e").payload.get("blocked_reason") == "ref_conflict"
    resolve_ref_conflict(ledger, "intent-e", actor="reviewer", reason="terminal-exit")
    assert ledger.get("intent-e").state == ExecutionState.failed


def test_p4_compensation_cas_fails_if_state_changed(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")

    def _comp_count() -> int:
        with ledger._connect() as conn:  # noqa: SLF001
            return int(
                conn.execute("SELECT COUNT(*) FROM execution_compensations").fetchone()[
                    0
                ]
            )

    ledger.reserve(
        intent_id="intent-comp",
        actuator="agent_bus",
        identity_key="comp-key-abcdef12",
        store_epoch="e",
    )
    ledger.set_state("intent-comp", ExecutionState.outcome_unknown)
    ledger.set_state("intent-comp", ExecutionState.blocked_unknown)
    with ledger._connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE execution_intents SET state = ? WHERE intent_id = ?",
            (ExecutionState.completed.value, "intent-comp"),
        )
        conn.commit()
    with pytest.raises(IllegalStateTransitionError):
        ledger.apply_compensation(
            "intent-comp",
            authorized_by="reviewer",
            reason="test",
            prior_state=ExecutionState.blocked_unknown,
        )
    assert _comp_count() == 0
