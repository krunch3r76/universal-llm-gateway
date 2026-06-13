"""Regression: cursor-sdk implement-generate admission (messages-fold).

Pins the behavior verified live on 2026-06-13 (agent-bus thread 1724; the live
symptom was a deploy-lag, not a code defect). The route's cursor-sdk generate
intercept must:

- ADMIT ``contract="implement"`` with a ``packet_path`` and NO caller-supplied
  messages, routing to the SDK orchestrator;
- NOT read the dispatch thread for implement (the implement corpus is the packet,
  not assembled dispatch-thread message text);
- still require caller-owned dispatch-thread context for non-implement contracts;
- never accept a public ``messages[]`` field on the folded generate wire.

Guards friction 17195 / assertion 17200. See
cortex:notes/system/threads/1724-messages-fold-implement-densify-findings.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from pydantic import ValidationError

from .route import TeamDispatchGenerateBody, team_dispatch


def _patch_sdk_and_thread_read(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sdk_return: dict[str, str],
    thread_body: str,
) -> tuple[AsyncMock, AsyncMock]:
    """Patch the SDK orchestrator + dispatch-thread reader on the route module."""
    sdk_mock = AsyncMock(return_value=sdk_return)
    thread_read = AsyncMock(return_value=thread_body)
    monkeypatch.setattr(
        "systems.frontier_consult.route.dispatch_cursor_sdk_generate", sdk_mock
    )
    monkeypatch.setattr(
        "systems.frontier_consult.route.read_latest_dispatch_thread_body", thread_read
    )
    return sdk_mock, thread_read


@pytest.mark.asyncio
async def test_cursor_sdk_implement_admits_without_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """implement + packet_path admits via the SDK orchestrator; thread never read."""
    sdk_mock, thread_read = _patch_sdk_and_thread_read(
        monkeypatch,
        sdk_return={"execution_id": "exec-1", "thread_id": "1726"},
        thread_body="should-not-be-read",
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="implement",
        packet_path="tmp/reviews/packet.md",
    )
    result = await team_dispatch(body, Response())

    assert result == {"execution_id": "exec-1", "thread_id": "1726"}
    # The implement corpus is the packet — the dispatch thread is never read,
    # so no "user message required" gate can fire on this path.
    thread_read.assert_not_awaited()
    sdk_mock.assert_awaited_once()
    kwargs = sdk_mock.await_args.kwargs
    assert kwargs["contract"] == "implement"
    assert kwargs["packet_path"] == "tmp/reviews/packet.md"
    assert kwargs["message_text"] == ""  # source_text="" for implement


@pytest.mark.asyncio
async def test_cursor_sdk_non_implement_reads_dispatch_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-implement still requires caller-owned context from the dispatch thread."""
    sdk_mock, thread_read = _patch_sdk_and_thread_read(
        monkeypatch,
        sdk_return={"execution_id": "exec-2", "thread_id": "1727"},
        thread_body="caller context",
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="light-bounded",
    )
    await team_dispatch(body, Response())

    thread_read.assert_awaited_once()
    sdk_mock.assert_awaited_once()
    assert sdk_mock.await_args.kwargs["message_text"] == "caller context"


def test_team_generate_body_forbids_public_messages() -> None:
    """Folded wire: public messages[] must never be accepted on generate."""
    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            role="cursor-sdk",
            dispatch_thread_id="todo:some-arc",
            contract="implement",
            packet_path="tmp/reviews/packet.md",
            messages=[{"role": "user", "content": "x"}],  # type: ignore[call-arg]
        )
