"""Arc 6655 — contract=seed must nest (non-null dispatch_id) or refuse legibly."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.handler import (
    _NESTED_CONTRACTS,
    process_job,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.wire_map import _CONTRACTS


def test_nested_contracts_includes_seed() -> None:
    assert "seed" in _NESTED_CONTRACTS


def test_admit_contracts_seed_maps_to_light_bounded_handoff() -> None:
    from services.git_integration_worker.cursor_auto.wire_map import resolve_handoff_contract

    assert "seed" in _CONTRACTS
    assert resolve_handoff_contract("seed") == "light-bounded"


def _seed_directive_body() -> str:
    return (
        "TYPE: DIRECTIVE\n"
        "contract: seed\n"
        "density: dense\n"
        "## Scope\n"
        "todo:contract-seed-nested-dispatch\n"
        "vision: pillar-4 — seed must execute or refuse, never hollow done\n"
    )


def _pass_through_admit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )
    return bus


def test_process_job_seed_submits_nested_dispatch_with_dispatch_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: seed must reach submit_nested_dispatch with dispatch_id set."""
    bus = _pass_through_admit(monkeypatch)
    submit = AsyncMock(
        return_value={
            "ok": True,
            "dispatch_id": "auto-seed-nested-001",
            "execution_id": "exec-auto-seed-nested-001",
        }
    )
    polled = AsyncMock(
        return_value={"ok": True, "terminal": True, "status": "completed"}
    )
    sdk_body = AsyncMock(return_value="status: complete\n")
    relay = AsyncMock(return_value={"ok": True, "status_code": 200})
    wake = AsyncMock(return_value={"ok": True, "status_code": 200})
    delivery = AsyncMock(return_value={"ok": True, "send_verified": True})

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal_with_liveness",
        polled,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.fetch_sdk_closeout_body",
        sdk_body,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        relay,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        wake,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.maybe_deliver_cse_wake",
        delivery,
    )

    job = AutoJob(
        job_id="j-seed-nest",
        thread_id="6655",
        turn_number=2487,
        subject="seed nested dispatch",
        body=_seed_directive_body(),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="low",
        contract="seed",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["ok"] is True
    assert result["phase"] == "nested_dispatch"
    submit.assert_awaited_once()
    assert submit.await_args.kwargs["handoff_contract"] == "light-bounded"
    assert submit.return_value["dispatch_id"] == "auto-seed-nested-001"
    assert result.get("dispatch_id") == "auto-seed-nested-001"


def test_process_job_seed_does_not_hollow_done_in_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: broken path posted status:done with dispatch_id null (~181ms)."""
    bus = _pass_through_admit(monkeypatch)
    submit = AsyncMock(
        return_value={
            "ok": False,
            "error": "stop-after-admit-proof",
        }
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )

    job = AutoJob(
        job_id="j-seed-no-hollow",
        thread_id="6655",
        turn_number=2488,
        subject="seed no hollow",
        body=_seed_directive_body(),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="low",
        contract="seed",
    )

    result = asyncio.run(process_job(job, bus=bus))
    submit.assert_awaited_once()
    assert result["terminal_status"] == "status:failed"
    payload = json.loads(bus.reply.await_args_list[-1].kwargs["body"])
    assert "v0 in-seat Auto handled contract=seed" not in payload.get("summary", "")
