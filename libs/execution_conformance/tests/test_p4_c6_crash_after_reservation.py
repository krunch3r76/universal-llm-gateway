"""C6 — crash after local reservation; remote admits ≤ 1."""

from __future__ import annotations

import pytest
from execution_conformance.adapters import (
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
    StargatePipelineAdapter,
)
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState


def _run_case(tmp_path, monkeypatch, wrapped, probe, identity, intent_id, admit_kwargs):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    dispatch_crash_after_reserve(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs=admit_kwargs,
    )
    result = reconcile(ledger.reopen(), probe, intent_id)
    return wrapped, probe, result, ledger


def test_p4_c6_crash_after_reservation_agent_bus(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    wrapped = CountingAdmitWrapper(AgentBusAdapter())
    probe = CountingAdmitWrapper(AgentBusAdapter())
    _, probe, result, ledger = _run_case(
        tmp_path,
        monkeypatch,
        wrapped,
        probe,
        "c6-agent-bus-key-abcdef12",
        "intent-c6-agent_bus",
        {"slug": "c6-bus"},
    )
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

    assert wrapped.admit_count == 0
    assert probe.admit_count == 1
    assert result.redispatched is True
    row = ledger.get("intent-c6-agent_bus")
    assert row.state == ExecutionState.accepted
    assert row.actuator_ref is not None
    with connect() as conn:
        assert (
            thread_id_for_idempotency_key(conn, "c6-agent-bus-key-abcdef12") is not None
        )


def test_p4_c6_crash_after_reservation_stargate(tmp_path, monkeypatch) -> None:
    wrapped = CountingAdmitWrapper(StargatePipelineAdapter())
    probe = CountingAdmitWrapper(StargatePipelineAdapter())
    _run_case(
        tmp_path,
        monkeypatch,
        wrapped,
        probe,
        "c6-stargate-exec",
        "intent-c6-stargate",
        {"pipeline": "c6"},
    )
    assert wrapped.admit_count == 0
    assert probe.admit_count == 0


def test_p4_c6_crash_after_reservation_cdp(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    wrapped = CountingAdmitWrapper(CdpInflightAdapter())
    probe = CountingAdmitWrapper(CdpInflightAdapter())
    _run_case(
        tmp_path,
        monkeypatch,
        wrapped,
        probe,
        "c6-cdp-exec",
        "intent-c6-cdp",
        {"thread_id": "22398"},
    )
    assert wrapped.admit_count == 0
    assert probe.admit_count == 0


def test_p4_c6_crash_after_reservation_bridge(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    wrapped = CountingAdmitWrapper(CursorBridgeBusModelAdapter())
    probe = CountingAdmitWrapper(CursorBridgeBusModelAdapter())
    _run_case(
        tmp_path,
        monkeypatch,
        wrapped,
        probe,
        "c6-bridge-intent",
        "intent-c6-bridge",
        {"slug": "c6-bridge"},
    )
    assert wrapped.admit_count == 0
    assert probe.admit_count == 0
    row = ReferenceSqliteIntentLedger(tmp_path / "intents.db").get("intent-c6-bridge")
    assert row is not None
    assert row.state == ExecutionState.blocked_unknown
