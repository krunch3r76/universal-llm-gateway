"""Unit tests for ContinuityCheckpointResolveHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from .resolve import ContinuityCheckpointResolveHandler

pytestmark = pytest.mark.offline


class _Ctx:
    execution_id = "exec-001"
    dispatch_thread_id = "10223"
    options = {
        "thread": "10223",
        "surface": "cursor",
        "from_agent": "cursor",
        "jsonl_path": "abc/uuid/file.jsonl",
    }


class _Step:
    pass


@pytest.mark.asyncio
async def test_cursor_jsonl_path_passthrough() -> None:
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), _Ctx())
    assert out.json["jsonl_path"] == "abc/uuid/file.jsonl"


@pytest.mark.asyncio
async def test_claude_ai_resolve_happy_path() -> None:
    ctx = _Ctx()
    ctx.options = {
        "thread": "10223",
        "surface": "claude_ai",
        "from_agent": "cursor",
        "chat_url": "https://claude.ai/cowork/cse_0181xcjbYP83D8VdBopSyiLs",
    }
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json["transcript_id"] == "cse_0181xcjbYP83D8VdBopSyiLs"
    assert out.json["chat_url"].endswith("cse_0181xcjbYP83D8VdBopSyiLs")


@pytest.mark.asyncio
async def test_claude_ai_resolve_missing_chat_url() -> None:
    ctx = _Ctx()
    ctx.options = {"thread": "10223", "surface": "claude_ai", "from_agent": "cursor"}
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json["refused"]["code"] == "checkpoint.chat_url_required"


@pytest.mark.asyncio
async def test_claude_ai_resolve_unclassified_url() -> None:
    ctx = _Ctx()
    ctx.options = {
        "thread": "10223",
        "surface": "claude_ai",
        "from_agent": "cursor",
        "chat_url": "https://example.com/",
    }
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json["refused"]["code"] == "checkpoint.chat_url_unclassified"


@pytest.mark.asyncio
async def test_discover_single_dominant_write() -> None:
    ctx = _Ctx()
    ctx.options = {"thread": "10223", "surface": "cursor", "from_agent": "cursor"}
    handler = ContinuityCheckpointResolveHandler()
    with patch(
        "handlers.resolve.cortex_dispatch",
        new=AsyncMock(
            return_value={
                "open_windows": [
                    {
                        "transcript_id": "uuid-1",
                        "jsonl_path": "rel/path.jsonl",
                        "binding": "dominant_write",
                    }
                ]
            }
        ),
    ):
        out = await handler.execute(_Step(), ctx)
    assert out.json["jsonl_path"] == "rel/path.jsonl"
