"""Friction a:37147 — sync agent_bus handlers must not block the mcp event loop.

A blocking ``wait`` long-poll ran inline inside ``async def agent_bus``, so every
other MCP request stalled for the whole wait and Cloudflare returned 502.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
import tools.agent_bus as agent_bus_pkg
from fastmcp import FastMCP
from tools.agent_bus import register_agent_bus_tools


@pytest.fixture
def agent_bus_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_bus_pkg, "record", lambda *a, **k: None)
    mcp = FastMCP("test-agent-bus-off-loop")
    register_agent_bus_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus").fn


def test_blocking_sync_handler_lets_other_coroutines_run(
    agent_bus_fn, monkeypatch: pytest.MonkeyPatch
) -> None:
    released = threading.Event()

    def _blocking_wait(*, thread: str = "") -> dict[str, Any]:
        return {"thread": thread, "released": released.wait(timeout=2.0)}

    monkeypatch.setitem(agent_bus_pkg.AGENT_BUS_OPS, "wait", _blocking_wait)

    async def _concurrent_request() -> None:
        await asyncio.sleep(0.05)
        released.set()

    async def _run() -> dict[str, Any]:
        result, _ = await asyncio.gather(
            agent_bus_fn(tool="wait", arguments='{"thread": "1"}'),
            _concurrent_request(),
        )
        return result

    assert asyncio.run(_run()) == {"thread": "1", "released": True}


def test_async_handler_still_awaited_on_loop(
    agent_bus_fn, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread: list[int] = []

    async def _async_handler(*, thread: str = "") -> dict[str, Any]:
        return {"thread": thread, "ident": threading.get_ident()}

    async def _run() -> dict[str, Any]:
        loop_thread.append(threading.get_ident())
        return await agent_bus_fn(tool="thread_get", arguments='{"thread": "2"}')

    monkeypatch.setitem(agent_bus_pkg.AGENT_BUS_OPS, "thread_get", _async_handler)

    assert asyncio.run(_run()) == {"thread": "2", "ident": loop_thread[0]}
