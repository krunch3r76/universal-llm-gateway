"""Edge ``cursor_glass`` handlers with guarded CDP (fake pages)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from grok_window_edge.cdp import CdpPortRefusedError, assert_cdp_origin_allowed
from grok_window_edge.cursor_glass import cursor_glass

pytestmark = pytest.mark.offline

_ALLOWLIST_WITH_CURSOR = frozenset({9338, 9339, 9340})


@pytest.mark.asyncio
async def test_wake_tab_pastes_with_guarded_cdp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "grok_window_edge.cdp.CDP_PORT_ALLOWLIST",
        _ALLOWLIST_WITH_CURSOR,
    )
    with (
        patch(
            "grok_window_edge.cursor_glass.unknown_gui_host_error",
            return_value={"ok": True, "gui_host": ":8"},
        ),
        patch(
            "grok_window_edge.cursor_glass.normalize_target",
            return_value={
                "ok": True,
                "origin": "http://127.0.0.1:9338",
                "display": "8",
                "app": "cursor",
            },
        ),
        patch(
            "grok_window_edge.cursor_glass.wake_tab_cdp",
            return_value={"ok": True, "reason": "delivered", "op": "wake_tab"},
        ) as core,
    ):
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "agent-1", "body": "wake line"},
        )
    core.assert_called_once()
    assert out["ok"] is True
    assert out["op"] == "wake_tab"


@pytest.mark.asyncio
async def test_wake_tab_requires_body() -> None:
    out = await cursor_glass("wake_tab", {"gui_host": ":8", "agent_id": "a"})
    assert out["ok"] is False
    assert out["error"] == "body_required"


@pytest.mark.asyncio
async def test_rename_tab_calls_cdp_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grok_window_edge.cdp.CDP_PORT_ALLOWLIST",
        _ALLOWLIST_WITH_CURSOR,
    )
    with (
        patch(
            "grok_window_edge.cursor_glass.unknown_gui_host_error",
            return_value={"ok": True, "gui_host": ":8"},
        ),
        patch(
            "grok_window_edge.cursor_glass.cdp_origin_for_gui_host",
            return_value="http://127.0.0.1:9338",
        ),
        patch(
            "grok_window_edge.cursor_glass.rename_tab_cdp",
            return_value={"ok": True, "new_title": "t", "op": "rename_tab"},
        ) as core,
    ):
        out = await cursor_glass(
            "rename_tab",
            {"gui_host": ":8", "agent_id": "aid", "title": "new title"},
        )
    core.assert_called_once()
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_wake_tab_refuses_cdp_9338_before_dial() -> None:
    with (
        patch(
            "grok_window_edge.cursor_glass.unknown_gui_host_error",
            return_value={"ok": True, "gui_host": ":8"},
        ),
        patch(
            "grok_window_edge.cursor_glass.normalize_target",
            return_value={
                "ok": True,
                "origin": "http://127.0.0.1:9338",
                "display": "8",
                "app": "cursor",
            },
        ),
        patch("grok_window_edge.cursor_glass.wake_tab_cdp") as core,
    ):
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "a", "body": "line"},
        )
    core.assert_not_called()
    assert out["ok"] is False
    assert out["code"] == "cdp_port_refused"
    assert out["op"] == "wake_tab"


def test_cdp_9341_still_refused_on_origin() -> None:
    with pytest.raises(CdpPortRefusedError):
        assert_cdp_origin_allowed("http://127.0.0.1:9341")


def test_cdp_9338_refused_on_origin() -> None:
    with pytest.raises(CdpPortRefusedError):
        assert_cdp_origin_allowed("http://127.0.0.1:9338")
