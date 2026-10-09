"""CHECKPOINT body tokens and duplicate suppression."""

from __future__ import annotations

import pytest
from agent_bus_store.checkpoint_projection import (
    CANONICAL_RESUME_FOOTER,
    extract_authored_resume_footer,
)

from .post import compose_checkpoint_body, run_post

pytestmark = pytest.mark.offline

_GOLDEN_KW = {
    "residue": "note",
    "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "turn_count": 2,
    "coverage": "full",
    "channel": "hop",
    "session_id": "sess",
    "messages_sha256": "abc123",
    "surface": "grok",
    "provenance": "raw",
    "producer": "cursor-jsonl-extractor",
    "house": "h",
    "n": 3,
    "capture": "dom_harvest@abc · pseudo_byte_exact",
}


def test_compose_checkpoint_body_golden() -> None:
    body = compose_checkpoint_body(**_GOLDEN_KW)
    assert (
        "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 · "
        "turns@cp=2 · coverage=full · channel=hop"
    ) in body
    assert "Mission: resume grok house h from checkpoint 3" in body
    assert extract_authored_resume_footer(body) == CANONICAL_RESUME_FOOTER
    assert "Window: agent_id=" not in body


def test_compose_mission_caller_clamp_and_prefix() -> None:
    long_mission = "M" * 250
    body = compose_checkpoint_body(
        **_GOLDEN_KW,
        mission=f"  line1\nline2  {long_mission}",
    )
    mission_line = next(ln for ln in body.splitlines() if ln.startswith("Mission:"))
    assert len(mission_line) <= len("Mission: ") + 200
    assert "\n" not in mission_line

    prefixed = compose_checkpoint_body(**_GOLDEN_KW, mission="Mission: already set")
    assert prefixed.count("Mission: Mission:") == 0
    assert "Mission: already set" in prefixed
    assert prefixed.count("Mission:") == 1


@pytest.mark.asyncio
async def test_body_and_duplicate() -> None:
    sent: list[dict] = []

    async def get(path: str, params=None):
        if params is not None:
            return {"turn_number": 0}, 200
        return {"turns": []}, 200

    async def send(**kwargs):
        sent.append(kwargs)
        return {"turn": {"turn_number": 4}}, 201

    out = await run_post(
        {
            "house": "h",
            "n": 3,
            "root_thread": "9",
            "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
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
            "surface": "grok",
            "provenance": "raw",
            "producer": "cursor-jsonl-extractor",
        },
        get=get,
        send=send,
    )
    body = out["body"]
    assert "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890" in body
    assert "messages_sha256:abc123" in body
    assert "provenance:raw" in body
    assert "producer:cursor-jsonl-extractor" in body
    assert "capture:none" in body
    assert extract_authored_resume_footer(body) == CANONICAL_RESUME_FOOTER
    assert sent[0]["after_turn"] == 0

    async def get_dup(path: str, params=None):
        if params is not None:
            return {"turn_number": 1}, 200
        return {"turns": [{"body": body, "subject": "CHECKPOINT"}]}, 200

    again = await run_post(
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
        get=get_dup,
        send=send,
    )
    assert again["duplicate"] is True
    assert len(sent) == 1
