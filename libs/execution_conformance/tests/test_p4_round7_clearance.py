"""Round-7 clearance — accepted/running owner miss, atomic lease, ledger CAS."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from typing import Any

import pytest
from execution_conformance.adapters import (
    AdmitOutcome,
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
    intent_tag_body,
)
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import _block_unknown, dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


class EventGatedAdmitWrapper(CountingAdmitWrapper):
    entered: threading.Event
    release: threading.Event

    def __init__(
        self,
        inner,
        *,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(inner)
        self.entered = entered
        self.release = release

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        self.entered.set()
        assert self.release.wait(timeout=5.0)
        return super().admit(identity_key, **kwargs)


def test_p4_round7_agent_bus_accepted_running_owner_miss_blocks(
    bus_env, tmp_path
) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "round7-owner-miss-abcdef12"
    probe = CountingAdmitWrapper(AgentBusAdapter())
    dispatch_intent(
        ledger,
        probe,
        intent_id="intent-r7-ab",
        identity_key=key,
        admit_kwargs={"slug": "r7"},
    )
    assert probe.admit_count == 1
    row = ledger.get("intent-r7-ab")
    assert row.state == ExecutionState.accepted
    thread_id = row.actuator_ref
    assert thread_id is not None
    with closing(sqlite3.connect(bus_env)) as conn:
        conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        conn.commit()
    outcome = reconcile(ledger, CountingAdmitWrapper(AgentBusAdapter()), "intent-r7-ab")
    assert outcome.state == ExecutionState.blocked_unknown
    assert outcome.blocked is True
    assert outcome.adopted_existing is False
    assert probe.admit_count == 1
    row = ledger.get("intent-r7-ab")
    assert row.payload.get("blocked_reason") == "owner_miss"

    ledger2 = ReferenceSqliteIntentLedger(tmp_path / "intents2.db")
    key2 = "round7-running-miss-abcdef12"
    probe2 = CountingAdmitWrapper(AgentBusAdapter())
    dispatch_intent(
        ledger2,
        probe2,
        intent_id="intent-r7-run",
        identity_key=key2,
        admit_kwargs={"slug": "r7"},
    )
    ledger2.set_state("intent-r7-run", ExecutionState.running)
    row2 = ledger2.get("intent-r7-run")
    tid2 = row2.actuator_ref
    with closing(sqlite3.connect(bus_env)) as conn:
        conn.execute("DELETE FROM threads WHERE id = ?", (tid2,))
        conn.commit()
    outcome2 = reconcile(
        ledger2, CountingAdmitWrapper(AgentBusAdapter()), "intent-r7-run"
    )
    assert outcome2.state == ExecutionState.blocked_unknown
    assert outcome2.blocked is True
    assert outcome2.adopted_existing is False
    assert probe2.admit_count == 1
    assert ledger2.get("intent-r7-run").payload.get("blocked_reason") == "owner_miss"


def test_p4_round7_inflight_marker_reserve_and_clear(tmp_path) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "ledger.db")
    ledger.reserve(
        intent_id="i1",
        actuator="agent_bus",
        identity_key="round7-inflight-key-abcdef12",
        store_epoch="epoch",
        payload={"admit_kwargs": {}},
        inflight_claimed_at=100.0,
    )
    row = ledger.get("i1")
    assert row.payload is not None
    assert "inflight_claimed_at" in row.payload
    ledger.clear_inflight_marker("i1")
    row = ledger.get("i1")
    assert "inflight_claimed_at" not in (row.payload or {})
    ledger.reserve(
        intent_id="i2",
        actuator="agent_bus",
        identity_key="round7-block-key-abcdef12",
        store_epoch="epoch",
        inflight_claimed_at=200.0,
    )
    _block_unknown(ledger, "i2", reason="owner_miss")
    row = ledger.get("i2")
    assert "inflight_claimed_at" not in (row.payload or {})


@pytest.mark.parametrize(
    "adapter_factory,identity,kwargs",
    [
        (
            lambda: CdpInflightAdapter(),
            "cdp-r7-lease-key-abcdef12",
            {"thread_id": "22398"},
        ),
        (
            lambda: CursorBridgeBusModelAdapter(),
            "bridge-r7-lease-key-abcdef12",
            {"slug": "r7"},
        ),
    ],
)
def test_p4_round7_dispatch_inflight_lease_transient(
    cdp_env, bus_env, tmp_path, adapter_factory, identity, kwargs
) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = adapter_factory()
    intent_id = f"intent-{adapter.name}-lease"
    entered = threading.Event()
    release = threading.Event()
    wrapped = EventGatedAdmitWrapper(
        adapter, entered=entered, release=release
    )
    errors: list[BaseException] = []
    dispatch_result: list[Any] = []

    def _bg() -> None:
        try:
            dispatch_result.append(
                dispatch_intent(
                    ledger.reopen(),
                    wrapped,
                    intent_id=intent_id,
                    identity_key=identity,
                    admit_kwargs=kwargs,
                    clock=lambda: 1000.0,
                    claim_ttl_seconds=30.0,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_bg)
    t.start()
    assert entered.wait(timeout=5.0)
    r = reconcile(
        ledger.reopen(),
        CountingAdmitWrapper(adapter),
        intent_id,
        clock=lambda: 1005.0,
        claim_ttl_seconds=30.0,
    )
    assert r.transient is True
    assert r.state == ExecutionState.reserved
    assert r.blocked is False
    assert r.redispatched is False
    release.set()
    t.join()
    assert not errors
    assert dispatch_result[0].state == ExecutionState.accepted
    assert dispatch_result[0].actuator_ref is not None
    final = reconcile(
        ledger, CountingAdmitWrapper(adapter), intent_id
    )
    assert final.state == ExecutionState.accepted
    assert final.blocked is False


@pytest.mark.parametrize(
    "adapter_factory,identity,kwargs",
    [
        (
            lambda: CdpInflightAdapter(),
            "cdp-r7-stale-key-abcdef12",
            {"thread_id": "22398"},
        ),
        (
            lambda: CursorBridgeBusModelAdapter(),
            "bridge-r7-stale-key-abcdef12",
            {"slug": "r7"},
        ),
    ],
)
def test_p4_round7_inflight_ttl_stale_blocks(
    cdp_env, bus_env, tmp_path, adapter_factory, identity, kwargs
) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = adapter_factory()
    intent_id = f"intent-{adapter.name}-stale"
    ledger.reserve(
        intent_id=intent_id,
        actuator=adapter.name,  # type: ignore[arg-type]
        identity_key=identity,
        store_epoch=adapter.store_epoch(),
        payload={"admit_kwargs": kwargs},
        inflight_claimed_at=1000.0,
    )
    mid = reconcile(
        ledger,
        adapter,
        intent_id,
        clock=lambda: 1005.0,
        claim_ttl_seconds=30.0,
    )
    assert mid.transient is True
    late = reconcile(
        ledger,
        adapter,
        intent_id,
        clock=lambda: 1035.0,
        claim_ttl_seconds=30.0,
    )
    assert late.blocked is True
    assert late.state == ExecutionState.blocked_unknown
    row = ledger.get(intent_id)
    assert row.payload.get("blocked_reason") == "retry_claim_stale"
    assert "inflight_claimed_at" not in (row.payload or {})


def test_p4_round7_bridge_duplicate_msg_sticky(bus_env, tmp_path) -> None:
    from agent_bus_store.db import create_thread, insert_turn

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = CursorBridgeBusModelAdapter()
    key = "bridge-dup-msg-key-abcdef12"
    tag = intent_tag_body(key)
    detail = create_thread(thread_id=None, slug="dup")
    thread_id = str(detail["id"])
    body = tag + "one"
    insert_turn(
        thread=thread_id,
        from_agent="a",
        to_agent="b",
        subject="MSG",
        body=body,
    )
    insert_turn(
        thread=thread_id,
        from_agent="a",
        to_agent="b",
        subject="MSG",
        body=body,
    )
    ledger.reserve(
        intent_id="intent-dup",
        actuator="cursor_bridge",
        identity_key=key,
        store_epoch=adapter.store_epoch(),
    )
    reconcile(ledger, adapter, "intent-dup")
    row = ledger.get("intent-dup")
    assert row.state == ExecutionState.blocked_unknown
    assert row.payload.get("blocked_reason") == "duplicate_msg"
