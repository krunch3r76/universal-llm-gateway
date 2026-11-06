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
from execution_conformance.ledger import IntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState
from execution_conformance.test_hooks import dispatch_crash_after_reserve


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db.connection import init_db

    init_db()


def _run_case(tmp_path, monkeypatch, wrapped, probe, identity, intent_id, admit_kwargs):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = IntentLedger(tmp_path / "intents.db")
    dispatch_crash_after_reserve(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs=admit_kwargs,
    )
    reconcile(ledger.reopen(), probe, intent_id)
    return wrapped, probe


def test_p4_c6_crash_after_reservation_agent_bus(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env
) -> None:
    wrapped = CountingAdmitWrapper(AgentBusAdapter())
    probe = CountingAdmitWrapper(AgentBusAdapter())
    _run_case(
        tmp_path,
        monkeypatch,
        wrapped,
        probe,
        "c6-agent-bus-key-abcdef12",
        "intent-c6-agent_bus",
        {"slug": "c6-bus"},
    )
    assert wrapped.admit_count == 0
    assert probe.admit_count <= 1


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
    row = IntentLedger(tmp_path / "intents.db").get("intent-c6-bridge")
    assert row is not None
    assert row.state == ExecutionState.blocked_unknown
