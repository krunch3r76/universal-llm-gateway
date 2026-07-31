"""Unit tests for cursor-auto first-episode admit BRIEFING."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from foo.briefing_fixture import (
    BASE_ADMIT_BODY,
    BRIEFING_FORBIDDEN_SUBSTRINGS,
    BRIEFING_HEADER,
    BRIEFING_REQUIRED_SUBSTRINGS,
    MAX_BRIEFING_LINES,
    MINI_BRIEFING_SAMPLE,
    TURNS_FIRST_EPISODE,
    TURNS_FOLLOW_ON,
    TURNS_NO_PRIOR_ADMITS,
    TURNS_PRIOR_ADMIT,
)

from services.git_integration_worker.cursor_auto.episode_briefing import (
    _CODE_WORK_CONTRACTS,
    _MAX_BRIEFING_LINES,
    build_briefing_block,
    compose_admit_body,
    is_first_episode_admit,
    maybe_briefing_for_admit,
)
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.queue import AutoJob


def test_is_first_episode_admit_no_prior_admits():
    assert is_first_episode_admit(TURNS_NO_PRIOR_ADMITS) is True


def test_is_first_episode_admit_prior_admit():
    assert is_first_episode_admit(TURNS_PRIOR_ADMIT) is False


def test_build_briefing_block_shape_and_tone():
    block = build_briefing_block()
    lines = block.splitlines()
    assert lines[0] == BRIEFING_HEADER
    assert len(lines) <= MAX_BRIEFING_LINES
    for needle in BRIEFING_REQUIRED_SUBSTRINGS:
        assert needle in block
    for forbidden in BRIEFING_FORBIDDEN_SUBSTRINGS:
        if forbidden in ("you lack", "don't have"):
            assert forbidden not in block.lower()
        else:
            assert forbidden not in block


def test_build_briefing_block_code_work_stanza():
    block = build_briefing_block(contract="implement")
    assert "Codework (implement)" in block
    assert "abstraction-layering" in block
    assert "CLOSEOUT" in block
    assert len(block.splitlines()) <= MAX_BRIEFING_LINES
    assert "cursor-auto lane" in block
    assert "manage / charter_reload" not in block


def test_build_briefing_block_seed_stanza():
    block = build_briefing_block(contract="seed")
    assert "work-item-seed-path" in block
    assert "S1→S6" in block
    assert "entry gate" in block
    assert len(block.splitlines()) <= MAX_BRIEFING_LINES


def test_build_briefing_block_under_cap_for_all_contracts():
    contracts = set(_CODE_WORK_CONTRACTS) | {"confer", None}
    assert MAX_BRIEFING_LINES == _MAX_BRIEFING_LINES
    for contract in contracts:
        block = build_briefing_block(contract=contract)
        line_count = len(block.splitlines())
        assert line_count <= _MAX_BRIEFING_LINES, (
            f"contract={contract!r} produced {line_count} lines"
        )


def test_compose_admit_body_with_and_without_briefing():
    assert compose_admit_body(BASE_ADMIT_BODY, None) == BASE_ADMIT_BODY
    assert compose_admit_body(BASE_ADMIT_BODY, MINI_BRIEFING_SAMPLE) == (
        f"{BASE_ADMIT_BODY}\n\n{MINI_BRIEFING_SAMPLE}"
    )


def test_maybe_briefing_fail_closed_on_fetch_failure():
    async def fail_fetch(_thread_id: str):
        return None

    result = asyncio.run(maybe_briefing_for_admit("5899", fetch_turns=fail_fetch))
    assert result is None


def test_maybe_briefing_omits_on_follow_on_episode():
    async def follow_on_fetch(_thread_id: str):
        return list(TURNS_FOLLOW_ON)

    result = asyncio.run(maybe_briefing_for_admit("5899", fetch_turns=follow_on_fetch))
    assert result is None


def test_maybe_briefing_includes_on_first_episode():
    async def first_fetch(_thread_id: str):
        return list(TURNS_FIRST_EPISODE)

    result = asyncio.run(maybe_briefing_for_admit("5899", fetch_turns=first_fetch))
    assert result is not None
    assert result.startswith(BRIEFING_HEADER)


def test_process_job_admit_includes_briefing_first_episode(monkeypatch):
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    async def first_fetch(_thread_id: str):
        return []

    # admit_gates imports fetch_thread_turns by name — patch both call sites.
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.episode_briefing.fetch_thread_turns",
        first_fetch,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        first_fetch,
    )
    poll = AsyncMock(
        return_value={
            "ok": True,
            "terminal": True,
            "dispatch_id": "auto-brief",
            "status": "completed",
        }
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        AsyncMock(return_value={"ok": True, "dispatch_id": "auto-brief"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal",
        poll,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.fetch_sdk_closeout_body",
        AsyncMock(return_value="TYPE: CLOSEOUT\nstatus: complete\n"),
    )
    relay = AsyncMock(return_value={"ok": True})
    wake = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        relay,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        wake,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kwargs: {"active": 0, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )

    job = AutoJob(
        job_id="j-brief-1",
        thread_id="5899",
        turn_number=1,
        subject="operator question",
        body="Help me understand the next charter step.\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="answer",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["ok"] is True
    assert bus.reply.await_count >= 1
    admit_body = bus.reply.await_args_list[0].kwargs["body"]
    assert "TYPE: BRIEFING" in admit_body
    assert "cursor-auto lane" in admit_body
    assert "live_deltas" in admit_body
    # Standing posture moved to cursor_request descriptor — not re-sent each admit.
    assert "NEW CDP WINDOW" not in admit_body
    assert "front-door bind" not in admit_body
    # contract=answer → soft only (no codebase-work stanza / no lane routing)
    assert "Codework (" not in admit_body
    assert "abstraction-layering" not in admit_body


def test_process_job_admit_omits_briefing_follow_on(monkeypatch):
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    async def follow_on_fetch(_thread_id: str):
        return list(TURNS_FOLLOW_ON)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.episode_briefing.fetch_thread_turns",
        follow_on_fetch,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        follow_on_fetch,
    )
    poll = AsyncMock(
        return_value={
            "ok": True,
            "terminal": True,
            "dispatch_id": "auto-brief",
            "status": "completed",
        }
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        AsyncMock(return_value={"ok": True, "dispatch_id": "auto-brief"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal",
        poll,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.fetch_sdk_closeout_body",
        AsyncMock(return_value="TYPE: CLOSEOUT\nstatus: complete\n"),
    )
    relay = AsyncMock(return_value={"ok": True})
    wake = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        relay,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        wake,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kwargs: {"active": 0, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )

    job = AutoJob(
        job_id="j-brief-2",
        thread_id="5899",
        turn_number=5,
        subject="operator question",
        body="Follow-on operator question.\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="answer",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["ok"] is True
    admit_body = bus.reply.await_args_list[0].kwargs["body"]
    assert "TYPE: BRIEFING" not in admit_body
