"""MCP skill_suggest relay tests (spec §8 tests 24–25)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

MCP_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

from tools.skill_suggest import _resolve_effective_agent


def test_resolve_agent_override() -> None:
    assert _resolve_effective_agent("claude-web") == "claude-web"


def test_resolve_cursor_profile() -> None:
    with patch(
        "tools.skill_suggest.current_request_metadata",
        return_value={"request_profile": "cursor_safe", "seat_class": "claude"},
    ):
        assert _resolve_effective_agent(None) == "claude-cursor"


def test_resolve_unresolved_without_override() -> None:
    with patch("tools.skill_suggest.current_request_metadata", return_value={}):
        assert _resolve_effective_agent(None) is None


def test_relay_calls_endpoint_with_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    captured: dict = {}

    def _fake_cx(method: str, path: str, body: dict | None = None, **kwargs) -> dict:
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        captured["headers"] = kwargs.get("headers")
        return {"suggestions": [], "count": 0, "ranker_status": "disabled"}

    monkeypatch.setattr("tools.skill_suggest.cx", _fake_cx)
    from server import _build_server

    mcp, _, _ = _build_server()
    tools = asyncio.run(mcp.list_tools())
    tool_fn = next(t for t in tools if t.name == "skill_suggest")
    with patch(
        "tools.skill_suggest.current_request_metadata",
        return_value={"request_profile": "cursor_safe"},
    ):
        result = tool_fn.fn(loaded=["fs"], conversation_context="consult handoff")
    assert result["count"] == 0
    assert captured["path"] == "/skills/suggest"
    assert captured["body"]["agent"] == "claude-cursor"
    assert captured["headers"] == {"X-Cortex-Transport": "mcp"}


def test_relay_errors_without_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    called = {"n": 0}

    def _fake_cx(*_args, **_kwargs) -> dict:
        called["n"] += 1
        return {}

    monkeypatch.setattr("tools.skill_suggest.cx", _fake_cx)
    from server import _build_server

    mcp, _, _ = _build_server()
    tools = asyncio.run(mcp.list_tools())
    tool_fn = next(t for t in tools if t.name == "skill_suggest")
    with patch("tools.skill_suggest.current_request_metadata", return_value={}):
        result = tool_fn.fn(loaded=[])
    assert "error" in result
    assert called["n"] == 0
