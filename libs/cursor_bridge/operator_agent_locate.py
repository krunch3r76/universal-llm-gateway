"""Locate Grok Bot chats by agent_id when sidebar rows are hidden."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

LocateMethod = Literal["sidebar", "cmd_k", "hidden_chats"]

_VISIBLE_AGENT_IDS_JS = r"""
(() => {
  const hiddenBtn = Array.from(document.querySelectorAll('button')).find((b) =>
    /^Hidden Bots/i.test((b.innerText || '').replace(/\s+/g, ' ').trim())
  );
  let hiddenPanel = null;
  if (hiddenBtn) {
    let panel = hiddenBtn.parentElement;
    for (let i = 0; i < 8 && panel; i += 1) {
      if (panel.querySelectorAll('button[data-agent-id]').length > 5) {
        hiddenPanel = panel;
        break;
      }
      panel = panel.parentElement;
    }
  }
  const ids = [];
  for (const el of document.querySelectorAll('button[data-agent-id]')) {
    if (hiddenPanel && hiddenPanel.contains(el)) continue;
    const aid = (el.getAttribute('data-agent-id') || '').trim();
    if (aid) ids.push(aid);
  }
  return ids;
})()
"""

_OPEN_SEARCH_JS = r"""
(() => {
  const inp = document.querySelector('input[aria-label="Search"], input[placeholder="Search"]');
  if (inp) return {ok: true, already_open: true};
  const btn = Array.from(document.querySelectorAll('button, [role=button]')).find((b) => {
    const t = (b.getAttribute('aria-label') || b.innerText || '').trim().toLowerCase();
    return t === 'search' || t.includes('search bots') || t.includes('search chats');
  });
  if (!btn) return {ok: false, reason: 'no_search_button'};
  btn.click();
  return {ok: true, opened: true};
})()
"""

_LOCATE_CMD_K_JS = r"""
(async (target) => {
  const inp = document.querySelector('input[aria-label="Search"], input[placeholder="Search"]');
  if (!inp) return {ok: false, reason: 'no_search_input'};
  const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inp), 'value')
    || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  if (desc && typeof desc.set === 'function') desc.set.call(inp, target);
  else inp.value = target;
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  await new Promise((r) => setTimeout(r, 450));
  for (const el of document.querySelectorAll('[role=option], button[data-agent-id]')) {
    const aid = (el.getAttribute('data-agent-id') || '').trim();
    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (aid === target || text.includes(target)) {
      el.click();
      await new Promise((r) => setTimeout(r, 250));
      return {ok: true, matched_by: aid === target ? 'agent_id' : 'text'};
    }
  }
  return {ok: false, reason: 'no_search_match'};
})
"""

_LOCATE_HIDDEN_JS = r"""
(async (target) => {
  const hiddenBtn = Array.from(document.querySelectorAll('button')).find((b) =>
    /^Hidden Bots/i.test((b.innerText || '').replace(/\s+/g, ' ').trim())
  );
  if (!hiddenBtn) return {ok: false, reason: 'no_hidden_button'};
  hiddenBtn.click();
  await new Promise((r) => setTimeout(r, 250));
  for (const el of document.querySelectorAll('button[data-agent-id]')) {
    if ((el.getAttribute('data-agent-id') || '').trim() !== target) continue;
    el.click();
    await new Promise((r) => setTimeout(r, 250));
    return {ok: true};
  }
  return {ok: false, reason: 'not_in_hidden_list'};
})
"""


async def _cdp_eval(
    ws_url: str, expression: str, *, await_promise: bool = False, timeout_s: float = 8.0
) -> Any:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": await_promise,
                        "returnByValue": True,
                    },
                }
            )
        )
        raw = await asyncio.wait_for(_response(ws, 1), timeout_s)
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def _response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg


async def _open_search_panel(ws_url: str) -> dict[str, Any]:
    value = await _cdp_eval(ws_url, _OPEN_SEARCH_JS)
    await asyncio.sleep(0.25)
    return value if isinstance(value, dict) else {"ok": False}


async def visible_sidebar_agent_ids(ws_url: str) -> set[str]:
    """Agent ids shown in the visible sidebar (excludes Hidden Bots panel rows)."""
    raw = await _cdp_eval(ws_url, _VISIBLE_AGENT_IDS_JS)
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if str(x).strip()}


async def locate_via_cmd_k(ws_url: str, agent_id: str) -> dict[str, Any]:
    """Open search via DOM and select the row matching ``agent_id``."""
    opened = await _open_search_panel(ws_url)
    if not opened.get("ok") and not opened.get("already_open"):
        return {"ok": False, "locate_method": "cmd_k", **opened}
    expr = f"({_LOCATE_CMD_K_JS})({json.dumps(agent_id)})"
    value = await _cdp_eval(ws_url, expr, await_promise=True)
    if isinstance(value, dict) and value.get("ok"):
        return {"ok": True, "locate_method": "cmd_k", **value}
    return {
        "ok": False,
        "locate_method": "cmd_k",
        **(value if isinstance(value, dict) else {}),
    }


async def locate_via_hidden_chats(ws_url: str, agent_id: str) -> dict[str, Any]:
    """Open Hidden Bots and click the row for ``agent_id``."""
    expr = f"({_LOCATE_HIDDEN_JS})({json.dumps(agent_id)})"
    value = await _cdp_eval(ws_url, expr, await_promise=True)
    if isinstance(value, dict) and value.get("ok"):
        return {"ok": True, "locate_method": "hidden_chats", **value}
    return {
        "ok": False,
        "locate_method": "hidden_chats",
        **(value if isinstance(value, dict) else {}),
    }


async def locate_off_sidebar(ws_url: str, agent_id: str) -> dict[str, Any]:
    """Try Cmd-K search, then Hidden Bots manager."""
    cmd = await locate_via_cmd_k(ws_url, agent_id)
    if cmd.get("ok"):
        return cmd
    hidden = await locate_via_hidden_chats(ws_url, agent_id)
    if hidden.get("ok"):
        return hidden
    return {
        "ok": False,
        "reason": "agent_not_found",
        "attempts": [cmd, hidden],
    }


__all__ = [
    "LocateMethod",
    "locate_off_sidebar",
    "locate_via_cmd_k",
    "locate_via_hidden_chats",
    "visible_sidebar_agent_ids",
]
