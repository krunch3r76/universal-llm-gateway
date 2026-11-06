"""Round-5/6 — concurrent reconcile claim_retry: one redispatched winner."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import patch

from execution_conformance.adapters import (
    AgentBusAdapter,
    CountingAdmitWrapper,
)
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.states import ExecutionState

_real_create_thread = None
_start_barrier: threading.Barrier | None = None


def _patched_create_thread(*args: Any, **kwargs: Any):
    time.sleep(0.08)
    assert _real_create_thread is not None
    return _real_create_thread(*args, **kwargs)


def _thread_count(conn, key: str) -> int:
    from agent_bus_store.db.thread_idempotency import thread_id_for_idempotency_key

    tid = thread_id_for_idempotency_key(conn, key)
    return 0 if tid is None else 1


def test_p4_round5_concurrent_reconcile_claim_retry(bus_env, tmp_path) -> None:
    global _real_create_thread, _start_barrier
    from agent_bus_store.db import threads as threads_mod
    from agent_bus_store.db.connection import connect

    _real_create_thread = threads_mod.create_thread
    _start_barrier = threading.Barrier(2)

    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "concurrent-claim-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-concurrent",
        identity_key=key,
        admit_kwargs={"slug": "conc"},
    )
    ledger.clear_inflight_marker("intent-concurrent")
    errors: list[BaseException] = []
    results = []

    def _worker() -> None:
        try:
            assert _start_barrier is not None
            _start_barrier.wait(timeout=5.0)
            with patch(
                "agent_bus_store.db.threads.create_thread",
                side_effect=_patched_create_thread,
            ):
                probe = CountingAdmitWrapper(AgentBusAdapter())
                results.append(reconcile(ledger, probe, "intent-concurrent"))
        except BaseException as exc:  # noqa: BLE001
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
    redispatched_flags = [r.redispatched for r in results]
    assert redispatched_flags.count(True) == 1
    assert redispatched_flags.count(False) == 1
    assert all(not r.blocked for r in results)
    with connect() as conn:
        assert _thread_count(conn, key) == 1
