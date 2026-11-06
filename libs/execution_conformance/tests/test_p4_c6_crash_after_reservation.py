"""C6 — crash after local reservation, restart both sides; remote admits ≤ 1."""

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
from execution_conformance.reconciler import dispatch_intent, reconcile


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db.connection import init_db

    init_db()


def _case_stargate(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = IntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(StargatePipelineAdapter())
    identity = "c6-stargate-exec"
    intent_id = "intent-c6-stargate_pipeline"
    dispatch_intent(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs={"pipeline": "c6"},
        remote_admit=False,
    )
    probe = CountingAdmitWrapper(StargatePipelineAdapter())
    reconcile(ledger.reopen(), probe, intent_id)
    return wrapped, probe


def _case_agent_bus(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = IntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(AgentBusAdapter())
    identity = "c6-agent-bus-key-abcdef12"
    intent_id = "intent-c6-agent_bus"
    dispatch_intent(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs={"slug": "c6-bus"},
        remote_admit=False,
    )
    probe = CountingAdmitWrapper(AgentBusAdapter())
    reconcile(ledger.reopen(), probe, intent_id)
    return wrapped, probe


def _case_cdp(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = IntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(CdpInflightAdapter())
    identity = "c6-cdp-exec"
    intent_id = "intent-c6-cdp_ask"
    dispatch_intent(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs={"thread_id": "22398"},
        remote_admit=False,
    )
    probe = CountingAdmitWrapper(CdpInflightAdapter())
    reconcile(ledger.reopen(), probe, intent_id)
    return wrapped, probe


def _case_bridge(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = IntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(CursorBridgeBusModelAdapter())
    identity = "c6-bridge-intent"
    intent_id = "intent-c6-cursor_bridge"
    dispatch_intent(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs={"slug": "c6-bridge"},
        remote_admit=False,
    )
    probe = CountingAdmitWrapper(CursorBridgeBusModelAdapter())
    reconcile(ledger.reopen(), probe, intent_id)
    return wrapped, probe


@pytest.mark.parametrize(
    "case_fn", [_case_agent_bus, _case_stargate, _case_cdp, _case_bridge]
)
def test_p4_c6_crash_after_reservation(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bus_env, case_fn
) -> None:
    wrapped, probe = case_fn(tmp_path, monkeypatch)
    assert wrapped.admit_count == 0
    assert probe.admit_count <= 1
