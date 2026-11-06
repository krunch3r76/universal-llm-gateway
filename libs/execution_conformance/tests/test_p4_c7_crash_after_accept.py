"""C7 — crash after remote accept before local receipt; adopt without re-admit."""

from __future__ import annotations

import pytest
from execution_conformance.adapters import (
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
    StargatePipelineAdapter,
)
from execution_conformance.crash_hooks import dispatch_crash_after_admit_before_receipt
from execution_conformance.ledger import IntentLedger
from execution_conformance.reconciler import reconcile


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db.connection import init_db

    init_db()


def _fresh_counting(inner: object) -> CountingAdmitWrapper:
    if isinstance(inner, StargatePipelineAdapter):
        return CountingAdmitWrapper(StargatePipelineAdapter(inner.tracker))
    if isinstance(inner, AgentBusAdapter):
        return CountingAdmitWrapper(AgentBusAdapter())
    if isinstance(inner, CdpInflightAdapter):
        return CountingAdmitWrapper(CdpInflightAdapter())
    return CountingAdmitWrapper(CursorBridgeBusModelAdapter())


@pytest.mark.parametrize(
    ("make_adapter", "identity", "admit_kwargs"),
    [
        (
            lambda: CountingAdmitWrapper(AgentBusAdapter()),
            "c7-agent-bus-key-abcdef12",
            {"slug": "c7-bus"},
        ),
        (
            lambda: CountingAdmitWrapper(StargatePipelineAdapter()),
            "c7-stargate-exec",
            {"pipeline": "c7"},
        ),
        (
            lambda: CountingAdmitWrapper(CdpInflightAdapter()),
            "c7-cdp-exec",
            {"thread_id": "22398"},
        ),
        (
            lambda: CountingAdmitWrapper(CursorBridgeBusModelAdapter()),
            "c7-bridge-intent",
            {"slug": "c7-bridge"},
        ),
    ],
)
def test_p4_c7_crash_after_accept_before_receipt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    bus_env,
    make_adapter,
    identity,
    admit_kwargs,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "gateway"))
    ledger = IntentLedger(tmp_path / "intents.db")
    wrapped = make_adapter()
    intent_id = f"intent-c7-{wrapped.name}"
    dispatch_crash_after_admit_before_receipt(
        ledger,
        wrapped,
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs=admit_kwargs,
    )
    assert wrapped.admit_count == 1
    probe = _fresh_counting(wrapped.inner)
    result = reconcile(ledger.reopen(), probe, intent_id)
    assert probe.admit_count == 0
    assert result.adopted is True
    assert wrapped.admit_count == 1
