"""agent_bus_read exposes delivery_bindings_resolve (read-only wake actuation resolve)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module  # noqa: E402
from tools._agent_bus_read import AGENT_BUS_READ_OPS, register_agent_bus_read_tool  # noqa: E402


@pytest.fixture
def agent_bus_read_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_bus_module, "record", lambda *a, **k: None)
    mcp = FastMCP("test-agent-bus-read-delivery-bindings")
    register_agent_bus_read_tool(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus_read").fn


def test_delivery_bindings_resolve_registered_on_read_table() -> None:
    assert "delivery_bindings_resolve" in AGENT_BUS_READ_OPS


def test_agent_bus_read_delivery_bindings_resolve_relay(agent_bus_read_fn) -> None:
    """Breaks when op is missing from AGENT_BUS_READ_OPS or relay path/query is wrong."""
    resolve_payload = {
        "state": "bound",
        "as_of": "2026-10-27T00:00:00Z",
        "basis": "endpoint_subscription",
        "source": "agent_bus_store.delivery_bindings_resolve",
        "scope": "house:20995/intent:wake",
        "current": {"actuation": {"type": "cursor_glass"}},
        "stale": False,
    }
    with patch(
        "tools.agent_bus.subscriptions.relay", return_value=resolve_payload
    ) as relay:
        result = agent_bus_read_fn(
            tool="delivery_bindings_resolve",
            arguments=json.dumps({"house_id": "20995", "intent": "wake"}),
        )
    assert result == resolve_payload
    relay.assert_called_once()
    service, method, path = relay.call_args[0]
    assert service == "agent-bus"
    assert method == "GET"
    assert path.startswith("/delivery-bindings/resolve?")
    assert "house_id=20995" in path
    assert "intent=wake" in path
