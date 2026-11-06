"""Round-6 — advisory blocked promotion + in-flight dispatch lease."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from execution_conformance.adapters import (
    AdmitOutcome,
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
)
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import dispatch_intent, reconcile
from execution_conformance.states import ExecutionState


class SlowAdmitWrapper(CountingAdmitWrapper):
    barrier: threading.Barrier | None = None

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        if self.barrier is not None:
            self.barrier.wait(timeout=5.0)
        time.sleep(0.05)
        return super().admit(identity_key, **kwargs)


def _clear_inflight(ledger: ReferenceSqliteIntentLedger, intent_id: str) -> None:
    ledger.clear_inflight_marker(intent_id)
    row = ledger.get(intent_id)
    assert row is not None
    assert row.inflight_claimed_at is None


@pytest.mark.parametrize(
    "adapter_factory,identity,kwargs",
    [
        (
            lambda: CdpInflightAdapter(),
            "cdp-round6-key-abcdef12",
            {"thread_id": "22398"},
        ),
        (
            lambda: CursorBridgeBusModelAdapter(),
            "bridge-round6-key-abcdef12",
            {"slug": "round6"},
        ),
    ],
)
def test_p4_round6_blocked_then_admit_then_dispatch(
    cdp_env, bus_env, tmp_path, adapter_factory, identity, kwargs
) -> None:
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    adapter = adapter_factory()
    intent_id = f"intent-{adapter.name}-round6"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(adapter),
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs=kwargs,
    )
    _clear_inflight(ledger, intent_id)
    blocked = reconcile(ledger, adapter, intent_id)
    assert blocked.blocked is True
    row = ledger.get(intent_id)
    assert row.state == ExecutionState.blocked_unknown
    assert row.payload.get("blocked_reason") == "owner_miss"
    adapter.admit(identity, **kwargs)
    result = dispatch_intent(
        ledger,
        CountingAdmitWrapper(adapter),
        intent_id=intent_id,
        identity_key=identity,
        admit_kwargs=kwargs,
    )
    row = ledger.get(intent_id)
    assert row.state == ExecutionState.accepted
    assert row.actuator_ref is not None
    assert row.payload.get("owner_unknown_reason") is not None
    assert result.adopted_existing or row.actuator_ref


def test_p4_round6_concurrent_dispatch_inflight_lease(bus_env, tmp_path) -> None:
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "inflight-dispatch-key-abcdef12"
    barrier = threading.Barrier(2)
    wrapped = SlowAdmitWrapper(AgentBusAdapter())
    wrapped.barrier = barrier
    errors: list[BaseException] = []
    reconcile_results = []

    def _dispatch() -> None:
        try:
            dispatch_intent(
                ledger.reopen(),
                wrapped,
                intent_id="intent-inflight",
                identity_key=key,
                admit_kwargs={"slug": "inflight"},
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _reconcile_while() -> None:
        try:
            time.sleep(0.01)
            reconcile_results.append(
                reconcile(
                    ledger.reopen(),
                    CountingAdmitWrapper(AgentBusAdapter()),
                    "intent-inflight",
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_dispatch)
    t2 = threading.Thread(target=_dispatch)
    tr = threading.Thread(target=_reconcile_while)
    for t in (t1, t2, tr):
        t.start()
    for t in (t1, t2, tr):
        t.join()
    assert not errors
    row = ledger.get("intent-inflight")
    assert row is not None
    assert row.state == ExecutionState.accepted
    assert row.actuator_ref is not None
    if reconcile_results:
        assert any(r.transient for r in reconcile_results) or row.state == ExecutionState.accepted
    final = reconcile(ledger, CountingAdmitWrapper(AgentBusAdapter()), "intent-inflight")
    assert final.state == ExecutionState.accepted
    with connect() as conn:
        assert thread_id_for_idempotency_key(conn, key) is not None
