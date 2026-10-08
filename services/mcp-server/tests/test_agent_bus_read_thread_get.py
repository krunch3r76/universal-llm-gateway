"""Tests for agent_bus_read thread_get relay (+ cursor_auto_job enrich)."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.threads import _thread_get_dispatch, _thread_get_impl


def test_thread_get_labels_last_associated_only_when_url_present() -> None:
    with_url = {
        "id": "12286",
        "cse_chat_url": "https://claude.ai/cowork/cse_old",
        "status": "active",
    }
    without = {"id": "12286", "cse_chat_url": None, "status": "active"}
    with patch("tools.agent_bus.threads.relay", return_value=with_url):
        labeled = _thread_get_impl(thread="12286")
    assert labeled["cse_chat_url_basis"] == "last_associated"
    assert labeled["cse_current_probe"] == (
        "cse_session(op=resolve_attended, parent_thread=12286)"
    )
    with patch("tools.agent_bus.threads.relay", return_value=dict(without)):
        plain = _thread_get_impl(thread="12286")
    assert "cse_chat_url_basis" not in plain
    assert "cse_current_probe" not in plain


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

    with patch("tools.agent_bus.threads.relay", return_value=detail) as relay:
        result = _thread_get_impl(thread="049")

    assert result == detail
    relay.assert_called_once()
    assert "include_resume=false" in relay.call_args[0][2]


def test_thread_get_forwards_to_query() -> None:
    detail = {
        "id": "049",
        "slug": "root-arc",
        "status": "active",
        "unread_count": 2,
        "unread_basis": {
            "basis": "read_at_null",
            "recipient": "web",
            "includes_superseded": False,
            "as_of": "2026-10-06T00:00:00Z",
            "source": "agent_bus_store.threads",
        },
    }
    with patch("tools.agent_bus.threads.relay", return_value=detail) as relay:
        result = _thread_get_impl(thread="049", to="web")
    assert "to=web" in relay.call_args[0][2]
    assert result["unread_basis"]["recipient"] == "web"


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


def test_thread_get_include_resume_true_relays_flag_and_envelope() -> None:
    detail = {
        "id": "049",
        "slug": "root-arc",
        "status": "active",
        "turn_count": 12,
        "unread_count": 0,
        "tags": ["role:root"],
        "resume_envelope": {"tape_verbal": [], "checkpoint_highlight": "hi"},
    }

    with patch("tools.agent_bus.threads.relay", return_value=detail) as relay:
        result = _thread_get_dispatch(thread=49, include_resume=True)

    assert relay.call_args[0][2] == "/threads/49?include_resume=true"
    assert result["resume_envelope"] == {
        "tape_verbal": [],
        "checkpoint_highlight": "hi",
    }
