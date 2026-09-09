"""Unit tests for ContinuityCheckpointSealHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from .seal import ContinuityCheckpointSealHandler

pytestmark = pytest.mark.offline


class _Ctx:
    execution_id = "exec-002"
    dispatch_thread_id = "10223"
    options = {"thread": "10223", "surface": "cursor", "from_agent": "cursor"}
    outputs = {
        "resolve": {
            "json": {
                "jsonl_path": "rel/path.jsonl",
                "transcript_id": "uuid-1",
            }
        }
    }


class _Step:
    pass


@pytest.mark.asyncio
async def test_seal_success_shape() -> None:
    handler = ContinuityCheckpointSealHandler()
    with patch(
        "handlers.seal.cortex_dispatch",
        new=AsyncMock(
            return_value={
                "session_id": "cursor-2026-test",
                "journal_row_id": 99,
                "turn_count": 12,
                "content_hash": "abc123",
                "conversation_uuid": "uuid-1",
            }
        ),
    ):
        out = await handler.execute(_Step(), _Ctx())
    assert out.json["turn_count"] == 12
    assert out.json["refused"] is None
    assert out.json["messages_sha256"] == "abc123"


@pytest.mark.asyncio
async def test_seal_not_lane_window_refused() -> None:
    handler = ContinuityCheckpointSealHandler()
    with patch(
        "handlers.seal.cortex_dispatch",
        new=AsyncMock(
            return_value={
                "error": "window does not bind",
                "code": "transcript_seal.not_lane_window",
            }
        ),
    ):
        out = await handler.execute(_Step(), _Ctx())
    assert out.json["refused"]["code"] == "transcript_seal.not_lane_window"
