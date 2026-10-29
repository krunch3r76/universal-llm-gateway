"""Handler: bad_turn without raising; batch turn passthrough."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipelines.orion_chat_wake.v1.handlers.wake import OrionChatWakeHandler

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_handler_bad_turn_structured() -> None:
    handler = OrionChatWakeHandler()
    ctx = MagicMock()
    ctx.options = {"thread": "21207", "turn": "not-a-turn", "from_agent": "maestro"}
    out = await handler.execute(MagicMock(), ctx)
    body = json.loads(out.raw)
    assert body["ok"] is False
    assert body["reason"] == "bad_turn"


@pytest.mark.asyncio
async def test_handler_batch_turn_does_not_int_cast() -> None:
    handler = OrionChatWakeHandler()
    ctx = MagicMock()
    ctx.options = {
        "thread": "21207",
        "turn": "69,70,71,72,73",
        "from_agent": "maestro",
        "turn_ids": [1, 2, 3, 4, 5],
    }
    with patch(
        "pipelines.orion_chat_wake.v1.handlers.wake.deliver_orion_chat_wake",
        new=AsyncMock(return_value={"ok": True, "delivered": True}),
    ) as deliver:
        await handler.execute(MagicMock(), ctx)
    assert deliver.await_args.kwargs["turn"] == "69,70,71,72,73"
