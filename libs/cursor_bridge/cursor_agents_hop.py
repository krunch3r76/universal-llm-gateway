"""Headless CDP hop for Cursor Agents on Xvfb (:8) and orion-node tunnel."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.glass_services_js import launch_new_agent_tab_js

_CURSOR_AGENTS_TITLE = "Cursor Agents"
_XVFB_HOSTS = frozenset(
    {
        ":8",
        "8",
        "io:8",
        "127.0.0.1:9338",
        "http://127.0.0.1:9338",
        "orion-node",
        "127.0.0.1:19338",
        "http://127.0.0.1:19338",
    }
)
_DEBUG_ORIGIN_BY_HOST: dict[str, str] = {
    ":8": "http://127.0.0.1:9338",
    "8": "http://127.0.0.1:9338",
    "io:8": "http://127.0.0.1:9338",
    "127.0.0.1:9338": "http://127.0.0.1:9338",
    "http://127.0.0.1:9338": "http://127.0.0.1:9338",
    "orion-node": "http://127.0.0.1:19338",
    "127.0.0.1:19338": "http://127.0.0.1:19338",
    "http://127.0.0.1:19338": "http://127.0.0.1:19338",
}


def cursor_debug_url_for_gui_host(gui_host: str | None) -> str:
    """Map a GUI host label to the local CDP origin for Cursor Agents."""
    key = str(gui_host or "").strip() or ":8"
    if key in _DEBUG_ORIGIN_BY_HOST:
        return _DEBUG_ORIGIN_BY_HOST[key]
    lowered = key.lstrip(":").lower()
    if lowered == "8":
        return "http://127.0.0.1:9338"
    if lowered == "orion-node":
        return "http://127.0.0.1:19338"
    return "http://127.0.0.1:9338"


def is_cursor_xvfb_host(gui_host: str | None) -> bool:
    """True when ``gui_host`` names a Glass Cursor Agents CDP target."""
    return str(gui_host or "").strip() in _XVFB_HOSTS


def pick_chat_page(
    pages: list[dict[str, str]], *, surface: str = "cursor"
) -> dict[str, str] | None:
    """Pick the debug page for ``surface`` (cursor → Cursor Agents title)."""
    del surface  # cursor-only for now
    named = [
        p
        for p in pages
        if p.get("title") == _CURSOR_AGENTS_TITLE and p.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        return named[0]
    usable = [p for p in pages if p.get("web_socket_debugger_url")]
    return usable[0] if len(usable) == 1 else None


def launch_cursor_agents_tab(
    message: str,
    *,
    debug_url: str | None = None,
    model_query: str = "",
    timeout_s: float = 15.0,
    gui_host: str | None = None,
) -> dict[str, Any]:
    """Open a new agent via Glass services, set model, paste ``message``, and submit."""
    origin = debug_url or cursor_debug_url_for_gui_host(gui_host)
    try:
        pages = list_pages(origin, timeout_s=min(timeout_s, 5.0))
    except OSError as exc:
        return {
            "ok": False,
            "reason": "debug_unreachable",
            "detail": type(exc).__name__,
            "model_query": model_query,
        }
    page = pick_chat_page(pages, surface="cursor")
    if page is None:
        return {"ok": False, "reason": "no_page", "pages": len(pages), "model_query": model_query}
    ws_url = page["web_socket_debugger_url"]
    return asyncio.run(
        _launch_async(
            ws_url, message, page, timeout_s=timeout_s, model_query=model_query
        )
    )


async def _launch_async(
    ws_url: str,
    message: str,
    page: dict[str, str],
    *,
    timeout_s: float,
    model_query: str = "",
) -> dict[str, Any]:
    import websockets

    expression = launch_new_agent_tab_js(message, model_query)
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        result = await _eval_await(ws, 1, expression, timeout_s)
        if not isinstance(result, dict):
            return {
                "ok": False,
                "reason": "launch_eval_failed",
                "model_query": model_query,
                "result": result,
            }
        if not result.get("ok"):
            result.setdefault("model_query", model_query)
            return result
    editor_id = str(result.get("editor_agent_id") or result.get("agent_id") or "").strip()
    return {
        "ok": True,
        "transport": "cdp_cursor_agents",
        "page_title": page.get("title"),
        "editor_agent_id": editor_id,
        "model_query": model_query,
        "model_pick": {"ok": True, "model_query": model_query} if model_query else {"ok": True, "skipped": True},
        "send_fallback_clicked": False,
        "glass_launch": result,
    }


async def _eval(ws: Any, mid: int, expression: str, timeout_s: float) -> Any:
    await ws.send(
        json.dumps(
            {
                "id": mid,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }
        )
    )
    raw = await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def _eval_await(ws: Any, mid: int, expression: str, timeout_s: float) -> Any:
    await ws.send(
        json.dumps(
            {
                "id": mid,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            }
        )
    )
    raw = await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def _recv_id(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg


from cursor_bridge.cursor_agents_archive import (  # noqa: E402
    _GLASS_ROW_PROBE_JS,
    _archive_agent_js,
    _archive_async,
    _archive_on_ws,
    _cursor_agents_ws_url,
    _list_root_sidebar_rows_js,
    _prune_predecessors_async,
    _rename_agent_row_js,
    _rename_agent_title_js,
    _root_row_pattern,
    _select_agent_js,
    archive_cursor_agents_tab,
    prune_cursor_agents_predecessors,
    rename_agent_row_in_sidebar,
    rename_cursor_agents_sidebar_title,
)

__all__ = [
    "_GLASS_ROW_PROBE_JS",
    "_archive_agent_js",
    "_archive_async",
    "_archive_on_ws",
    "_cursor_agents_ws_url",
    "_eval",
    "_eval_await",
    "_list_root_sidebar_rows_js",
    "_prune_predecessors_async",
    "_recv_id",
    "_select_agent_js",
    "archive_cursor_agents_tab",
    "cursor_debug_url_for_gui_host",
    "is_cursor_xvfb_host",
    "launch_cursor_agents_tab",
    "list_pages",
    "pick_chat_page",
    "prune_cursor_agents_predecessors",
    "rename_agent_row_in_sidebar",
    "rename_cursor_agents_sidebar_title",
    "_rename_agent_row_js",
    "_rename_agent_title_js",
    "_root_row_pattern",
]
