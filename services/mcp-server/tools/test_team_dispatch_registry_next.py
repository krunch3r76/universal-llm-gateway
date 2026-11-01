"""team_dispatch generate attaches work-registry _next when row id is present."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from tools.frontier import _registry_next_hint, register_frontier_tools


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def _team_dispatch_fn():
    recorder = _Recorder()
    register_frontier_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["team_dispatch"]


@pytest.mark.asyncio
async def test_generate_adds_registry_next_from_dispatch_id() -> None:
    fn = _team_dispatch_fn()
    relay = AsyncMock(return_value={"dispatch_id": "disp-mcp-1", "status": "admitted"})
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
    assert "error" not in result
    expected = _registry_next_hint(row_id="disp-mcp-1")
    assert result.get("_next") == expected
    assert "team_dispatch(op=registry" in result["_next"]


@pytest.mark.asyncio
async def test_generate_succeeds_when_services_package_unimportable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP container has no services package; admit must not fail on _next."""
    monkeypatch.setitem(sys.modules, "services", None)
    fn = _team_dispatch_fn()
    relay = AsyncMock(return_value={"dispatch_id": "disp-no-svc", "status": "admitted"})
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
    assert "error" not in result
    assert result.get("dispatch_id") == "disp-no-svc"
    assert result.get("_next")


@pytest.mark.asyncio
async def test_generate_leaves_result_intact_when_hint_helper_raises() -> None:
    fn = _team_dispatch_fn()
    relay = AsyncMock(
        return_value={"dispatch_id": "disp-hint-fail", "status": "admitted"}
    )
    with (
        patch("tools.frontier._relay", relay),
        patch(
            "tools.frontier._registry_next_hint",
            side_effect=RuntimeError("hint broken"),
        ),
    ):
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
    assert "error" not in result
    assert result.get("dispatch_id") == "disp-hint-fail"
    assert "_next" not in result
