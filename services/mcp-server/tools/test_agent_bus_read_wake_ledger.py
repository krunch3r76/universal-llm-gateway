"""agent_bus_read exposes wake_ledger (read-only wake ledger query)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module  # noqa: E402
from tools._agent_bus_read import (  # noqa: E402
    AGENT_BUS_READ_OPS,
    register_agent_bus_read_tool,
)


@pytest.fixture
def agent_bus_read_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_bus_module, "record", lambda *a, **k: None)
    mcp = FastMCP("test-agent-bus-read-wake-ledger")
    register_agent_bus_read_tool(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus_read").fn


def test_wake_ledger_in_read_ops() -> None:
    assert "wake_ledger" in AGENT_BUS_READ_OPS


def test_agent_bus_read_wake_ledger_relay(agent_bus_read_fn) -> None:
    captured: list[tuple[str, str, str | None]] = []

    def fake_relay(service: str, method: str, path: str, **kwargs: object) -> dict:
        captured.append((service, method, path))
        return {"rows": [], "count": 0}

    with patch("tools.agent_bus.wake_ledger.relay", fake_relay):
        out = agent_bus_read_fn(
            tool="wake_ledger",
            arguments='{"thread": "12286", "limit": 10}',
        )
    assert out == {"rows": [], "count": 0}
    assert captured == [
        ("agent-bus", "GET", "/endpoint-wake/ledger?thread=12286&limit=10")
    ]
