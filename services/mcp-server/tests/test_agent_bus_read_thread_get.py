"""Tests for agent_bus_read thread_get relay."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.threads import _thread_get_dispatch, _thread_get_impl


def test_thread_get_happy_path() -> None:
    detail = {
        "id": "049",
        "slug": "root-arc",
        "status": "active",
        "summary": "Standing root",
        "turn_count": 12,
        "unread_count": 0,
        "tags": ["role:root"],
    }

    with patch("tools.agent_bus.threads.relay", return_value=detail):
        result = _thread_get_impl(thread="049")

    assert result == detail
    assert result["tags"] == ["role:root"]
    assert result["turn_count"] == 12


def test_thread_get_missing_thread_structured_error() -> None:
    with patch(
        "tools.agent_bus.threads.relay",
        return_value={"error": "HTTP 404", "detail": "Thread 999 not found"},
    ):
        result = _thread_get_impl(thread="999")

    assert result["reason"] == "thread_not_found"
    assert "999" in result["error"]
    assert "threads" not in result


def test_thread_get_dispatch_requires_thread() -> None:
    result = _thread_get_dispatch(thread="")
    assert "error" in result
    assert "thread_get requires" in result["error"]
