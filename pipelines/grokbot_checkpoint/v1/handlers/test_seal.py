"""Seal refuses when the conversation has no start."""

from __future__ import annotations

import pytest

from . import seal as seal_mod

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_missing_start_refuses_without_close() -> None:
    calls: list[str] = []

    async def fake_dispatch(tool: str, args: dict) -> dict:
        calls.append(tool)
        return {}

    out = await seal_mod.run_seal(
        {"bus_identity": "grok-bot-hm1", "agent_id": "agent", "house": "hm1"},
        {
            "snapshot_uri": "ephemeral/harvests/grok/hm1-agent.raw.messages.json",
            "envelope": {"messages": [{"role": "user", "content": "hi"}], "meta": {}},
            "messages_sha256": "abc",
        },
        execution_id="exec",
        dispatch=fake_dispatch,
    )
    assert out["refused"]["code"] == "seal.missing_start"
    assert calls == []
