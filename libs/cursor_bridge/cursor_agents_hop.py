"""Headless CDP hop for Cursor Agents on Xvfb display :8 (9338)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cursor_bridge.cdp_pages import list_pages

_CURSOR_AGENTS_TITLE = "Cursor Agents"
_XVFB_HOSTS = frozenset({":8", "8", "io:8", "127.0.0.1:9338", "http://127.0.0.1:9338"})

_NEW_AGENT_CLICK_JS = r"""
(() => {
  const sel =
    '[data-action-id="new-agent"] .ui-sidebar-menu-button-trigger[role="button"],'
    + '[data-action-id="new-agent"],'
    + 'button[aria-label="New Agent"]';
  const el = document.querySelector(sel);
  if (!el) return {ok: false, reason: 'new_agent_missing'};
  el.click();
  return {ok: true};
})()
"""

_COMPOSER_FOCUS_JS = r"""
(() => {
  const el = document.querySelector('[aria-label="Prompt"][contenteditable="true"]');
  if (!el) return {ok: false, reason: 'no_composer'};
  const draft = (el.innerText || '').trim();
  const stale = /^BRIDGE_WAKE\s+thread=\S+\s+turn=\d+(?:,\d+)*$/.test(draft);
  if (draft.length > 0 && !stale) return {ok: false, reason: 'composer_not_empty', draft};
  el.focus();
  if (stale) {
    document.execCommand('delete', false, null);
    el.innerText = '';
  }
  return {ok: true};
})()
"""

_SEND_FALLBACK_JS = r"""
(() => {
  const el = document.querySelector('[aria-label="Prompt"][contenteditable="true"]');
  if (!el) return {ok: true, send_fallback_clicked: false};
  const text = (el.innerText || '').trim();
  if (!text) return {ok: true, send_fallback_clicked: false};
  const btn =
    document.querySelector('.ui-prompt-input-submit-button')
    || document.querySelector('button[aria-label*="Send"]');
  if (!btn) return {ok: true, send_fallback_clicked: false};
  btn.click();
  return {ok: true, send_fallback_clicked: true};
})()
"""


def is_cursor_xvfb_host(gui_host: str | None) -> bool:
    """True when ``gui_host`` names the Glass Cursor Agents display on :8."""
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
    debug_url: str = "http://127.0.0.1:9338",
    model_query: str = "",
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Open a new agent via CDP, paste ``message``, and submit."""
    try:
        pages = list_pages(debug_url, timeout_s=min(timeout_s, 5.0))
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


async def _dispatch_ctrl_slash(ws: Any, mid: int, timeout_s: float) -> None:
    for phase in ("keyDown", "keyUp"):
        await ws.send(
            json.dumps(
                {
                    "id": mid,
                    "method": "Input.dispatchKeyEvent",
                    "params": {
                        "type": phase,
                        "modifiers": 2,
                        "key": "/",
                        "code": "Slash",
                        "windowsVirtualKeyCode": 191,
                        "nativeVirtualKeyCode": 191,
                    },
                }
            )
        )
        await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
        mid += 1


async def _try_select_model_query(
    ws: Any, model_query: str, *, timeout_s: float, start_id: int
) -> dict[str, Any]:
    query = str(model_query or "").strip()
    if not query:
        return {"ok": True, "skipped": True}
    mid = start_id
    try:
        await _dispatch_ctrl_slash(ws, mid, timeout_s)
        mid += 2
        await ws.send(
            json.dumps({"id": mid, "method": "Input.insertText", "params": {"text": query}})
        )
        await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
        mid += 1
        for phase in ("keyDown", "keyUp"):
            await ws.send(
                json.dumps(
                    {
                        "id": mid,
                        "method": "Input.dispatchKeyEvent",
                        "params": {
                            "type": phase,
                            "key": "Enter",
                            "code": "Enter",
                            "windowsVirtualKeyCode": 13,
                            "nativeVirtualKeyCode": 13,
                        },
                    }
                )
            )
            await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
            mid += 1
        await asyncio.sleep(0.2)
        return {"ok": True, "model_query": query}
    except Exception as exc:  # noqa: BLE001 — best-effort; paste flow continues
        return {"ok": False, "reason": "model_query_failed", "detail": type(exc).__name__}


async def _launch_async(
    ws_url: str,
    message: str,
    page: dict[str, str],
    *,
    timeout_s: float,
    model_query: str = "",
) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        click = await _eval(ws, 1, _NEW_AGENT_CLICK_JS, timeout_s)
        if not (isinstance(click, dict) and click.get("ok")):
            reason = click.get("reason") if isinstance(click, dict) else "new_agent_click_failed"
            return {
                "ok": False,
                "reason": reason,
                "click": click,
                "model_query": model_query,
            }
        await asyncio.sleep(0.25)
        model_pick = await _try_select_model_query(
            ws, model_query, timeout_s=timeout_s, start_id=10
        )
        focus = await _eval(ws, 2, _COMPOSER_FOCUS_JS, timeout_s)
        if not (isinstance(focus, dict) and focus.get("ok")):
            reason = focus.get("reason") if isinstance(focus, dict) else "composer_focus_failed"
            return {
                "ok": False,
                "reason": reason,
                "focus": focus,
                "model_query": model_query,
                "model_pick": model_pick,
            }
        await ws.send(
            json.dumps({"id": 3, "method": "Input.insertText", "params": {"text": message}})
        )
        await asyncio.wait_for(_recv_id(ws, 3), timeout_s)
        for mid, phase in ((4, "keyDown"), (5, "keyUp")):
            await ws.send(
                json.dumps(
                    {
                        "id": mid,
                        "method": "Input.dispatchKeyEvent",
                        "params": {
                            "type": phase,
                            "key": "Enter",
                            "code": "Enter",
                            "windowsVirtualKeyCode": 13,
                            "nativeVirtualKeyCode": 13,
                        },
                    }
                )
            )
            await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
        await asyncio.sleep(0.15)
        fallback = await _eval(ws, 6, _SEND_FALLBACK_JS, timeout_s)
        editor_id = ""
        probe = await _eval(
            ws,
            7,
            "(() => { const el = document.querySelector('[data-agent-id]'); "
            "return el ? (el.getAttribute('data-agent-id') || '') : ''; })()",
            timeout_s,
        )
        if isinstance(probe, str):
            editor_id = probe.strip()
    return {
        "ok": True,
        "transport": "cdp_cursor_agents",
        "page_title": page.get("title"),
        "editor_agent_id": editor_id,
        "model_query": model_query,
        "model_pick": model_pick,
        "send_fallback_clicked": bool(
            isinstance(fallback, dict) and fallback.get("send_fallback_clicked")
        ),
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
    "_list_root_sidebar_rows_js",
    "_prune_predecessors_async",
    "_recv_id",
    "_select_agent_js",
    "archive_cursor_agents_tab",
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
