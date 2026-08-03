"""Unit tests for DIRECTIVE effective_contract upgrade (agent-bus:6163)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.directive import effective_contract
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_contract_disposition,
)

_DIRECTIVE_MECHANICAL = (
    "TYPE: DIRECTIVE\ndensity: mechanical\nscope: libs/foo\nImplement the thing."
)


@pytest.mark.parametrize(
    ("wire", "body", "expected"),
    [
        ("answer", _DIRECTIVE_MECHANICAL, "implement"),
        ("answer", "TYPE: DIRECTIVE\ncontract: answer\nscope: foo", "answer"),
        ("answer", "hello question", "answer"),
        ("investigate", _DIRECTIVE_MECHANICAL, "investigate"),
        (None, _DIRECTIVE_MECHANICAL, "implement"),
        ("answer", "TYPE: DIRECTIVE\ncontract: Confer\nscope: foo", "confer"),
        ("answer", "TYPE: DIRECTIVE\ncontract: unknown-token\nscope: foo", "unknown-token"),
    ],
)
def test_effective_contract(wire: str | None, body: str, expected: str) -> None:
    assert effective_contract(wire, body) == expected


def test_effective_contract_unknown_body_passes_through_to_wire_map() -> None:
    body = "TYPE: DIRECTIVE\ncontract: unknown-token\nscope: foo"
    resolved = resolve_contract_disposition(effective_contract("answer", body))
    assert resolved["contract"] == "answer"
    assert resolved["disposition_hint"] == "answered"


def test_process_job_directive_answer_upgrades_to_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    """6202-shaped body must not short-circuit to terminal_in_seat answered."""
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock(
        return_value={"ok": True, "dispatch_id": "d-6202-shape"}
    )
    poll = AsyncMock(
        return_value={
            "terminal": True,
            "status": "done",
            "superseded": False,
        }
    )
    fetch_closeout = AsyncMock(return_value="done summary")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal",
        poll,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.fetch_sdk_closeout_body",
        fetch_closeout,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.settle_supersede",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.blocking_admit_gate",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )
    relay = AsyncMock(return_value={"ok": True, "status_code": 200})
    wake = AsyncMock(return_value={"ok": True, "status_code": 200})
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        relay,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        wake,
    )

    job = AutoJob(
        job_id="j-6202-shape",
        thread_id="6202",
        turn_number=1,
        subject="Implement effective_contract",
        body=_DIRECTIVE_MECHANICAL,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="answer",
    )

    result = asyncio.run(process_job(job, bus=bus))
    submit.assert_awaited_once()
    assert submit.await_args.kwargs["handoff_contract"] == "pure-mechanical"
    admit_call = bus.reply.await_args_list[0]
    assert "contract=implement" in admit_call.kwargs["body"]
    assert "handoff=pure-mechanical" in admit_call.kwargs["body"]
    assert result.get("ok") is True
    assert result.get("phase") == "nested_dispatch"
    assert job.contract == "implement"
