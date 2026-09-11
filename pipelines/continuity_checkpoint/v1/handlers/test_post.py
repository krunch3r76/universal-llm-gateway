"""Unit tests for ContinuityCheckpointPostHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from .post import ContinuityCheckpointPostHandler, _compose_body

pytestmark = pytest.mark.offline


def test_compose_body_refused_seal_omits_window() -> None:
    body = _compose_body(
        residue="WIP",
        seal={"refused": {"code": "transcript_seal.not_lane_window"}},
        mission="resume",
    )
    assert "Window:" not in body
    assert "Harvest: refused(transcript_seal.not_lane_window)" in body


def test_compose_body_claude_ai_window_anchor() -> None:
    body = _compose_body(
        residue="WIP",
        seal={
            "transcript_id": "cse_abc123",
            "turn_count": 1,
            "session_id": "web-anthropic-test",
            "messages_sha256": "deadbeef",
            "chat_url": "https://claude.ai/cowork/cse_abc123",
            "coverage": "tail_only",
            "refused": None,
        },
        mission="resume",
        surface="claude_ai",
    )
    assert (
        "Window: chat_url=https://claude.ai/cowork/cse_abc123 · "
        "transcript_id=cse_abc123 · turns@cp=1 · coverage=tail_only"
    ) in body
    assert "surface:claude_ai" in body


def test_compose_body_success_anchor() -> None:
    body = _compose_body(
        residue="WIP",
        seal={
            "transcript_id": "uuid-1",
            "turn_count": 5,
            "session_id": "cursor-test",
            "messages_sha256": "deadbeef",
            "refused": None,
        },
        mission="ship 4b",
    )
    assert "Window: transcript_id=uuid-1 · turns@cp=5" in body
    assert "Harvest: transcript:cursor-test" in body


class _Ctx:
    execution_id = "exec-post"
    dispatch_thread_id = "10223"
    options = {"thread": "10223", "surface": "cursor", "from_agent": "continuity"}
    outputs = {
        "seal": {
            "json": {
                "transcript_id": "uuid-1",
                "turn_count": 3,
                "session_id": "cursor-test",
                "messages_sha256": "abc",
                "refused": None,
            }
        },
        "pre_consolidate": {
            "json": {
                "residue": "Mission: test post",
                "mission": "test post",
                "executor": "cursor-sdk",
                "card_patch_applied": False,
            }
        },
    }


class _Step:
    pass


@pytest.mark.asyncio
async def test_post_emits_terminal_result() -> None:
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10}, 200)),
        ),
        patch(
            "handlers.post.bus_send",
            new=AsyncMock(return_value=({"turn_number": 11}, 201)),
        ),
    ):
        out = await handler.execute(_Step(), _Ctx())
    assert out.json["status"] == "posted"
    assert out.json["pre_consolidate"]["executor"] == "cursor-sdk"
    assert out.json["harvest_entity_id"] == "transcript:cursor-test"
