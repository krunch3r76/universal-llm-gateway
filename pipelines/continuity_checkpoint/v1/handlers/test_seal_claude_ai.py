"""Unit tests for claude_ai succession seal."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from .seal import ContinuityCheckpointSealHandler
from .seal_claude_ai import _dedupe_turns, seal_claude_ai

pytestmark = pytest.mark.offline

_CSE_URL = "https://claude.ai/cowork/cse_0181xcjbYP83D8VdBopSyiLs"
_CSE_ID = "cse_0181xcjbYP83D8VdBopSyiLs"


def test_dedupe_collapses_doubled_assistant_reply() -> None:
    turns = [
        {"author": "assistant", "text": "Hello world", "ordinal": 1},
        {"author": "assistant", "text": "Claude responded: Hello world", "ordinal": 2},
    ]
    deduped = _dedupe_turns(turns)
    assert len(deduped) == 1
    assert deduped[0]["ordinal"] == 1


@pytest.mark.asyncio
async def test_seal_claude_ai_happy_path_tail() -> None:
    harvest = AsyncMock(
        return_value={
            "outcome": "harvested",
            "content_provenance": "cse-dom",
            "turns": [
                {"author": "assistant", "text": "Hello world", "ordinal": 1},
                {
                    "author": "assistant",
                    "text": "Claude responded: Hello world",
                    "ordinal": 2,
                },
            ],
            "truncated": False,
            "cursor": 2,
        }
    )
    dispatch = AsyncMock(
        return_value={
            "session_id": "web-anthropic-2026-09-10-120000-abc",
            "journal_row_id": 7,
            "turn_count": 1,
            "content_hash": "sha256:deadbeef",
            "transcript_entity_id": "transcript:web-anthropic-2026-09-10-120000-abc",
        }
    )
    with (
        patch(
            "cortex_store.session_close_successor_hop.lookup_journaled_by_conversation_uuid",
            return_value=None,
        ),
        patch(
            "handlers.seal_claude_ai._lookup_session_id_for_transcript",
            return_value=None,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_transcript_seal._stamp_succession_fields",
            return_value=None,
        ),
    ):
        payload = await seal_claude_ai(
            thread="10479",
            chat_url=_CSE_URL,
            transcript_id=_CSE_ID,
            from_agent="cursor",
            harvest_fn=harvest,
            cortex_dispatch_fn=dispatch,
        )
    assert payload["refused"] is None
    assert payload["coverage"] == "tail"
    assert payload["turn_count"] == 1
    assert payload["content_provenance"] == "cse-dom"
    close_args = dispatch.await_args.args[1]
    assert close_args["agent"] == "web-anthropic"
    assert close_args["closed_by"] == "succession"
    assert close_args["succession_seal_authority"] is True
    assert close_args["transcript_messages"]["messages"][0]["role"] == "assistant"
    assert close_args["transcript_messages"]["meta"]["coverage"] == "tail"


@pytest.mark.asyncio
async def test_seal_claude_ai_truncated_harvest_downgrades_to_tail() -> None:
    harvest = AsyncMock(
        return_value={
            "outcome": "harvested",
            "content_provenance": "cse-dom",
            "coverage": "full",
            "turns": [
                {"author": "user", "text": "User turn text here.", "ordinal": 1},
                {"author": "assistant", "text": "Assistant reply here.", "ordinal": 2},
                {"author": "user", "text": "Follow-up question.", "ordinal": 3},
                {"author": "assistant", "text": "Follow-up answer.", "ordinal": 4},
            ],
            "truncated": True,
            "cursor": 4,
        }
    )
    dispatch = AsyncMock(
        return_value={
            "session_id": "web-anthropic-2026-09-10-120000-abc",
            "journal_row_id": 7,
            "turn_count": 4,
            "content_hash": "sha256:deadbeef",
            "transcript_entity_id": "transcript:web-anthropic-2026-09-10-120000-abc",
        }
    )
    with (
        patch(
            "cortex_store.session_close_successor_hop.lookup_journaled_by_conversation_uuid",
            return_value=None,
        ),
        patch(
            "handlers.seal_claude_ai._lookup_session_id_for_transcript",
            return_value=None,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_transcript_seal._stamp_succession_fields",
            return_value=None,
        ),
    ):
        payload = await seal_claude_ai(
            thread="10479",
            chat_url=_CSE_URL,
            transcript_id=_CSE_ID,
            from_agent="cursor",
            harvest_fn=harvest,
            cortex_dispatch_fn=dispatch,
        )
    assert payload["refused"] is None
    assert payload["coverage"] == "tail"
    close_args = dispatch.await_args.args[1]
    assert close_args["transcript_messages"]["meta"]["coverage"] == "tail"
    assert close_args["transcript_messages"]["meta"]["truncated"] is True


@pytest.mark.asyncio
async def test_seal_claude_ai_harvest_declared_full_coverage() -> None:
    harvest = AsyncMock(
        return_value={
            "outcome": "harvested",
            "content_provenance": "cse-dom",
            "coverage": "full",
            "turns": [
                {"author": "user", "text": "User turn text here.", "ordinal": 1},
                {"author": "assistant", "text": "Assistant reply here.", "ordinal": 2},
            ],
            "truncated": False,
            "cursor": 2,
        }
    )
    dispatch = AsyncMock(
        return_value={
            "session_id": "web-anthropic-2026-09-10-120000-abc",
            "journal_row_id": 7,
            "turn_count": 2,
            "content_hash": "sha256:deadbeef",
            "transcript_entity_id": "transcript:web-anthropic-2026-09-10-120000-abc",
        }
    )
    with (
        patch(
            "cortex_store.session_close_successor_hop.lookup_journaled_by_conversation_uuid",
            return_value=None,
        ),
        patch(
            "handlers.seal_claude_ai._lookup_session_id_for_transcript",
            return_value=None,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_transcript_seal._stamp_succession_fields",
            return_value=None,
        ),
    ):
        payload = await seal_claude_ai(
            thread="10479",
            chat_url=_CSE_URL,
            transcript_id=_CSE_ID,
            from_agent="cursor",
            harvest_fn=harvest,
            cortex_dispatch_fn=dispatch,
        )
    assert payload["refused"] is None
    assert payload["coverage"] == "full"
    close_args = dispatch.await_args.args[1]
    assert close_args["transcript_messages"]["meta"]["coverage"] == "full"
    assert close_args["transcript_messages"]["meta"]["truncated"] is False


@pytest.mark.asyncio
async def test_seal_claude_ai_already_journaled_returns_seal_fingerprint() -> None:
    harvest = AsyncMock(
        return_value={
            "outcome": "harvested",
            "turns": [
                {"author": "assistant", "text": "Hello world", "ordinal": 1},
            ],
            "truncated": False,
        }
    )
    dispatch = AsyncMock()
    human_closed = SimpleNamespace(session_id="web-anthropic-prior-session")
    with (
        patch(
            "cortex_store.session_close_successor_hop.lookup_journaled_by_conversation_uuid",
            return_value=human_closed,
        ),
        patch(
            "handlers.seal_claude_ai._lookup_journal_seal_meta",
            return_value=("sha256:abc123", "messages-v1"),
        ),
    ):
        payload = await seal_claude_ai(
            thread="10479",
            chat_url=_CSE_URL,
            transcript_id=_CSE_ID,
            from_agent="cursor",
            harvest_fn=harvest,
            cortex_dispatch_fn=dispatch,
        )
    assert payload["refused"] is None
    assert payload["already_closed"] is True
    assert payload["messages_sha256"] == "sha256:abc123"
    assert payload["verbatim_codec"] == "messages-v1"
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_seal_claude_ai_not_attached_refused() -> None:
    harvest = AsyncMock(return_value={"outcome": "not_attached", "reason": "no lane"})
    dispatch = AsyncMock()
    payload = await seal_claude_ai(
        thread="10479",
        chat_url=_CSE_URL,
        transcript_id=_CSE_ID,
        from_agent="cursor",
        harvest_fn=harvest,
        cortex_dispatch_fn=dispatch,
    )
    assert payload["refused"]["code"] == "checkpoint.harvest_unavailable"
    assert "not_attached" in payload["refused"]["message"]
    dispatch.assert_not_called()


class _ClaudeCtx:
    execution_id = "exec-claude"
    dispatch_thread_id = "10479"
    options = {"thread": "10479", "surface": "claude_ai", "from_agent": "cursor"}
    outputs = {
        "resolve": {
            "json": {
                "chat_url": _CSE_URL,
                "transcript_id": _CSE_ID,
            }
        }
    }


class _Step:
    pass


@pytest.mark.asyncio
async def test_seal_handler_claude_ai_branch() -> None:
    handler = ContinuityCheckpointSealHandler()
    with patch(
        "handlers.seal.seal_claude_ai",
        new=AsyncMock(
            return_value={
                "session_id": "web-anthropic-2026-test",
                "transcript_id": _CSE_ID,
                "turn_count": 1,
                "coverage": "tail",
                "chat_url": _CSE_URL,
                "refused": None,
            }
        ),
    ):
        out = await handler.execute(_Step(), _ClaudeCtx())
    assert out.json["coverage"] == "tail"
    assert out.json["refused"] is None
