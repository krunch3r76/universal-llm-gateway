"""Round-2: dispatch_intent re-entry must not bypass reconcile guards."""

from __future__ import annotations

from execution_conformance.adapters import (
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
    StargatePipelineAdapter,
)
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent
from execution_conformance.states import ExecutionState


def test_p4_round2_dispatch_twice_cdp(tmp_path, monkeypatch, bus_env) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(CdpInflightAdapter())
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-cdp-twice",
        identity_key="cdp-twice-exec-id",
        admit_kwargs={"thread_id": "22398"},
    )
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-cdp-twice",
        identity_key="cdp-twice-exec-id",
        admit_kwargs={"thread_id": "22398"},
    )
    assert wrapped.admit_count == 1


def test_p4_round2_dispatch_twice_stargate_after_restart(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    t1 = StargatePipelineAdapter()
    wrapped = CountingAdmitWrapper(t1)
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-stargate-twice",
        identity_key="stargate-twice-exec",
        admit_kwargs={"pipeline": "p"},
    )
    fresh = CountingAdmitWrapper(StargatePipelineAdapter())
    dispatch_intent(
        ledger,
        fresh,
        intent_id="intent-stargate-twice",
        identity_key="stargate-twice-exec",
        admit_kwargs={"pipeline": "p"},
    )
    assert wrapped.admit_count == 1
    assert fresh.admit_count == 0


def test_p4_round2_dispatch_twice_blocked_unknown(
    tmp_path, monkeypatch, bus_env
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(CursorBridgeBusModelAdapter())
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-blocked",
        identity_key="bridge-block-key-12",
        admit_kwargs={"slug": "b"},
    )
    from execution_conformance.reconciler import reconcile

    reconcile(ledger, CursorBridgeBusModelAdapter(), "intent-blocked")
    assert ledger.get("intent-blocked").state == ExecutionState.accepted
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-blocked",
        identity_key="bridge-block-key-12",
        admit_kwargs={"slug": "b"},
    )
    assert wrapped.admit_count == 1


def test_p4_round2_dispatch_twice_completed(tmp_path, monkeypatch, bus_env) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(AgentBusAdapter())
    key = "completed-key-abcdef12"
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-done",
        identity_key=key,
        admit_kwargs={"slug": "done"},
    )
    ledger.set_state("intent-done", ExecutionState.completed)
    dispatch_intent(
        ledger,
        wrapped,
        intent_id="intent-done",
        identity_key=key,
        admit_kwargs={"slug": "done"},
    )
    assert wrapped.admit_count == 1


def test_p4_round2_bridge_tag_special_chars(tmp_path, monkeypatch, bus_env) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CursorBridgeBusModelAdapter()
    key = "intent_%wild\\key"
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-special",
        identity_key=key,
        admit_kwargs={"slug": "special"},
    )
    lookup = adapter.lookup(key)
    assert lookup.found is True
    assert lookup.unknown_reason == "msg_without_correlated_reply"
