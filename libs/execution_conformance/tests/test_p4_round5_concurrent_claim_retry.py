"""Round-5 — concurrent reconcile claim_retry race (Major 1)."""

from __future__ import annotations

import threading
import time
from typing import Any

from execution_conformance.adapters import (
    AdmitOutcome,
    AgentBusAdapter,
    CountingAdmitWrapper,
)
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState


class SlowAgentBusAdapter(AgentBusAdapter):
    """Slow admit so dual reconcile threads contend on claim_retry."""

    def admit(self, identity_key: str, **kwargs: Any) -> AdmitOutcome:
        time.sleep(0.08)
        return super().admit(identity_key, **kwargs)


def _thread_count(conn, key: str) -> int:
    from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

    tid = thread_id_for_idempotency_key(conn, key)
    return 0 if tid is None else 1


def test_p4_round5_concurrent_reconcile_claim_retry(bus_env, tmp_path) -> None:
    from agent_bus_store.db.connection import connect

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "concurrent-claim-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-concurrent",
        identity_key=key,
        admit_kwargs={"slug": "conc"},
    )
    errors: list[BaseException] = []
    results = []

    def _worker() -> None:
        try:
            probe = CountingAdmitWrapper(SlowAgentBusAdapter())
            results.append(reconcile(ledger.reopen(), probe, "intent-concurrent"))
        except BaseException as exc:  # noqa: BLE001 — test collects thread errors
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    row = ledger.get("intent-concurrent")
    assert row is not None
    assert row.state == ExecutionState.accepted
    assert row.actuator_ref is not None
    assert any(r.redispatched for r in results) or row.actuator_ref
    with connect() as conn:
        assert _thread_count(conn, key) == 1
