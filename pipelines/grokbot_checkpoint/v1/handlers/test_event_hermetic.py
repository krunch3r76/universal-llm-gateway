"""Checkpoint bus events must not reach cortex record during handler tests."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from ._test_call_post import call_post
from . import post as post_mod, seal as seal_mod

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_no_checkpoint_event_reaches_record(monkeypatch) -> None:
    assert isinstance(post_mod.stargate_continuity_checkpoint_posted, MagicMock)
    assert isinstance(seal_mod.stargate_continuity_checkpoint_sealed, MagicMock)

    import continuity_tape.events_checkpoint as events_checkpoint
    import cortex_store.event_publisher as event_publisher

    record_ckpt = MagicMock(name="record_ckpt")
    record_pub = MagicMock(name="record_pub")
    monkeypatch.setattr(events_checkpoint, "record", record_ckpt)
    monkeypatch.setattr(event_publisher, "record", record_pub)

    async def get(path: str, params=None):
        if path.startswith("/threads/") and path.endswith("/turns"):
            return {"detail": "Not Found"}, 404
        if path == "/turns/by-number":
            return {"turn_number": 0}, 200
        if path == "/turns" and params and params.get("thread") == "9":
            return {"turns": []}, 200
        return {}, 404

    async def send(**kwargs):
        return {"turn": {"turn_number": 4}}, 201

    await call_post(
        {
            "house": "h",
            "n": 3,
            "root_thread": "9",
            "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
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
        get=get,
        send=send,
    )

    post_mod.stargate_continuity_checkpoint_posted(
        execution_id="e",
        thread="9",
        surface="grok",
        from_agent="bot",
        bus_turn=4,
    )
    seal_mod.stargate_continuity_checkpoint_sealed(
        execution_id="e",
        thread="9",
        surface="grok",
        from_agent="bot",
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        turns_at_cp=2,
        session_id="sess",
    )

    assert record_ckpt.call_count == 0
    assert record_pub.call_count == 0
    assert post_mod.stargate_continuity_checkpoint_posted.call_count >= 1
