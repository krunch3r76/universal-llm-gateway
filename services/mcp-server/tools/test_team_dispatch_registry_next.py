"""team_dispatch generate attaches work-registry _next when row id is present."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tools.frontier import register_frontier_tools


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorator


@pytest.mark.asyncio
async def test_generate_adds_registry_next_from_dispatch_id() -> None:
    recorder = _Recorder()
    register_frontier_tools(recorder)  # type: ignore[arg-type]
    fn = recorder.functions["team_dispatch"]
    relay = AsyncMock(
        return_value={"dispatch_id": "disp-mcp-1", "status": "admitted"}
    )
    with patch("tools.frontier._relay", relay):
        result = await fn(
            op="generate",
            seat="cursor-sdk",
            contract="freeform",
            lane="B",
            dispatch_thread_id="21650",
            prompt="test",
            model="cursor/grok-4.7",
            caller_agent="cursor-sdk",
        )
    assert result.get("_next")
    assert "disp-mcp-1" in result["_next"]
    assert "team_dispatch(op=registry" in result["_next"]
