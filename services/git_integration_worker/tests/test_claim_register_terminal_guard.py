"""Packet A S1 — post_terminal_status claim-register partial guard.

Fail-closed at Claimed construction (unit-tested in libs/claim_register);
never fail-closed at POST — bare fix_hint posts with register=unknown.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from claim_register import CLAIM_REGISTER_UNKNOWN, claimed_derived

from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def _job() -> AutoJob:
    return AutoJob(
        job_id="j-claim-reg",
        thread_id="6655",
        turn_number=1,
        subject="claim register guard",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="low",
        contract="implement",
    )


def test_post_terminal_status_degrades_bare_fix_hint_does_not_raise() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    result = asyncio.run(
        post_terminal_status(
            _job(),
            client=client,
            queue=MagicMock(),
            summary="blocked",
            disposition="blocked",
            contract="implement",
            terminal_status="status:blocked",
            payload={
                "summary": "blocked",
                "reason": "empty_directive_scope",
                "fix_hint": "bare counsel string",
            },
            failed=True,
        )
    )
    assert result["status_code"] == 200
    body = json.loads(client.reply.await_args.kwargs["body"])
    assert body["fix_hint"]["register"] == CLAIM_REGISTER_UNKNOWN
    assert body["fix_hint"]["value"] == "bare counsel string"
    # Turn was posted — not dropped (post never fail-closed).
    client.reply.assert_awaited_once()


def test_post_terminal_status_passes_typed_fix_hint() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    typed = claimed_derived("typed", basis="test").to_wire()
    asyncio.run(
        post_terminal_status(
            _job(),
            client=client,
            queue=MagicMock(),
            summary="blocked",
            disposition="blocked",
            contract="implement",
            terminal_status="status:blocked",
            payload={"summary": "blocked", "fix_hint": typed},
            failed=True,
        )
    )
    body = json.loads(client.reply.await_args.kwargs["body"])
    assert body["fix_hint"]["register"] == "derived"
    assert body["fix_hint"]["value"] == "typed"
