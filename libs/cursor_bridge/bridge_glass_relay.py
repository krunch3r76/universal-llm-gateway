"""Glass CDP relays for cursor_bridge wake/rename/prune ops."""

from __future__ import annotations

import asyncio
from typing import Any

from cursor_bridge.cursor_agents_archive import prune_cursor_agents_predecessors
from cursor_bridge.cursor_agents_hop import (
    cursor_debug_url_for_gui_host,
    rename_agent_row_in_sidebar,
)
from cursor_bridge.cursor_window_config import subscription_display_for_gui_host
from cursor_bridge.spawn_endpoint_house_ledger import (
    last_successful_wake,
    record_tab_wake,
)
from cursor_bridge.tab_ready_rehandshake import (
    post_tab_gone_for_lane,
    tab_ready_instruction,
)
from cursor_bridge.wake_delivery import _paste_cursor_services, normalize_target


def _pick_page_for_wake(
    pages: list[dict[str, str]], app: str, *, agent_id: str
) -> dict[str, str] | None:
    """Select CDP page for wake_tab; never fall back to the focused tab when agent_id is set."""
    from cursor_bridge.wake_delivery import _pick_page

    if not agent_id:
        return _pick_page(pages, app)
    from cursor_bridge.wake_delivery import _WINDOW_TITLE

    wanted = _WINDOW_TITLE.get(app, "")
    named = [
        p for p in pages if p.get("title") == wanted and p.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        return named[0]
    return None


def relay_wake_tab(
    *,
    gui_host: str,
    agent_id: str | None,
    body: str,
    thread: str | None = None,
) -> dict[str, Any]:
    """Paste a wake line into a Glass Cursor Agents tab."""
    from cursor_bridge.cdp_pages import list_pages

    if thread:
        from agent_bus_store.db.endpoint_wake_ledger import (
            resolve_wake_delivery_binding,
        )

        binding = resolve_wake_delivery_binding(house_id=str(thread).strip())
        if str(binding.get("state") or "") != "bound":
            return {
                "ok": False,
                "error": "unbound",
                "detail": str(binding.get("reason") or "unbound"),
                "op": "wake_tab",
                "gui_host": gui_host,
                "thread": thread,
            }
        actuation = dict((binding.get("current") or {}).get("actuation") or {})
        if str(actuation.get("type") or "") != "cursor_glass":
            return {
                "ok": False,
                "error": "unbound",
                "detail": "actuation_not_cursor_glass",
                "op": "wake_tab",
                "gui_host": gui_host,
                "thread": thread,
            }
        gui_host = str(actuation.get("gui_host") or gui_host)
        agent_id = str(actuation.get("agent_id") or agent_id or "")

    display = subscription_display_for_gui_host(gui_host)
    target = normalize_target(display, "cursor")
    if not target.get("ok"):
        return {
            "ok": False,
            "error": "unknown_target",
            "detail": f"wake_tab unknown gui_host {gui_host!r}",
            "op": "wake_tab",
            "gui_host": gui_host,
        }
    try:
        pages = list_pages(target["origin"])
    except OSError as exc:
        return {
            "ok": False,
            "error": "debug_unreachable",
            "detail": type(exc).__name__,
            "op": "wake_tab",
            "gui_host": gui_host,
        }
    aid = str(agent_id or "").strip()
    page = _pick_page_for_wake(pages, "cursor", agent_id=aid)
    if page is None:
        return {
            "ok": False,
            "error": "no_page",
            "op": "wake_tab",
            "gui_host": gui_host,
            "pages": len(pages),
        }
    ws_url = page["web_socket_debugger_url"]
    try:
        paste_res = asyncio.run(_paste_cursor_services(ws_url, body, agent_id=aid))
    except (TimeoutError, RuntimeError, OSError) as exc:
        record_tab_wake(
            gui_host=gui_host,
            agent_id=aid,
            op="wake_tab",
            ok=False,
            reason="paste_failed",
            detail={"detail": type(exc).__name__},
        )
        return {
            "ok": False,
            "error": "paste_failed",
            "detail": type(exc).__name__,
            "op": "wake_tab",
            "gui_host": gui_host,
        }
    record_tab_wake(
        gui_host=gui_host,
        agent_id=aid,
        op="wake_tab",
        ok=bool(paste_res.get("ok")),
        reason=str(paste_res.get("reason") or ""),
    )
    out = {**paste_res, "op": "wake_tab", "gui_host": gui_host, "display": display}
    prior = last_successful_wake(aid)
    if prior:
        out["last_successful_wake_at"] = prior.get("at")
    return out


def relay_rename_tab(
    *,
    gui_host: str,
    agent_id: str,
    title: str,
    thread: str | None = None,
) -> dict[str, Any]:
    debug_url = cursor_debug_url_for_gui_host(gui_host)
    res = rename_agent_row_in_sidebar(agent_id, title, debug_url=debug_url)
    out = {**res, "op": "rename_tab", "gui_host": gui_host}
    if res.get("ok"):
        out["tab_ready_instruction"] = tab_ready_instruction(title)
        tid = str(thread or "").strip()
        if tid:
            gone = post_tab_gone_for_lane(tid, reason="bridge_rename")
            out["tab_gone"] = gone
    prior = last_successful_wake(agent_id)
    if prior:
        out["last_successful_wake_at"] = prior.get("at")
    return out


def relay_prune_sidebar(*, gui_host: str, thread: str, agent_id: str) -> dict[str, Any]:
    debug_url = cursor_debug_url_for_gui_host(gui_host)
    res = prune_cursor_agents_predecessors(
        thread,
        active_agent_id=agent_id,
        debug_url=debug_url,
    )
    return {**res, "op": "prune_sidebar", "gui_host": gui_host}


__all__ = ["relay_prune_sidebar", "relay_rename_tab", "relay_wake_tab"]
