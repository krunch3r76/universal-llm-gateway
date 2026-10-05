"""GIW memo outbox: owed only with wake_lane, claim race, suppress, backoff."""

from __future__ import annotations

import asyncio
import threading

import pytest
from closeout_memo.client import PostResult

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit import (
    classify_memo,
    try_emit,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline


def _admit(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    wake_lane: str | None,
    contract: str = "implement",
    resume_of: str | None = None,
    nest_under: str | None = None,
) -> None:
    req = CursorDispatchRequest(
        thread_id=str(15000 + (abs(hash(dispatch_id)) % 1000)),
        model="composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        packet_path=f"tmp/{dispatch_id}.md",
        handoff_contract=contract,
        wake_lane=wake_lane,
        resume_of=resume_of,
        nest_under=nest_under,
        parent_dispatch_thread_id="12286",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model=req.model,
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id=req.model,
        ),
        contract=contract,
    )


def test_mark_terminal_owes_only_when_wake_lane_set() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="memo-owed", wake_lane="12286")
    _admit(ledger, dispatch_id="memo-silent", wake_lane=None)
    ledger.mark_terminal(dispatch_id="memo-owed", terminal_status="completed")
    ledger.mark_terminal(dispatch_id="memo-silent", terminal_status="completed")
    owed = ledger.memo_row("memo-owed")
    silent = ledger.memo_row("memo-silent")
    assert owed is not None and owed["memo_state"] == "owed"
    assert owed["wake_lane"] == "12286"
    assert silent is not None and silent["memo_state"] is None


def test_resume_of_inherits_wake_lane_and_nest_under_does_not() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="memo-parent", wake_lane="12286")
    _admit(
        ledger,
        dispatch_id="memo-resume",
        wake_lane=None,
        resume_of="memo-parent",
    )
    _admit(
        ledger,
        dispatch_id="memo-nest",
        wake_lane=None,
        nest_under="memo-parent",
    )
    resumed = ledger.memo_row("memo-resume")
    nested = ledger.memo_row("memo-nest")
    assert resumed is not None and resumed["wake_lane"] == "12286"
    assert nested is not None and nested["wake_lane"] is None


def test_claim_is_single_winner() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="memo-race", wake_lane="12286")
    ledger.mark_terminal(dispatch_id="memo-race", terminal_status="completed")
    results: list[bool] = []

    def _claim() -> None:
        results.append(ledger.claim_memo("memo-race"))

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_hop_successor_suppresses_before_post(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="memo-hop", wake_lane="12286", contract="conductor")
    ledger.merge_record_json(
        dispatch_id="memo-hop",
        patch={"hop_successor": "succ-1", "memo_closeout": {"closeout_status": "completed"}},
    )
    ledger.mark_terminal(dispatch_id="memo-hop", terminal_status="completed")
    posted: list[object] = []

    async def _post(request: object) -> PostResult:
        posted.append(request)
        return PostResult(accepted=True, status_code=202)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.post_closeout_memo",
        _post,
    )
    asyncio.run(try_emit("memo-hop"))
    row = ledger.memo_row("memo-hop")
    assert posted == []
    assert row is not None and row["memo_state"] == "suppressed"
    assert row["terminal_status"] == "completed"


def test_post_failure_returns_owed_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="memo-backoff", wake_lane="12286")
    ledger.mark_terminal(dispatch_id="memo-backoff", terminal_status="completed")

    async def _post(request: object) -> PostResult:
        return PostResult(accepted=False, status_code=503, error="down")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.post_closeout_memo",
        _post,
    )
    asyncio.run(try_emit("memo-backoff"))
    row = ledger.memo_row("memo-backoff")
    assert row is not None
    assert row["terminal_status"] == "completed"
    assert row["memo_state"] == "owed"
    assert int(row["memo_attempts"]) == 1
    assert float(row["memo_next_at"]) > 0


def test_raising_post_does_not_clear_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, dispatch_id="memo-raise", wake_lane="12286")
    ledger.mark_terminal(dispatch_id="memo-raise", terminal_status="failed")

    async def _post(request: object) -> PostResult:
        raise RuntimeError("stargate down")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit.post_closeout_memo",
        _post,
    )
    asyncio.run(try_emit("memo-raise"))
    row = ledger.memo_row("memo-raise")
    assert row is not None
    assert row["terminal_status"] == "failed"
    assert row["memo_state"] == "owed"


def test_classify_restart_park_and_conductor_stop() -> None:
    parked = {
        "contract": "implement",
        "wake_lane": "12286",
        "terminal_status": "cancelled",
    }
    kind, status, _nxt, suppress = classify_memo(
        parked, {"memo_closeout": {"wake": "giw_restart:abc", "emit_tag": "CURSOR_SDK_PARKED"}}
    )
    assert suppress == "giw_restart"
    assert kind == "sdk_parked"
    stopped = {"contract": "conductor", "wake_lane": "12286", "terminal_status": "completed"}
    kind, status, nxt, suppress = classify_memo(
        stopped, {"memo_closeout": {"closeout_status": "completed"}}
    )
    assert suppress is None
    assert kind == "conductor_stop"
    assert status == "completed"
    assert nxt == "none"
