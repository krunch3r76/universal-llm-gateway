"""Execute contract end-to-end through ``process_job`` — admission, run, refusal."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.execute_runner import (
    clear_tool_op_invoker,
    set_tool_op_invoker,
)
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.queue import AutoJob

_EXECUTE_BODY = (
    "TYPE: DIRECTIVE\n"
    "contract: execute\n"
    "tool_op: email.pull\n"
    "effects_expected: raw pull JSON inline\n"
    "density: sparse\n"
)


def _job(**overrides: object) -> AutoJob:
    base = dict(
        job_id="j-exec",
        thread_id="6328",
        turn_number=1,
        subject="tier-M pull",
        body=_EXECUTE_BODY,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="low",
        contract="execute",
    )
    base.update(overrides)
    return AutoJob(**base)


def _reply_payload(client: AsyncMock) -> dict:
    return json.loads(client.reply.await_args.kwargs["body"])


@pytest.mark.asyncio
async def test_execute_without_an_invoker_refuses_rather_than_claiming_done() -> None:
    """No tool surface registered ⇒ honest needs-attended, never status:done."""
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    clear_tool_op_invoker()
    with patch(
        "services.git_integration_worker.cursor_auto.handler.maybe_briefing_for_admit",
        new=AsyncMock(return_value=None),
    ):
        result = await process_job(_job(), bus=client)
    assert result["terminal_status"] == "status:needs-attended"
    terminal_calls = [
        c for c in client.reply.await_args_list if "needs-attended" in str(c)
    ]
    assert terminal_calls
    last = json.loads(terminal_calls[-1].kwargs["body"])
    assert last["reason"] == "execute_invoker_unconfigured"


@pytest.mark.asyncio
async def test_execute_with_invoker_relays_payload_in_seat() -> None:
    """With a tool surface, the op runs in seat and the payload rides inline."""
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    async def _invoker(*, tool: str, op: str, arguments: dict) -> dict:
        return {"fired": f"{tool}.{op}", "messages": []}

    set_tool_op_invoker(_invoker)
    try:
        with patch(
            "services.git_integration_worker.cursor_auto.handler."
            "maybe_briefing_for_admit",
            new=AsyncMock(return_value=None),
        ):
            result = await process_job(_job(), bus=client)
    finally:
        clear_tool_op_invoker()
    assert result["terminal_status"] == "status:done"
    assert result["disposition"] == "executed"
    payload = _reply_payload(client)
    assert payload["tool_payload"]["fired"] == "email.pull"
    assert payload["tool_op"] == "email.pull"


@pytest.mark.asyncio
async def test_execute_denied_op_blocks_before_the_runner() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    denied_body = _EXECUTE_BODY.replace("email.pull", "email.send")
    with patch(
        "services.git_integration_worker.cursor_auto.handler.maybe_briefing_for_admit",
        new=AsyncMock(return_value=None),
    ):
        result = await process_job(_job(body=denied_body), bus=client)
    assert result["terminal_status"] == "status:blocked"
    assert _reply_payload(client)["reason"] == "execute_tool_op_denied"


@pytest.mark.asyncio
async def test_execute_without_vision_not_blocked_by_vision_gate() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    with patch(
        "services.git_integration_worker.cursor_auto.handler.maybe_briefing_for_admit",
        new=AsyncMock(return_value=None),
    ):
        result = await process_job(_job(), bus=client)
    assert result["terminal_status"] == "status:needs-attended"
    subjects = [c.kwargs.get("subject", "") for c in client.reply.await_args_list]
    assert not any("vision_field_missing" in s for s in subjects)


@pytest.mark.asyncio
async def test_execute_denied_email_mutations_unwired_even_with_relay_flag(
    monkeypatch,
) -> None:
    """send/move/delete stay manifest-denied; flag must not wire them."""
    monkeypatch.setenv("EMAIL_BRIDGE_EXECUTE_RELAY_ENABLED", "1")
    from services.git_integration_worker.cursor_auto.execute_tool_op_invoker import (
        is_wired_tool_op,
    )

    assert is_wired_tool_op("email", "send") is False
    assert is_wired_tool_op("email", "move") is False
    assert is_wired_tool_op("email", "delete") is False


@pytest.mark.asyncio
async def test_execute_never_reaches_nested_submit() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    with (
        patch(
            "services.git_integration_worker.cursor_auto.handler.maybe_briefing_for_admit",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
            new=AsyncMock(),
        ) as submit,
    ):
        await process_job(_job(), bus=client)
    submit.assert_not_called()
