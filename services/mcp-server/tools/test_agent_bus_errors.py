"""Structured relay error preservation for agent_bus MCP (friction 13695 P0-B)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module  # noqa: E402


def test_structured_relay_error_surfaces_unread_turns_exist() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [{"thread": "1414", "turn_number": 7}],
        },
    }
    envelope = agent_bus_module._structured_relay_error(relay_result, op="reply")
    assert envelope is not None
    assert envelope["status_code"] == 409
    assert envelope["reason"] == "unread_turns_exist"
    assert envelope["detail"]["unread_turns"][0]["turn_number"] == 7


def test_reply_impl_preserves_409_detail() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [{"thread": "1414", "turn_number": 7}],
        },
    }

    with patch.object(agent_bus_module, "_relay", return_value=relay_result):
        with patch.object(agent_bus_module, "record", lambda *_args, **_kwargs: None):
            result = agent_bus_module._reply_impl(
                thread="1414",
                to="claude-web",
                subject="blocked",
                body="attempt",
                after_turn=6,
                from_agent="claude-web",
                status="open",
                mark_read=False,
                close=False,
            )

    assert result["status_code"] == 409
    assert result["reason"] == "unread_turns_exist"
    assert result["detail"]["unread_turns"]


def test_post_impl_preserves_409_after_route_guard_miss() -> None:
    relay_result: dict[str, Any] = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [],
        },
    }

    with patch.object(agent_bus_module, "_relay", return_value=relay_result):
        with patch.object(agent_bus_module, "record", lambda *_args, **_kwargs: None):
            result = agent_bus_module._post_impl(
                slug="blocked-post",
                to="cursor",
                subject="blocked",
                body="attempt",
                from_agent="claude-web",
                summary=None,
            )

    assert result["status_code"] == 409
    assert result["reason"] == "unread_turns_exist"
