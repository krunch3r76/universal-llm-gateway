"""Non-selecting Glass tab inspect (T3–T9)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cursor_bridge.glass_tab_inspect import inspect_glass_tab
from interlocutor.cursor_tab_view import map_run_state_phase

pytestmark = pytest.mark.offline

_PAGES = [{"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}]


def _chat_surface(
    *,
    active: str,
    active_id: str,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    agent_ids = {active_id: active}
    if extra:
        agent_ids.update(extra)
    titles = list({v for v in agent_ids.values()})
    return {
        "state": "chat_list",
        "active_title": active,
        "active_agent_id": active_id,
        "titles": titles,
        "agent_ids": agent_ids,
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
        "message_turns": [],
        "empty_basis": "empty_conversation",
        "has_active_chat": True,
    }


@pytest.mark.asyncio
async def test_inspect_background_tab_does_not_select() -> None:
    select_mock = AsyncMock()
    surface = _chat_surface(
        active="fg",
        active_id="fg-id",
        extra={"bg-id": "bg title"},
    )
    with (
        patch("cursor_bridge.glass_tab_inspect.list_pages", return_value=_PAGES),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_surface",
            new_callable=AsyncMock,
            return_value=surface,
        ),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_agent_run_state",
            new_callable=AsyncMock,
            return_value={"ok": True, "running": False, "status": "idle"},
        ),
        patch("cursor_bridge.chat_surface.select_sidebar_target", select_mock),
    ):
        out = await inspect_glass_tab(display="8", agent_id="bg-id")
    assert select_mock.await_count == 0
    assert out["ok"] is True
    assert out["target_foreground"] is False
    assert out["target_title"] == "bg title"


@pytest.mark.asyncio
async def test_inspect_foreground_tab_returns_turns_and_does_not_select() -> None:
    select_mock = AsyncMock()
    surface = _chat_surface(active="fg tab", active_id="fg-id")
    surface["message_turns"] = [{"role": "user", "text": "hello"}]
    with (
        patch("cursor_bridge.glass_tab_inspect.list_pages", return_value=_PAGES),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_surface",
            new_callable=AsyncMock,
            return_value=surface,
        ),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_agent_run_state",
            new_callable=AsyncMock,
            return_value={"ok": True, "running": False, "status": "done"},
        ),
        patch("cursor_bridge.chat_surface.select_sidebar_target", select_mock),
    ):
        out = await inspect_glass_tab(display="8", agent_id="fg-id")
    assert select_mock.await_count == 0
    assert out["target_foreground"] is True
    assert out["surface"]["message_turns"][0]["text"] == "hello"


@pytest.mark.asyncio
async def test_inspect_absent_id_refuses_target_absent() -> None:
    surface = _chat_surface(active="only", active_id="only-id")
    with (
        patch("cursor_bridge.glass_tab_inspect.list_pages", return_value=_PAGES),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_surface",
            new_callable=AsyncMock,
            return_value=surface,
        ),
    ):
        out = await inspect_glass_tab(display="8", agent_id="missing-id")
    assert out["ok"] is False
    assert out["code"] == "target_absent"
    assert out.get("rows_capped") is False


@pytest.mark.asyncio
async def test_inspect_absent_id_with_full_sidebar_flags_cap() -> None:
    aids = {f"id-{i}": f"title-{i}" for i in range(40)}
    surface = {
        "state": "chat_list",
        "active_title": "title-0",
        "active_agent_id": "id-0",
        "titles": list(aids.values()),
        "agent_ids": aids,
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
        "message_turns": [],
        "has_active_chat": True,
    }
    with (
        patch("cursor_bridge.glass_tab_inspect.list_pages", return_value=_PAGES),
        patch(
            "cursor_bridge.glass_tab_inspect.probe_surface",
            new_callable=AsyncMock,
            return_value=surface,
        ),
    ):
        out = await inspect_glass_tab(display="8", agent_id="not-in-list")
    assert out["code"] == "target_absent"
    assert out["rows_capped"] is True


@pytest.mark.parametrize(
    ("run", "phase", "reason"),
    [
        ({"ok": True, "running": True, "status": "generating"}, "running", None),
        ({"ok": True, "running": False, "status": "idle"}, "idle", None),
        ({"ok": True, "running": False, "status": ""}, "unknown", "empty_status"),
        ({"ok": False, "reason": "probe_failed"}, "unknown", "run_state_probe_failed"),
    ],
)
def test_run_state_phase_mapping(
    run: dict[str, Any], phase: str, reason: str | None
) -> None:
    mapped = map_run_state_phase(run)
    assert mapped["phase"] == phase
    if reason:
        assert mapped.get("reason") == reason
    assert mapped["source"] == "glass.composerDataService"
    generating = phase == "running"
    assert generating == (mapped["phase"] == "running")


def test_run_state_phase_mapping_probe_raises() -> None:
    mapped = map_run_state_phase({"ok": False})
    assert mapped["phase"] == "unknown"
    assert mapped.get("reason") == "run_state_probe_failed"


@pytest.mark.asyncio
async def test_inspect_wrong_display_refuses_without_io() -> None:
    list_mock = patch("cursor_bridge.glass_tab_inspect.list_pages")
    with list_mock as mocked:
        out = await inspect_glass_tab(display="99", agent_id="x")
    mocked.assert_not_called()
    assert out["ok"] is False
    assert out["code"] == "unknown_target"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pages", "surface", "code"),
    [
        ([], None, "no_page"),
        (
            [
                {"title": "Cursor Agents", "web_socket_debugger_url": "ws://a"},
                {"title": "Cursor Agents", "web_socket_debugger_url": "ws://b"},
            ],
            None,
            "no_page",
        ),
        (
            _PAGES,
            {"state": "signed_out", "agent_ids": {}},
            "signed_out",
        ),
        (_PAGES, {"state": "unknown", "agent_ids": {}}, "unknown"),
        (None, None, "debug_unreachable"),
        (_PAGES, None, "surface_probe_failed"),
    ],
)
async def test_inspect_page_and_surface_refusals(
    pages: list[dict[str, str]] | None,
    surface: dict[str, Any] | None,
    code: str,
) -> None:
    if code == "debug_unreachable":
        with patch(
            "cursor_bridge.glass_tab_inspect.list_pages",
            side_effect=TimeoutError("down"),
        ):
            out = await inspect_glass_tab(display="8", agent_id="a")
        assert out["code"] == "debug_unreachable"
        return
    if code == "surface_probe_failed":
        with (
            patch("cursor_bridge.glass_tab_inspect.list_pages", return_value=_PAGES),
            patch(
                "cursor_bridge.glass_tab_inspect.probe_surface",
                new_callable=AsyncMock,
                side_effect=RuntimeError("bad probe"),
            ),
        ):
            out = await inspect_glass_tab(display="8", agent_id="a")
        assert out["code"] == "surface_probe_failed"
        return
    probe = AsyncMock(return_value=surface) if surface else AsyncMock()
    with (
        patch("cursor_bridge.glass_tab_inspect.list_pages", return_value=pages or []),
        patch("cursor_bridge.glass_tab_inspect.probe_surface", probe),
    ):
        out = await inspect_glass_tab(display="8", agent_id="a")
    assert out["code"] == code