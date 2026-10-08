"""Tombstone tests: operator_request refuses; cursor_request is unregistered."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.cursor_request import register_operator_request_tool


class _ToolNameRecorder:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.functions: dict[str, Any] = {}
        self.kwargs: dict[str, dict[str, Any]] = {}

    def tool(self, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.registered.append(fn.__name__)
            self.functions[fn.__name__] = fn
            self.kwargs[fn.__name__] = kwargs
            return fn

        return decorator


def test_operator_request_is_retired_tombstone() -> None:
    recorder = _ToolNameRecorder()
    register_operator_request_tool(recorder)  # type: ignore[arg-type]
    assert recorder.registered == ["operator_request"]
    description = recorder.kwargs["operator_request"].get("description") or ""
    assert "RETIRED (a:38728)" in description
    assert "team_dispatch on ulg-code" in description
    assert "life_dispatch" in description
    assert "agent_bus send" in description
    with patch("tools.agent_bus.request._send_dispatch") as send_mock:
        result = recorder.functions["operator_request"](subject="s", body="b")
    assert result["reason"] == "cursor_auto_retired"
    assert "a:38728" in result["error"]
    send_mock.assert_not_called()


def test_surface_registration_registers_operator_request_on_life_only() -> None:
    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("surface_registration.py")
        .read_text(encoding="utf-8")
    )
    assert "register_operator_request_tool" in source
    life_block = source.split('if surface == "life":', 1)[1]
    assert "register_operator_request_tool(mcp)" in life_block.split(
        "register_fleet_liveness_tools", 1
    )[0]


def test_cursor_request_absent_on_life_surface_tool_list() -> None:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, overflow_reg = _build_server("life")
    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "cursor_request" not in tool_names
    assert "cursor_request" not in derive_surface_primary_tools("life")
    assert "cursor_request" not in overflow_reg
    assert "operator_request" in tool_names
    assert "operator_request" in derive_surface_primary_tools("life")
    assert "dispatch" not in tool_names
    assert "tool_search" not in tool_names

    async def _call_pruned_name() -> None:
        await mcp.call_tool(
            "cursor_request",
            {"subject": "s", "body": "b", "new_slug": "slug-test"},
        )

    with pytest.raises(Exception, match="Unknown tool"):
        asyncio.run(_call_pruned_name())


def test_operator_request_call_tool_returns_retirement() -> None:
    from server import _build_server

    mcp, _, _ = _build_server("life")

    async def _call() -> dict:
        return await mcp.call_tool(
            "operator_request",
            {"subject": "s", "body": "b", "new_slug": "x"},
        )

    result = asyncio.run(_call())
    payload = result.data if hasattr(result, "data") else result
    if isinstance(payload, list):
        payload = payload[0]
    text = str(payload)
    assert "cursor_auto_retired" in text


def test_cursor_request_absent_on_code_surface_tool_list() -> None:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, overflow_reg = _build_server("code")
    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "cursor_request" not in tool_names
    assert "cursor_request" not in overflow_reg
    assert "cursor_request" not in derive_surface_primary_tools("code")
    assert "operator_request" not in tool_names
    assert "operator_request" not in derive_surface_primary_tools("code")

    async def _call_pruned_name() -> None:
        await mcp.call_tool(
            "cursor_request",
            {"subject": "s", "body": "b", "new_slug": "slug-test"},
        )

    with pytest.raises(Exception, match="Unknown tool"):
        asyncio.run(_call_pruned_name())
