"""Headless CDP hop for Cursor Agents on Xvfb display :8 (9338)."""

from __future__ import annotations

import asyncio
import json
import time
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
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Open a new agent via CDP, paste ``message``, and submit."""
    try:
        pages = list_pages(debug_url, timeout_s=min(timeout_s, 5.0))
    except OSError as exc:
        return {"ok": False, "reason": "debug_unreachable", "detail": type(exc).__name__}
    page = pick_chat_page(pages, surface="cursor")
    if page is None:
        return {"ok": False, "reason": "no_page", "pages": len(pages)}
    ws_url = page["web_socket_debugger_url"]
    return asyncio.run(_launch_async(ws_url, message, page, timeout_s=timeout_s))


async def _launch_async(
    ws_url: str, message: str, page: dict[str, str], *, timeout_s: float
) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        click = await _eval(ws, 1, _NEW_AGENT_CLICK_JS, timeout_s)
        if not (isinstance(click, dict) and click.get("ok")):
            reason = click.get("reason") if isinstance(click, dict) else "new_agent_click_failed"
            return {"ok": False, "reason": reason, "click": click}
        await asyncio.sleep(0.25)
        focus = await _eval(ws, 2, _COMPOSER_FOCUS_JS, timeout_s)
        if not (isinstance(focus, dict) and focus.get("ok")):
            reason = focus.get("reason") if isinstance(focus, dict) else "composer_focus_failed"
            return {"ok": False, "reason": reason, "focus": focus}
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


_GLASS_ROW_PROBE_JS = r"""
  const rowTitle = (el) => {
    const t = el.querySelector('[class*="agent-item__title"]');
    if (t) { const x = (t.textContent || '').replace(/\s+/g, ' ').trim(); if (x) return x; }
    const b = el.querySelector('button[data-agent-id]');
    if (b && b.getAttribute('aria-label')) return b.getAttribute('aria-label').split(',')[0].trim();
    return (el.innerText || '').trim().split('\n')[0].trim();
  };
  const rowActive = (el) => el.getAttribute('data-active') === 'true'
    || !!(el.querySelector('.ui-sidebar-menu-button-trigger[aria-current="page"]'))
    || el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-current') === 'true'
    || !!el.querySelector('[aria-current="page"], [aria-selected="true"]');
  const rowStreaming = (el) => Boolean(
    el.querySelector('[aria-label="In progress"], .ui-dot-grid-loader')
  );
  const rowAgentId = (el) => (
    el.getAttribute('data-sidebar-file-drop-agent-id') || el.getAttribute('data-agent-id') || ''
  ).trim();
