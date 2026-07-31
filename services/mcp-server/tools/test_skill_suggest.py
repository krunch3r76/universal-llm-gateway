"""MCP skill_suggest tombstone tests — hidden + rejecting."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

MCP_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

_DEPRECATION_SNIPPET = "skill_suggest is deprecated indefinitely"


def _skill_suggest_callable():
    """Return the overflow skill_suggest handler (hidden from both ``tools/list``)."""
    from server import _build_server

    _, overflow_metadata, overflow_registry = _build_server("code")
    assert "skill_suggest" not in overflow_metadata
    return SimpleNamespace(fn=overflow_registry["skill_suggest"])


def test_skill_suggest_absent_from_tools_list_and_overflow_metadata() -> None:
    from server import _build_server

    for surface in ("life", "code"):
        mcp, overflow_metadata, overflow_registry = _build_server(surface)
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert "skill_suggest" not in names
        assert "skill_suggest" not in overflow_metadata
        assert "skill_suggest" in overflow_registry


def test_dispatch_skill_suggest_returns_deprecation_error() -> None:
    tool_fn = _skill_suggest_callable()
    result = tool_fn.fn(loaded=[], agent="claude-web")
    assert "error" in result
    assert _DEPRECATION_SNIPPET in result["error"]


def test_dispatch_skill_suggest_ignores_all_args() -> None:
    tool_fn = _skill_suggest_callable()
    result = tool_fn.fn(
        loaded='["consult-routing"]',
        conversation_context="anything",
        limit=5,
        agent="claude-cursor",
        entity_ids=["todo:x"],
        prefer_worker=True,
    )
    assert result == {
        "error": (
            "skill_suggest is deprecated indefinitely — do not call it. "
            "Discover skills via native boot index / <available_skills> stubs / "
            "description-gated rules only."
        )
    }
