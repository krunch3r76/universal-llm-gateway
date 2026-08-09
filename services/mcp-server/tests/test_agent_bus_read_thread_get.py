"""Tests for agent_bus_read thread_get relay (+ cursor_auto_job enrich)."""

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

    with (
        patch("tools.agent_bus.threads.relay", return_value=detail),
        patch(
            "tools.agent_bus.request_worker_client.fetch_job_state",
            return_value={"ok": True, "found": False, "job": None},
        ),
    ):
        result = _thread_get_impl(thread="049")

    assert result == detail
    assert result["tags"] == ["role:root"]
    assert result["turn_count"] == 12
    assert "cursor_auto_job" not in result


def test_thread_get_includes_live_cursor_auto_job_phase() -> None:
    detail = {
        "id": "7052",
        "slug": "private-lane",
        "status": "active",
        "turn_count": 3,
        "unread_count": 1,
        "tags": [],
    }
    job = {
        "job_id": "j-1",
        "thread_id": "7052",
        "status": "claimed",
        "lifecycle_phase": "admitted",
        "admitted_at": "2026-08-09T17:00:00+00:00",
        "bound_at": None,
        "dispatch_id": None,
        "escalation": "cdp/fable",
        "contract": "answer",
    }

    with (
        patch("tools.agent_bus.threads.relay", return_value=detail),
        patch(
            "tools.agent_bus.request_worker_client.fetch_job_state",
            return_value={"ok": True, "found": True, "job": job},
        ) as probe,
    ):
        result = _thread_get_impl(thread="7052")

    assert result["cursor_auto_job"]["lifecycle_phase"] == "admitted"
    assert result["cursor_auto_job"]["dispatch_id"] is None
    assert result["id"] == "7052"
    probe.assert_called_once()
    assert probe.call_args.kwargs["thread_id"] == "7052"


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