"""


def _archive_agent_js(agent_id: str) -> str:
    aid = json.dumps(agent_id.strip())
    return (
        f"(() => {{ const target = {aid};"
        f"{_GLASS_ROW_PROBE_JS}"
        " for (const el of document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]')) {"
        " if (rowAgentId(el) !== target) continue;"
        " if (rowStreaming(el)) return {ok: false, reason: 'agent_streaming', agent_id: target};"
        " const btn = el.querySelector('button[aria-label=\"Archive\"]');"
        " if (!btn) return {ok: false, reason: 'no_archive_btn'};"
        " btn.click(); return {ok: true, archived: target};"
        " } return {ok: false, reason: 'row_not_found'}; })()"
    )


def _list_root_sidebar_rows_js(root_id: str) -> str:
    rid = json.dumps(root_id.strip())
    return (
        f"(() => {{ const rootId = {rid};"
        f"{_GLASS_ROW_PROBE_JS}"
        " const esc = rootId.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');"
        " const re = new RegExp('^\\\\.?\\\\s*' + esc + '\\\\b|^resume\\\\s+' + esc + '\\\\b', 'i');"
        " const out = [];"
        " for (const el of document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]')) {"
        " const title = rowTitle(el); if (!re.test(title)) continue;"
        " out.push({agent_id: rowAgentId(el), title, active: rowActive(el), streaming: rowStreaming(el)});"
        " } return out; })()"
    )


def _cursor_agents_ws_url(
    debug_url: str, timeout_s: float
) -> tuple[str | None, dict[str, Any] | None]:
    try:
        pages = list_pages(debug_url, timeout_s=min(timeout_s, 5.0))
    except OSError as exc:
        return None, {"ok": False, "reason": "debug_unreachable", "detail": type(exc).__name__}
    page = pick_chat_page(pages, surface="cursor")
    if page is None:
        return None, {"ok": False, "reason": "no_page", "pages": len(pages)}
    return page["web_socket_debugger_url"], None


def archive_cursor_agents_tab(
    agent_id: str,
    *,
    debug_url: str = "http://127.0.0.1:9338",
    timeout_s: float = 10.0,
    wait_until_idle_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> dict[str, Any]:
    """Archive a retired agent row in the Cursor Agents sidebar via CDP."""
    if not str(agent_id or "").strip():
        return {"ok": False, "reason": "empty_agent_id"}
    ws_url, err = _cursor_agents_ws_url(debug_url, timeout_s)
    if err:
        return err
    assert ws_url is not None
    return asyncio.run(
        _archive_async(
            ws_url,
            agent_id.strip(),
            timeout_s=timeout_s,
            wait_until_idle_s=wait_until_idle_s,
            poll_interval_s=poll_interval_s,
        )
    )


async def _archive_on_ws(
    ws: Any,
    agent_id: str,
    *,
    timeout_s: float,
    wait_until_idle_s: float,
    poll_interval_s: float,
    eval_id: int = 1,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, wait_until_idle_s)
    while True:
        result = await _eval(ws, eval_id, _archive_agent_js(agent_id), timeout_s)
        if isinstance(result, dict):
            if result.get("ok") or result.get("reason") != "agent_streaming":
                return result
            if time.monotonic() >= deadline:
                return result
        else:
            return {"ok": False, "reason": "archive_eval_failed", "result": result}
        await asyncio.sleep(poll_interval_s)


async def _archive_async(
    ws_url: str,
    agent_id: str,
    *,
    timeout_s: float,
    wait_until_idle_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        return await _archive_on_ws(
            ws,
            agent_id,
            timeout_s=timeout_s,
            wait_until_idle_s=wait_until_idle_s,
            poll_interval_s=poll_interval_s,
        )


def prune_cursor_agents_predecessors(
    root_id: str,
    *,
    active_agent_id: str = "",
    departing_agent_id: str = "",
    keep_recent_predecessors: int = 1,
    debug_url: str = "http://127.0.0.1:9338",
    timeout_s: float = 10.0,
    wait_until_idle_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> dict[str, Any]:
    """Archive older root predecessors; keep recent ones active in the sidebar."""
    root = str(root_id or "").strip()
    if not root:
        return {"ok": False, "reason": "empty_root_id"}
    ws_url, err = _cursor_agents_ws_url(debug_url, timeout_s)
    if err:
        return err
    assert ws_url is not None
    return asyncio.run(
        _prune_predecessors_async(
            ws_url,
            root,
            active_agent_id=active_agent_id,
            departing_agent_id=departing_agent_id,
            keep_recent_predecessors=keep_recent_predecessors,
            timeout_s=timeout_s,
            wait_until_idle_s=wait_until_idle_s,
            poll_interval_s=poll_interval_s,
        )
    )


async def _prune_predecessors_async(
    ws_url: str,
    root_id: str,
    *,
    active_agent_id: str,
    departing_agent_id: str,
    keep_recent_predecessors: int,
    timeout_s: float,
    wait_until_idle_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    import websockets

    active_aid = str(active_agent_id or "").strip()
    departing = str(departing_agent_id or "").strip()
    archived: list[str] = []
    skipped: list[dict[str, Any]] = []
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        raw_rows = await _eval(ws, 1, _list_root_sidebar_rows_js(root_id), timeout_s)
        if not isinstance(raw_rows, list):
            return {"ok": False, "reason": "list_rows_failed", "result": raw_rows}
        predecessors: list[str] = []
        active_skip_done = False
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("agent_id") or "").strip()
            if not aid:
                continue
            if active_aid:
                if aid == active_aid:
                    continue
            elif row.get("active") and aid != departing and not active_skip_done:
                active_skip_done = True
                continue
            predecessors.append(aid)
        if departing:
            predecessors = [departing] + [a for a in predecessors if a != departing]
        kept_predecessors = predecessors[: max(0, keep_recent_predecessors)]
        for mid, aid in enumerate(predecessors[max(0, keep_recent_predecessors) :], start=2):
            result = await _archive_on_ws(
                ws,
                aid,
                timeout_s=timeout_s,
                wait_until_idle_s=wait_until_idle_s,
                poll_interval_s=poll_interval_s,
                eval_id=mid,
            )
            if isinstance(result, dict) and result.get("ok"):
                archived.append(aid)
            else:
                skipped.append(
                    {"agent_id": aid, **(result if isinstance(result, dict) else {"result": result})}
                )
    return {
        "ok": True,
        "root_id": root_id,
        "kept_predecessors": kept_predecessors,
        "archived": archived,
        "skipped": skipped,
    }
