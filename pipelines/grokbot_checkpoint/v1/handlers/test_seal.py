"""session_close arguments, already_closed, sealed event surface."""

from __future__ import annotations

import pytest

from . import seal as seal_mod

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_seal_args_and_already_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    events: list[dict] = []

    async def fake_dispatch(tool: str, args: dict) -> dict:
        calls.append({"tool": tool, "args": args})
        return {"session_id": "sess-1", "already_closed": True, "meta": {}}

    monkeypatch.setattr(
        seal_mod, "stargate_continuity_checkpoint_sealed", lambda **kw: events.append(kw)
    )
    out = await seal_mod.run_seal(
        {"bus_identity": "grok-bot", "root_thread": "9", "agent_id": "agent"},
        {
            "snapshot_uri": "notes/snap.json",
            "surface": "grok",
            "turn_count": 2,
            "coverage": "full",
            "messages_sha256": "abc",
            "provenance": "transcribed",
            "producer": "harvester-bot-fixture",
        },
        execution_id="exec",
        dispatch=fake_dispatch,
    )
    args = calls[0]["args"]
    assert args["transcript_messages_path"] == "notes/snap.json"
    assert args["closed_by"] == "succession"
    assert args["succession_seal_authority"] is True
    assert args["agent"] == "grok-bot"
    assert out["already_closed"] is True
    assert out["session_id"] == "sess-1"
    assert events[0]["surface"] == "grok"
    assert out["proposals"]
