"""Round-6 — identity key length by actuator; resolve_blocked audit."""

from __future__ import annotations

import pytest
from execution_conformance.adapters import StargatePipelineAdapter
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import (
    dispatch_intent,
    resolve_blocked,
    resolve_ref_conflict,
)
from execution_conformance.states import ExecutionState


def test_p4_short_key_allowed_on_stargate_not_agent_bus(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    dispatch_intent(
        ledger,
        StargatePipelineAdapter(),
        intent_id="intent-short",
        identity_key="k1",
        admit_kwargs={"pipeline": "p"},
    )
    row = ledger.get("intent-short")
    assert row.state in (ExecutionState.accepted, ExecutionState.running)


def test_p4_short_key_rejected_on_agent_bus(tmp_path, bus_env) -> None:
    from execution_conformance.adapters import AgentBusAdapter

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    with pytest.raises(ValueError):
        dispatch_intent(
            ledger,
            AgentBusAdapter(),
            intent_id="intent-bad",
            identity_key="k1",
            admit_kwargs={"slug": "x"},
        )


def test_p4_resolve_blocked_audit_and_negative_paths(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    ledger.reserve(
        intent_id="intent-res",
        actuator="agent_bus",
        identity_key="resolve-key-abcdef12",
        store_epoch="e",
    )
    ledger.set_state("intent-res", ExecutionState.outcome_unknown)
    ledger.set_state("intent-res", ExecutionState.blocked_unknown)
    ledger.merge_payload("intent-res", {"blocked_reason": "owner_miss"})
    resolve_blocked(
        ledger, "intent-res", actor="reviewer", reason="done", target_state=ExecutionState.failed
    )
    with ledger._connect() as conn:  # noqa: SLF001
        count = conn.execute("SELECT COUNT(*) FROM execution_compensations").fetchone()[0]
    assert count == 1
    with pytest.raises(ValueError):
        resolve_blocked(
            ledger,
            "intent-res",
            actor="reviewer",
            reason="again",
            target_state=ExecutionState.failed,
        )
    ledger2 = ReferenceSqliteIntentLedger(tmp_path / "other.db")
    ledger2.reserve(
        intent_id="intent-ref",
        actuator="agent_bus",
        identity_key="ref-key-abcdef12",
        store_epoch="e",
    )
    with pytest.raises(ValueError):
        resolve_ref_conflict(
            ledger2, "intent-ref", actor="reviewer", reason="nope"
        )
