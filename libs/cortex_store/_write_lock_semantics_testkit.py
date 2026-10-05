"""Shared WRITE_LOCK semantics probes for S6 batch 7+ (L1–L3).

Proves typed routes and ``POST /dispatch`` acquire/release the handler's
``WRITE_LOCK`` identically — the gate that unblocks stamping WRITE_LOCK ops.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest


class CountingWriteLock:
    """``threading.Lock`` proxy that counts acquire/release pairs."""

    __slots__ = ("_inner", "acquires", "releases")

    def __init__(self, inner: threading.Lock | None = None) -> None:
        self._inner = inner or threading.Lock()
        self.acquires = 0
        self.releases = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if timeout != -1:
            ok = self._inner.acquire(blocking, timeout)
        else:
            ok = self._inner.acquire(blocking)
        if ok:
            self.acquires += 1
        return ok

    def release(self) -> None:
        self._inner.release()
        self.releases += 1

    def locked(self) -> bool:
        return self._inner.locked()

    def __enter__(self) -> CountingWriteLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


@dataclass
class TxnBoundaryTrace:
    begin_immediate_seen: bool = False
    commit_or_rollback_seen: bool = False
    lock_held_at_begin: bool | None = None
    lock_held_at_end: bool | None = None


def install_counting_write_lock(
    monkeypatch: pytest.MonkeyPatch,
    handler_module: Any,
) -> CountingWriteLock:
    """Replace ``WRITE_LOCK`` on ``cortex_store.db`` and the handler import."""
    from cortex_store import db as db_mod

    assert handler_module.WRITE_LOCK is db_mod.WRITE_LOCK, (
        "handler module must import WRITE_LOCK from cortex_store.db"
    )
    counter = CountingWriteLock()
    monkeypatch.setattr(db_mod, "WRITE_LOCK", counter)
    monkeypatch.setattr(handler_module, "WRITE_LOCK", counter)
    return counter


def trace_txn_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    handler_module: Any,
    counter: CountingWriteLock,
) -> TxnBoundaryTrace:
    """Wrap ``cortex_conn().execute`` on the handler module's bound import."""
    trace = TxnBoundaryTrace()
    original_conn = handler_module.cortex_conn

    class _TracedConn:
        __slots__ = ("_inner",)

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
            if isinstance(sql, str):
                upper = sql.strip().upper()
                if upper.startswith("BEGIN IMMEDIATE"):
                    trace.begin_immediate_seen = True
                    trace.lock_held_at_begin = counter.locked()
                if upper.startswith("COMMIT") or upper.startswith("ROLLBACK"):
                    trace.commit_or_rollback_seen = True
                    trace.lock_held_at_end = counter.locked()
            return self._inner.execute(sql, params)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def __enter__(self) -> _TracedConn:
            return self

        def __exit__(self, *exc: object) -> None:
            if hasattr(self._inner, "__exit__"):
                self._inner.__exit__(*exc)
            elif hasattr(self._inner, "close"):
                self._inner.close()

    def traced_cortex_conn() -> _TracedConn:
        return _TracedConn(original_conn())

    monkeypatch.setattr(handler_module, "cortex_conn", traced_cortex_conn)
    return trace


def assert_l1_acquisition_parity(
    counter: CountingWriteLock,
    trace: TxnBoundaryTrace,
    *,
    expect_txn: bool,
) -> None:
    assert counter.acquires == 1, f"expected one acquire, got {counter.acquires}"
    assert counter.releases == 1, f"expected one release, got {counter.releases}"
    assert not counter.locked(), "WRITE_LOCK still held after handler returned"
    if expect_txn:
        assert trace.begin_immediate_seen, "BEGIN IMMEDIATE never observed"
        assert trace.lock_held_at_begin is True, "lock must be held before BEGIN IMMEDIATE"
        assert trace.commit_or_rollback_seen, "COMMIT/ROLLBACK never observed"
        assert trace.lock_held_at_end is True, "lock must be held until txn ends"


def run_l2_serialization(
    counter: CountingWriteLock,
    call: Callable[[], Any],
    *,
    block_timeout_s: float = 0.5,
    complete_timeout_s: float = 10.0,
) -> None:
    """While WRITE_LOCK is held externally, ``call`` blocks then completes after release."""
    assert counter._inner.acquire(blocking=False), "setup: lock must be free"
    counter._inner.release()

    result: dict[str, Any] = {}
    started = threading.Event()

    def worker() -> None:
        started.set()
        result["value"] = call()

    counter._inner.acquire()  # external hold before worker starts the handler
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert started.wait(timeout=2.0), "worker never started"

    thread.join(timeout=block_timeout_s)
    assert thread.is_alive(), "call completed while lock held — expected block"

    counter._inner.release()
    thread.join(timeout=complete_timeout_s)
    assert not thread.is_alive(), "call did not complete after lock release"
    assert "value" in result


def assert_l3_lock_reacquirable(counter: CountingWriteLock) -> None:
    assert counter.acquire(blocking=False), "WRITE_LOCK not re-acquirable after failure"
    counter.release()


def assert_register_create_rollback_empty(
    conn: Any,
    skill_id: str,
    *,
    case_id: str = "case:batch7-parity",
) -> None:
    """After failed atomic create, no composite member rows or uses_skill edges."""
    from cortex_store.db import query

    skill_entity = f"agent_skill:{skill_id}"
    doc_entity = f"document:skill-{skill_id}"
    assert not query(
        conn,
        "SELECT 1 FROM entities WHERE id IN (?, ?)",
        (skill_entity, doc_entity),
    )
    assert not query(
        conn,
        "SELECT 1 FROM relationships WHERE "
        "(from_entity = ? AND to_entity = ?) OR "
        "(from_entity = ? AND to_entity = ? AND type = 'uses_skill')",
        (skill_entity, doc_entity, case_id, skill_entity),
    )
    assert not query(
        conn,
        "SELECT 1 FROM session_edges WHERE from_node = ? OR to_node = ?",
        (skill_entity, skill_entity),
    )
