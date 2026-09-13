"""Hermetic tests for GIW steer inject deposit / ack / escalation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.mcp_bridge_steer_inject import (
    PendingSteer,
    append_spool_entry,
    mark_delivered,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import ParkRefusal
from services.git_integration_worker.cursor_sdk_steer_inject import (
    SteerDepositResult,
    deposit_steer_directive,
    escalate_idle_to_park,
    poll_delivery_ack,
    recover_undelivered_steer_from_thread,
)
from services.git_integration_worker.cursor_sdk_steer_inject_http import (
    inject_one_dispatch,
)
from services.git_integration_worker.cursor_sdk_steer_inject_preflight import (
    InjectRefusal,
    preflight_inject,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    register_live_run,
    unregister_live_run,
)


@pytest.fixture
def spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "steer-spool"
    root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return root


def test_deposit_writes_spool_and_emits(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject._deposit_authority_turn",
        lambda **_k: "99",
    )
    result = deposit_steer_directive(
        dispatch_id="disp-dep",
        thread_id="10479",
        directive="check Stargate logs",
        reason="operator steer",
        spool_dir=spool,
    )
    assert result.authority_turn_id == "99"
    assert result.dispatch_id == "disp-dep"
    signals = [ev.signal for ev in events]
    assert "frontier.sdk.steer.inject.requested" in signals
    assert "frontier.sdk.steer.inject.spooled" in signals
    from scripts.mcp_bridge_steer_inject import spool_path

    assert spool_path(spool, "disp-dep").is_file()


def test_poll_delivery_ack_emits_delivered(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    deposit = SteerDepositResult(
        dispatch_id="disp-ack",
        entry_id="e-ack",
        authority_turn_id="12",
        spool_path=str(spool / "disp-ack.json"),
    )
    append_spool_entry(
        "disp-ack",
        authority_turn_id="12",
        directive="nudge",
        ttl_s=300,
        spool_dir=spool,
        entry_id="e-ack",
    )
    pending = PendingSteer(
        entry_id="e-ack",
        dispatch_id="disp-ack",
        authority_turn_id="12",
        directive="nudge",
        deposited_at="2026-09-13T00:00:00+00:00",
        ttl_s=300,
    )
    mark_delivered(pending, spool_dir=spool)
    row = poll_delivery_ack(deposit, spool_dir=spool)
    assert row is not None
    assert any(ev.signal == "frontier.sdk.steer.inject.delivered" for ev in events)


def test_escalate_idle_to_park_emits_and_signals_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.signal_park",
        lambda *_a, **_k: MagicMock(
            dispatch_id="disp-esc",
            thread_id="10479",
            refusal=ParkRefusal.NOT_LIVE_HERE,
        ),
    )
    deposit = SteerDepositResult(
        dispatch_id="disp-esc",
        entry_id="e-esc",
        authority_turn_id="5",
        spool_path="/tmp/x",
    )
    escalate_idle_to_park(deposit, reason="idle without MCP tool-call ack")
    assert any(ev.signal == "frontier.sdk.steer.inject.escalated" for ev in events)


@pytest.mark.asyncio
async def test_inject_one_dispatch_returns_pending_handle(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    monkeypatch.setenv("DATA_DIR", str(spool.parent))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="10479",
        model="cursor/composer-2.5",
        dispatch_id="disp-live",
        execution_id="exec-live",
        message="run",
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id="disp-live",
        thread_id="10479",
        model_id="composer-2.5",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
        source_repo="/tmp/repo",
    )
    ledger.mark_running(dispatch_id="disp-live")
    register_live_run(
        dispatch_id="disp-live",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject_http.deposit_steer_directive",
        lambda **_k: SteerDepositResult(
            dispatch_id="disp-live",
            entry_id="e-live",
            authority_turn_id="77",
            spool_path=str(spool / "disp-live.json"),
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject_http.recover_undelivered_steer_from_thread",
        lambda **_k: [],
    )
    status, body = await inject_one_dispatch(
        dispatch_id="disp-live",
        directive="check logs",
        reason="operator steer",
        actor="cursor",
        ttl_s=300,
    )
    unregister_live_run(dispatch_id="disp-live")
    assert status == 202
    assert body["inject_state"] == "pending"
    assert body["execution_id"] == "exec-live"
    assert body["steer"] == "inject"
    assert "park_kind" not in body


@pytest.mark.asyncio
async def test_inject_preflight_not_found() -> None:
    pre = preflight_inject("does-not-exist")
    assert pre.refusal is InjectRefusal.NOT_FOUND


@pytest.mark.asyncio
async def test_inject_preflight_not_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="10479",
        model="cursor/composer-2.5",
        dispatch_id="disp-idle",
        execution_id="exec-idle",
        message="run",
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id="disp-idle",
        thread_id="10479",
        model_id="composer-2.5",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
        source_repo="/tmp/repo",
    )
    ledger.mark_running(dispatch_id="disp-idle")
    pre = preflight_inject("disp-idle")
    assert pre.refusal is InjectRefusal.NOT_LIVE


@pytest.mark.asyncio
async def test_inject_one_dispatch_missing_directive_422() -> None:
    status, body = await inject_one_dispatch(
        dispatch_id="disp-any",
        directive="   ",
        reason="test",
        actor="cursor",
        ttl_s=None,
    )
    assert status == 422
    assert body["code"] == "CURSOR_INJECT_DIRECTIVE_REQUIRED"


def test_recover_undelivered_steer_from_thread_re_spools(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject._fetch_thread_turns",
        lambda _tid: [
            {
                "turn_number": 55,
                "subject": "STEER directive",
                "body": (
                    '{"dispatch_id":"disp-rec","directive":"resume harvest",'
                    '"reason":"bridge restart","actor":"steer-inject"}'
                ),
            }
        ],
    )
    recovered = recover_undelivered_steer_from_thread(
        dispatch_id="disp-rec",
        thread_id="10479",
        spool_dir=spool,
    )
    assert len(recovered) == 1
    assert recovered[0].authority_turn_id == "55"
    from scripts.mcp_bridge_steer_inject import claim_pending

    pending = claim_pending("disp-rec", spool_dir=spool)
    assert pending is not None
    assert pending.directive == "resume harvest"
