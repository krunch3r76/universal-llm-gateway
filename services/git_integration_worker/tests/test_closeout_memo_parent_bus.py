"""Parent-bus null-wake closeout delivery and memo outbox (agent-bus:21581)."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from multiprocessing import Process, Queue

import pytest
from closeout_memo.client import PostResult

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit import (
    parent_bus_target,
    sweep_due,
    try_emit,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_PARENT = "100"
_WORKER = "200"
_CALLER = "lead-agent"


def _admit_parent_bus(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    parent: str = _PARENT,
    worker: str = _WORKER,
    caller: str = _CALLER,
    wake_lane: str | None = None,
    contract: str = "implement",
    nest_under: str | None = None,
) -> None:
    req = CursorDispatchRequest(
        thread_id=worker,
        model="composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        packet_path=f"tmp/{dispatch_id}.md",
        handoff_contract=contract,
        wake_lane=wake_lane,
        parent_dispatch_thread_id=parent,
        nest_under=nest_under,
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=caller,
        resolved_model=req.model,
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id=req.model,
        ),
        contract=contract,
    )


def test_parent_bus_target_eligibility() -> None:
    row = {"wake_lane": None, "thread_id": _WORKER, "caller_agent": _CALLER}
    rec = {"parent_dispatch_thread_id": _PARENT}
    assert parent_bus_target(row, rec) == (_PARENT, _CALLER)
    assert parent_bus_target({**row, "wake_lane": "12286"}, rec) is None
    assert parent_bus_target(row, {"parent_dispatch_thread_id": _WORKER}) is None
    assert parent_bus_target(row, {"parent_dispatch_thread_id": "abc"}) is None
    assert parent_bus_target({**row, "caller_agent": ""}, rec) is None


def test_mark_terminal_owes_null_wake_parent_bus() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_parent_bus(ledger, dispatch_id="pb-owed", wake_lane=None)
    ledger.mark_terminal(dispatch_id="pb-owed", terminal_status="completed")
    row = ledger.memo_row("pb-owed")
    assert row is not None
    assert row["memo_state"] == "owed"


def test_mark_terminal_silent_when_parent_invalid() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_parent_bus(
        ledger,
        dispatch_id="pb-silent-self",
        parent=_WORKER,
        worker=_WORKER,
    )
    ledger.mark_terminal(dispatch_id="pb-silent-self", terminal_status="completed")
    row = ledger.memo_row("pb-silent-self")
    assert row is not None
    assert row["memo_state"] is None


@pytest.mark.asyncio
async def test_parent_bus_emit_posts_bus_send(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pb-send-1"
    _admit_parent_bus(ledger, dispatch_id=dispatch_id)
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"memo_closeout": {"turn": 3, "sidecar_ref": "cortex://notes/example.md"}},
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    captured: list[dict[str, str]] = []

    async def _bus_send(**kwargs: object) -> tuple[dict[str, object], int]:
        captured.append({k: str(v) for k, v in kwargs.items() if k != "after_turn"})
        return {"ok": True}, 201

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.bus_send",
        _bus_send,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.post_closeout_memo",
        lambda *_a, **_k: PostResult(accepted=False, status_code=500),
    )
    await try_emit(dispatch_id)
    row = ledger.memo_row(dispatch_id)
    assert row is not None and row["memo_state"] == "emitted"
    assert len(captured) == 1
    msg = captured[0]
    assert msg["thread"] == _PARENT
    assert msg["to_agent"] == _CALLER
    assert msg["from_agent"] == "cursor-sdk"
    assert msg["subject"] == f"cursor-sdk CLOSEOUT {dispatch_id} | giw pointer"
    assert "execution_id:" in msg["body"]
    assert "sidecar_ref:" in msg["body"]
    assert "git_integration_worker closeout-memo" in msg["body"]


@pytest.mark.asyncio
async def test_explicit_wake_lane_uses_cse_not_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pb-cse-route"
    _admit_parent_bus(ledger, dispatch_id=dispatch_id, wake_lane="12286")
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    bus_calls: list[object] = []
    cse_calls: list[object] = []

    async def _bus_send(**_kwargs: object) -> tuple[dict[str, object], int]:
        bus_calls.append(1)
        return {}, 201

    async def _cse(request: object) -> PostResult:
        cse_calls.append(request)
        return PostResult(accepted=True, status_code=202)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.bus_send",
        _bus_send,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.post_closeout_memo",
        _cse,
    )
    await try_emit(dispatch_id)
    assert bus_calls == []
    assert len(cse_calls) == 1


@pytest.mark.asyncio
async def test_concurrent_try_emit_single_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pb-race"
    _admit_parent_bus(ledger, dispatch_id=dispatch_id)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    gate = asyncio.Event()
    send_count = 0

    async def _bus_send(**_kwargs: object) -> tuple[dict[str, object], int]:
        nonlocal send_count
        send_count += 1
        await gate.wait()
        return {}, 201

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.bus_send",
        _bus_send,
    )

    async def _run() -> None:
        await try_emit(dispatch_id)

    tasks = [asyncio.create_task(_run()), asyncio.create_task(_run())]
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(*tasks)
    assert send_count == 1
    row = ledger.memo_row(dispatch_id)
    assert row is not None and row["memo_state"] == "emitted"


@pytest.mark.asyncio
async def test_503_then_200_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pb-503"
    _admit_parent_bus(ledger, dispatch_id=dispatch_id)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    calls = {"n": 0}

    async def _bus_send(**_kwargs: object) -> tuple[dict[str, object], int]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"error": {"code": "down"}}, 503
        return {}, 200

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.bus_send",
        _bus_send,
    )
    await try_emit(dispatch_id)
    row = ledger.memo_row(dispatch_id)
    assert row is not None
    assert row["terminal_status"] == "completed"
    assert row["memo_state"] == "owed"
    assert int(row["memo_attempts"]) == 1
    ledger.finish_memo(dispatch_id, state="owed", next_at=time.time() - 1)
    await try_emit(dispatch_id)
    row = ledger.memo_row(dispatch_id)
    assert row is not None and row["memo_state"] == "emitted"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_nested_completed_suppresses_parent_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pb-nested"
    _admit_parent_bus(ledger, dispatch_id=dispatch_id, nest_under="parent-x")
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    sends: list[object] = []

    async def _bus_send(**_kwargs: object) -> tuple[dict[str, object], int]:
        sends.append(1)
        return {}, 201

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.bus_send",
        _bus_send,
    )
    await try_emit(dispatch_id)
    assert sends == []
    row = ledger.memo_row(dispatch_id)
    assert row is not None and row["memo_state"] == "suppressed"


@pytest.mark.asyncio
async def test_stale_parent_claim_reclaimed_by_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pb-stale-claim"
    _admit_parent_bus(ledger, dispatch_id=dispatch_id)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    assert ledger.claim_memo(dispatch_id)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET memo_next_at=? WHERE dispatch_id=?",
            (time.time() - 10, dispatch_id),
        )

    async def _bus_send(**_kwargs: object) -> tuple[dict[str, object], int]:
        return {}, 200

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.bus_send",
        _bus_send,
    )
    await sweep_due()
    row = ledger.memo_row(dispatch_id)
    assert row is not None and row["memo_state"] == "emitted"


def _concurrent_insert_worker(db_path: str, queue: Queue) -> None:
    import os

    os.environ["AGENT_BUS_DB_PATH"] = db_path
    os.environ["AGENT_BUS_EVENTS_ENABLED"] = "false"
    from agent_bus_store.db import init_db
    from agent_bus_store.db.turns import insert_turn

    init_db()
    subject = "cursor-sdk CLOSEOUT dup-concurrent-test | giw pointer"
    body = "status: complete\n"
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _once() -> None:
        barrier.wait()
        _tid, _ts, _num, claimed = insert_turn(
            thread="300",
            from_agent="cursor-sdk",
            to_agent="lead-agent",
            subject=subject,
            body=body,
        )
        results.append(claimed)

    threads = [threading.Thread(target=_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    queue.put(results)


def test_sdk_closeout_atomic_dedupe_across_connections(tmp_path) -> None:
    from agent_bus_store.db import create_thread, init_db

    db_path = str(tmp_path / "dedupe.db")
    import os

    os.environ["AGENT_BUS_DB_PATH"] = db_path
    os.environ["AGENT_BUS_EVENTS_ENABLED"] = "false"
    init_db()
    create_thread(thread_id="300", slug="parent")
    for turn in range(260):
        from agent_bus_store.db.turns import insert_turn

        insert_turn(
            thread="300",
            from_agent="noise",
            to_agent="lead-agent",
            subject=f"filler {turn}",
            body="x",
        )
    queue: Queue = Queue()
    first = Process(target=_concurrent_insert_worker, args=(db_path, queue))
    second = Process(target=_concurrent_insert_worker, args=(db_path, queue))
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)
    assert first.exitcode == 0 and second.exitcode == 0
    batch_a = queue.get(timeout=5)
    batch_b = queue.get(timeout=5)
    assert batch_a.count(True) + batch_b.count(True) >= 1
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE notice_closeout_key=?",
            ("sdk:dup-concurrent-test",),
        ).fetchone()[0]
    assert count == 1


def test_finish_memo_stale_claimant_cannot_clobber_emitted() -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pb-finish-cas"
    _admit_parent_bus(ledger, dispatch_id=dispatch_id)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    assert ledger.claim_memo(dispatch_id)
    ledger.finish_memo(dispatch_id, state="emitted")
    ledger.finish_memo(dispatch_id, state="owed", next_at=time.time() + 30)
    row = ledger.memo_row(dispatch_id)
    assert row is not None and row["memo_state"] == "emitted"
