"""Tests for the narrow ``cursor_request`` MCP tool registration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract_vocab import CANONICAL_CONTRACTS, RECORDS
from request_profile import bind_request

from tools.cursor_request import (
    CALLER_FIELDS,
    _dispatch_cursor_request,
    register_cursor_request_tool,
)


class _ToolNameRecorder:
    """Minimal FastMCP duck-type that records tool registration calls."""

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


def test_cursor_request_registers_without_error() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)  # type: ignore[arg-type]
    assert recorder.registered == ["cursor_request"]
    description = recorder.kwargs["cursor_request"].get("description") or ""
    assert description
    for record in RECORDS:
        assert record.name in description
        assert record.closeout_shape in description
    for name in CANONICAL_CONTRACTS:
        assert name in description


def test_valid_call_delegates_to_request_dispatch_with_to_cursor() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]

    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "thread": {"id": "900"},
            "turn": {"turn_number": 1},
            "handler_status": "auto-admit-armed",
            "poll_hint": {"thread": "900", "after_turn": 1},
        }

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            result = cursor_request_fn(
                new_slug="arm-auto",
                subject="Implement X",
                body="TYPE: DIRECTIVE\ncontract: implement\n",
                contract="implement",
                from_agent="web-anthropic",
            )

    assert result["handler_status"] == "auto-admit-armed"
    assert len(captured) == 1
    assert captured[0]["to"] == "cursor"
    assert captured[0]["new_slug"] == "arm-auto"
    assert captured[0]["subject"] == "Implement X"
    assert captured[0]["contract"] == "implement"
    assert captured[0]["from_agent"] == "web-anthropic"
    assert "lane" not in captured[0]


def test_cursor_request_forwards_checkout_lane() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]

    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "thread": {"id": "900"},
            "turn": {"turn_number": 1},
            "handler_status": "auto-admit-armed",
        }

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            cursor_request_fn(
                new_slug="arm-auto",
                subject="Implement X",
                body="TYPE: DIRECTIVE\ncontract: implement\n",
                contract="implement",
                from_agent="web-anthropic",
                lane="A",
            )

    assert captured[0]["lane"] == "A"


def test_unknown_caller_argument_rejected_with_accepted_set_error() -> None:
    result = _dispatch_cursor_request(
        {
            "new_slug": "arm-auto",
            "subject": "X",
            "body": "Y",
            "to": "cursor",
        }
    )
    assert "error" in result
    assert "unsupported argument(s): to" in result["error"]
    assert "Accepted:" in result["error"]
    for field in sorted(CALLER_FIELDS):
        assert field in result["error"]


def test_from_omitted_reaches_dispatch_autofilled_on_life_surface() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]

    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "thread": {"id": "901"},
            "turn": {},
            "handler_status": "no-auto-handler",
        }

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            cursor_request_fn(
                new_slug="auto-fill",
                subject="Probe",
                body="body",
            )

    assert captured[0]["from_agent"] == "web-anthropic"


def test_surface_registration_includes_cursor_request_call() -> None:
    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("surface_registration.py")
        .read_text(encoding="utf-8")
    )
    assert "register_cursor_request_tool" in source
    assert "register_cursor_request_tool(mcp)" in source


@pytest.mark.parametrize("surface", ["life", "code"])
def test_cursor_request_present_on_surface_tool_list(surface: str) -> None:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, _ = _build_server(surface)  # type: ignore[arg-type]
    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "cursor_request" in tool_names
    assert "cursor_request" in derive_surface_primary_tools(surface)  # type: ignore[arg-type]
    cursor_request = next(tool for tool in tools if tool.name == "cursor_request")
    description = cursor_request.description or ""
    assert description, "schema compaction dropped cursor_request description"
    for record in RECORDS:
        assert record.name in description, record.name
        assert record.closeout_shape in description, record.closeout_shape
