"""CHECKPOINT body tokens and duplicate suppression."""

from __future__ import annotations

import pytest
from agent_bus_store.checkpoint_projection import (
    CANONICAL_RESUME_FOOTER,
    extract_authored_resume_footer,
)

from .post import run_post

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_body_and_duplicate() -> None:
    sent: list[dict] = []

    async def get(path: str, params=None):
        return {"turns": []}, 200

    async def send(**kwargs):
        sent.append(kwargs)
        return {"turn_number": 4}, 200

    out = await run_post(
        {
            "house": "h",
            "n": 3,
            "root_thread": "9",
            "agent_id": "agent-1",
            "residue": "note",
            "channel": "hop",
            "bus_identity": "bot",
            "verifier": "verifier",
        },
        {"session_id": "sess"},
        {
            "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
            "turn_count": 2,
            "coverage": "full",
            "messages_sha256": "abc123",
            "surface": "cursor",
            "provenance": "raw",
            "producer": "cursor-jsonl-extractor",
        },
        get=get,
        send=send,
    )
    body = out["body"]
    assert "Window: agent_id=agent-1" in body
    assert "messages_sha256:abc123" in body
    assert "provenance:raw" in body
    assert "producer:cursor-jsonl-extractor" in body
    assert "capture:none" in body
    assert extract_authored_resume_footer(body) == CANONICAL_RESUME_FOOTER
    assert "messages_sha256:abc123" in body
    async def get_dup(path: str, params=None):
        return {"turns": [{"body": body, "subject": "CHECKPOINT"}]}, 200

    again = await run_post(
        {
            "house": "h",
            "n": 3,
            "root_thread": "9",
            "agent_id": "agent-1",
            "bus_identity": "bot",
            "verifier": "verifier",
        },
        {"session_id": "sess"},
        {
            "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
            "turn_count": 2,
            "messages_sha256": "abc123",
            "provenance": "raw",
            "producer": "p",
        },
        get=get_dup,
        send=send,
    )
    assert again["duplicate"] is True
    assert len(sent) == 1
