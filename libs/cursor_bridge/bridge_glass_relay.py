"""Glass CDP relays for cursor_bridge wake/rename/prune ops."""

from __future__ import annotations

from typing import Any

from spawn_ledger.spawn_endpoint_house_ledger import (
    last_successful_wake,
    record_tab_wake,
)

from cursor_bridge.cursor_agents_archive import prune_cursor_agents_predecessors
from cursor_bridge.cursor_agents_hop import cursor_debug_url_for_gui_host
from cursor_bridge.glass_cdp_core import rename_tab_cdp, wake_tab_cdp
from cursor_bridge.tab_ready_rehandshake import (
    post_tab_gone_for_lane,
    tab_ready_instruction,
)


def finish_wake_tab(res: dict[str, Any]) -> dict[str, Any]:
    """Ledger and wake history for a CDP core result (MCP / legacy path)."""
    out = dict(res)
    paste_attempted = bool(out.pop("paste_attempted", False))
    gui_host = str(out.get("gui_host") or "")
    aid = str(out.get("agent_id") or "").strip()
    if paste_attempted:
        if out.get("error") == "paste_failed":
            record_tab_wake(
                gui_host=gui_host,
                agent_id=aid,
                op="wake_tab",
                ok=False,
                reason="paste_failed",
                detail={"detail": out.get("detail", "")},
            )
        else:
            record_tab_wake(
                gui_host=gui_host,
                agent_id=aid,
                op="wake_tab",
                ok=bool(out.get("ok")),
                reason=str(out.get("reason") or ""),
            )
    prior = last_successful_wake(aid)
    if prior:
        out["last_successful_wake_at"] = prior.get("at")
    return out


def finish_rename_tab(
    res: dict[str, Any], *, thread: str | None = None
) -> dict[str, Any]:
    """Bus TAB_GONE and wake history for a CDP core result (MCP / legacy path)."""
    out = dict(res)
    out.pop("paste_attempted", None)
    if out.get("ok"):
        title = str(out.get("new_title") or out.get("title") or "")
        if title:
            out["tab_ready_instruction"] = tab_ready_instruction(title)
        tid = str(thread or "").strip()
        if tid:
            gone = post_tab_gone_for_lane(tid, reason="bridge_rename")
            out["tab_gone"] = gone
    agent_id = str(out.get("agent_id") or "")
    prior = last_successful_wake(agent_id)
    if prior:
        out["last_successful_wake_at"] = prior.get("at")
    return out


def relay_wake_tab(
    *,
    gui_host: str,
    agent_id: str | None,
    body: str,
    thread: str | None = None,
) -> dict[str, Any]:
    """Paste a wake line into a Glass Cursor Agents tab."""
    core = wake_tab_cdp(
        gui_host=gui_host,
        agent_id=agent_id,
        body=body,
        thread=thread,
    )
    return finish_wake_tab(core)


def relay_rename_tab(
    *,
    gui_host: str,
    agent_id: str,
    title: str,
    thread: str | None = None,
) -> dict[str, Any]:
    core = rename_tab_cdp(gui_host=gui_host, agent_id=agent_id, title=title)
    return finish_rename_tab(core, thread=thread)


def relay_prune_sidebar(*, gui_host: str, thread: str, agent_id: str) -> dict[str, Any]:
    debug_url = cursor_debug_url_for_gui_host(gui_host)
    res = prune_cursor_agents_predecessors(
        thread,
        active_agent_id=agent_id,
        debug_url=debug_url,
    )
    return {**res, "op": "prune_sidebar", "gui_host": gui_host}


__all__ = [
    "finish_rename_tab",
    "finish_wake_tab",
    "relay_prune_sidebar",
    "relay_rename_tab",
    "relay_wake_tab",
]
