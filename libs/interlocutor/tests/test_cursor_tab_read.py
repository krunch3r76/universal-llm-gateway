"""Cursor display read via facade — non-selecting inspect path (T10–T15)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interlocutor import display_adapter
from interlocutor.facade import execute_read
from interlocutor.targets import DisplayEightTarget, resolve_target

pytestmark = pytest.mark.offline

_BG_ID = "507a3f34-a368-4d9d-85c2-a037e35a50b5"
_FG_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CURSOR_TARGET = f"display:8/{_BG_ID}/cursor"
_FG_VISIBLE = "FOREGROUND-TAB-MARKER-XYZZY"
_DRAFT_SECRET = "DRAFT-SECRET-NEVER-SERIALIZE"
_BG_TITLE = "bg tab title lane"
_CAP = 2000


@asynccontextmanager
async def _noop_lock(*_a: Any, **_k: Any):
    yield


def _surface_background_tab() -> dict[str, Any]:
    return {
        "state": "chat_list",
        "active_title": "foreground tab",
        "active_agent_id": _FG_ID,
        "titles": ["foreground tab", _BG_TITLE],
        "agent_ids": {_FG_ID: "foreground tab", _BG_ID: _BG_TITLE},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [_FG_VISIBLE],
        "message_turns": [{"role": "assistant", "text": _FG_VISIBLE}],
        "empty_basis": "messages_present",
        "has_active_chat": True,
    }


def _surface_foreground(*, draft: str = "", generating: bool = False) -> dict[str, Any]:
    long_tail = "Z" * (_CAP + 50)
    return {
        "state": "chat_list",
        "active_title": "fg",
        "active_agent_id": _BG_ID,
        "titles": ["fg"],
        "agent_ids": {_BG_ID: "fg"},
        "composer_draft": draft,
        "generating": generating,
        "recent_messages": ["short", long_tail],
        "message_turns": [
            {"role": "user", "text": "u"},
            {"role": "assistant", "text": long_tail},
        ],
        "empty_basis": "messages_present",
        "has_active_chat": True,
        "latest_fingerprint": "fp1",
    }


def test_cursor_read_background_tab_typed_unavailable() -> None:
    run_state = AsyncMock(return_value={"ok": True, "running": False, "status": "idle"})
    select_mock = AsyncMock()
    relay_mock = AsyncMock()
    with (
        patch(
            "cursor_bridge.glass_tab_inspect.list_pages",
            return_value=[{"title": "Cursor Agents", "web_socket_debugger_url": "ws://f"}],
        ),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_surface",
            new_callable=AsyncMock,
            return_value=_surface_background_tab(),
        ),
        patch("cursor_bridge.glass_tab_inspect.probe_agent_run_state", run_state),
        patch("cursor_bridge.chat_surface.select_sidebar_target", select_mock),
        patch("interlocutor.display_adapter.read_endpoint_turn", relay_mock),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
        patch("cursor_bridge.electron_chat_relay.display_switch_lock", _noop_lock),
    ):
        out = execute_read(target=_CURSOR_TARGET, coverage="latest")
    assert relay_mock.await_count == 0
    assert select_mock.await_count == 0
    assert out["ok"] is True
    assert out.get("selected") is False
    assert out.get("target_foreground") is False
    assert out["target_title"] == _BG_TITLE
    assert out["empty_basis"] == "tab_not_foreground"
    assert out["message_turns"] == []
    assert out.get("mailbox_basis") == "not_resolved"
    assert _FG_VISIBLE not in str(out)


def test_cursor_read_foreground_tab_bounded_tail() -> None:
    run_state = AsyncMock(return_value={"ok": True, "running": True, "status": "generating"})
    select_mock = AsyncMock()
    relay_mock = AsyncMock()
    with (
        patch(
            "cursor_bridge.glass_tab_inspect.list_pages",
            return_value=[{"title": "Cursor Agents", "web_socket_debugger_url": "ws://f"}],
        ),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_surface",
            new_callable=AsyncMock,
            return_value=_surface_foreground(draft=_DRAFT_SECRET),
        ),
        patch("cursor_bridge.glass_tab_inspect.probe_agent_run_state", run_state),
        patch("cursor_bridge.chat_surface.select_sidebar_target", select_mock),
        patch("interlocutor.display_adapter.read_endpoint_turn", relay_mock),
    ):
        out = execute_read(target=_CURSOR_TARGET, coverage="latest")
    assert relay_mock.await_count == 0
    assert select_mock.await_count == 0
    assert len(out["message_turns"]) <= 3
    last = out["message_turns"][-1]
    assert last["truncated"] is True
    assert last["char_count"] == _CAP + 50
    assert len(last["text"]) == _CAP
    assert out["tail_includes_streaming"] is True
    assert out["composer_has_draft"] is True
    assert "composer_draft" not in out
    assert _DRAFT_SECRET not in str(out)


def test_cursor_read_refusal_unknown_display() -> None:
    out = execute_read(target="display:99/x/cursor", coverage="latest")
    assert out["ok"] is False
    assert out["code"] == "unknown_target"
    assert "message_turns" not in out


def test_cursor_read_refusal_no_page() -> None:
    with patch("cursor_bridge.glass_tab_inspect.list_pages", return_value=[]):
        out = execute_read(target=_CURSOR_TARGET, coverage="latest")
    assert out["code"] == "no_page"
    assert "message_turns" not in out


def test_cursor_read_refusal_target_absent() -> None:
    with (
        patch(
            "cursor_bridge.glass_tab_inspect.list_pages",
            return_value=[{"title": "Cursor Agents", "web_socket_debugger_url": "ws://f"}],
        ),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_surface",
            new_callable=AsyncMock,
            return_value=_surface_background_tab(),
        ),
    ):
        out = execute_read(target="display:8/missing-id/cursor", coverage="latest")
    assert out["code"] == "target_absent"
    assert "message_turns" not in out


def test_grok_default_on_cursor_display_names_cursor_form() -> None:
    relay = AsyncMock(
        return_value={"ok": False, "code": "unknown_target", "reason": "unknown_target"}
    )
    with patch("interlocutor.display_adapter.read_endpoint_turn", relay):
        out = execute_read(target=f"display:8/{_BG_ID}", coverage="latest")
    assert out["ok"] is False
    assert out["code"] == "unknown_target"
    assert "display:8/" in out["reason"]
    assert "/cursor" in out["reason"]


def test_grok_display_read_unchanged() -> None:
    relay = AsyncMock(
        return_value={
            "ok": True,
            "latest_message": "grok hi",
            "generating": False,
            "empty_basis": "",
            "composer_draft": "draft stays",
            "latest_fingerprint": "",
            "active_agent_id": "g1",
            "active_title": "g",
            "recent_messages": ["grok hi"],
            "message_turns": [{"role": "assistant", "text": "grok hi"}],
            "endpoint": ":9:g1",
        }
    )
    with patch("interlocutor.display_adapter.read_endpoint_turn", relay) as mocked:
        out = execute_read(target="display:9/agent-grok/grok", coverage="latest")
    mocked.assert_awaited_once()
    select_kw = mocked.call_args.kwargs.get("select")
    select_pos = mocked.call_args.kwargs.get("select", mocked.call_args.args[1] if len(mocked.call_args.args) > 1 else None)
    assert (select_kw if select_kw is not None else select_pos) is True
    assert out.get("composer_draft") == "draft stays"
    assert out["latest_message"] == "grok hi"


def test_documented_cursor_target_form_resolves() -> None:
    resolved = resolve_target(f"display:8/{_BG_ID}/cursor")
    assert isinstance(resolved, DisplayEightTarget)
    assert resolved.app == "cursor"
    assert resolved.display == "8"
    assert resolved.agent_id == _BG_ID