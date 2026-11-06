"""Review blockers — regression tests."""

from __future__ import annotations

import threading

import pytest
from execution_conformance.adapters import (
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
)
from execution_conformance.crash_hooks import (
    dispatch_crash_after_admit_before_receipt,
    dispatch_crash_after_reserve,
)
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState
from execution_conformance.store_epoch import intent_tag_body


def test_p4_regression_double_reconcile_race(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CountingAdmitWrapper(AgentBusAdapter())
    key = "reconcile-race-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        adapter,
        intent_id="intent-race-reconcile",
        identity_key=key,
        admit_kwargs={"slug": "race"},
    )
    results: list[int] = []

    def _run() -> None:
        probe = CountingAdmitWrapper(AgentBusAdapter())
        reconcile(ledger.reopen(), probe, "intent-race-reconcile")
        results.append(probe.admit_count)

    t1 = threading.Thread(target=_run)
    t2 = threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sum(results) <= 1
    row = ledger.get("intent-race-reconcile")
    assert row is not None
    assert row.state == ExecutionState.accepted
    assert row.actuator_ref is not None
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

    with connect() as conn:
        assert thread_id_for_idempotency_key(conn, key) is not None


def test_p4_regression_double_reconcile_retry_claim(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CountingAdmitWrapper(AgentBusAdapter())
    key = "race-retry-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        adapter,
        intent_id="intent-race",
        identity_key=key,
        admit_kwargs={"slug": "race"},
    )
    wins: list[bool] = []

    def _claim() -> None:
        wins.append(ledger.claim_retry("intent-race"))

    t1 = threading.Thread(target=_claim)
    t2 = threading.Thread(target=_claim)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert wins.count(True) == 1


def test_p4_regression_completed_local_owner_miss_no_admit(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CountingAdmitWrapper(AgentBusAdapter())
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-done",
        identity_key="done-key-abcdef12",
        admit_kwargs={"slug": "done"},
    )
    ledger.set_state("intent-done", ExecutionState.completed)
    probe = CountingAdmitWrapper(AgentBusAdapter())
    result = reconcile(ledger, probe, "intent-done")
    assert probe.admit_count == 0
    assert result.state == ExecutionState.completed


def test_p4_regression_reconcile_idempotent_unknown(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CursorBridgeBusModelAdapter()
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-idem",
        identity_key="bridge-idem-key-01",
        admit_kwargs={"slug": "idem"},
    )
    r1 = reconcile(ledger, adapter, "intent-idem")
    r2 = reconcile(ledger, adapter, "intent-idem")
    assert r1.blocked is False
    assert r2.blocked is False
    assert r1.state == ExecutionState.accepted
    assert r2.state == ExecutionState.accepted


def test_p4_regression_reserved_to_completed_adopt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from execution_conformance.adapters import StargatePipelineAdapter

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    tracker = StargatePipelineAdapter().tracker
    exec_id = "stargate-reserved-complete"
    adapter = StargatePipelineAdapter(tracker)
    dispatch_crash_after_admit_before_receipt(
        ledger,
        adapter,
        intent_id="intent-res-complete",
        identity_key=exec_id,
        admit_kwargs={"pipeline": "p"},
    )
    tracker.complete_execution(
        exec_id,
        content="ok",
        model="m",
        usage=None,
        duration_s=0.1,
    )
    result = reconcile(ledger, StargatePipelineAdapter(tracker), "intent-res-complete")
    assert result.adopted is True
    row = ledger.get("intent-res-complete")
    assert row is not None
    assert row.state == ExecutionState.completed


def test_p4_regression_cdp_abandoned_cancelled(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    from systems.frontier_consult.cdp_generate_inflight_ledger import mark_abandoned

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CdpInflightAdapter()
    exec_id = "cdp-abandon-exec"
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-abandon",
        identity_key=exec_id,
        admit_kwargs={"thread_id": "22398"},
    )
    mark_abandoned(exec_id)
    lookup = adapter.lookup(exec_id)
    assert lookup.unknown_reason == "abandoned_worker_may_run"
    result = reconcile(ledger, adapter, "intent-abandon")
    assert result.blocked is True


def test_p4_regression_same_identity_two_intent_ids(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CountingAdmitWrapper(AgentBusAdapter())
    key = "shared-key-abcdef12"
    first = dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-a",
        identity_key=key,
        admit_kwargs={"slug": "shared"},
    )
    second = dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-b",
        identity_key=key,
        admit_kwargs={"slug": "shared"},
    )
    assert second.adopted_existing is True
    assert adapter.admit_count == 1
    assert first.actuator_ref == second.actuator_ref


def test_p4_regression_bridge_reply_must_match_intent_tag(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    from agent_bus_store.db import insert_turn

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CursorBridgeBusModelAdapter()
    key_a = "bridge-a-key-abcdef12"
    key_b = "bridge-b-key-abcdef12"
    dispatch_intent(
        ledger,
        adapter,
        intent_id="intent-a",
        identity_key=key_a,
        admit_kwargs={"slug": "lane-a"},
    )
    row = ledger.get("intent-a")
    assert row is not None
    thread_id = row.actuator_ref
    insert_turn(
        thread=thread_id,
        from_agent="cursor-sdk",
        to_agent="cursor-bridge",
        subject="REPLY",
        body=intent_tag_body(key_b) + "wrong",
    )
    lookup = adapter.lookup(key_a)
    assert lookup.unknown_reason == "msg_without_correlated_reply"
