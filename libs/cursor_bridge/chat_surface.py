"""Read a liaison window and decide whether a wake paste is allowed.

The debug-port page list only names the window (``Grok Bot``, ``Cursor
Agents``). This module reads the page itself: a Sign-in control with no
chat rows is ``signed_out``, and a row list yields the active sidebar
title. The title is the row's title element, not the preview line under
it. ``wake_decision`` then applies ``selection_verdict``. A bus
``TAB_READY`` is not an input. No paste is sent here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cursor_bridge.chat_target import chat_title, selection_verdict

_PROBE_JS = r"""
(() => {
  const flat = (el) => (el.innerText || el.getAttribute('aria-label') || '')
    .replace(/\s+/g, ' ').trim();
  const labels = [];
  for (const el of document.querySelectorAll('button, a, [role=button]')) {
    const text = flat(el);
    if (text && text.length <= 80) labels.push(text);
    if (labels.length >= 40) break;
  }
  const rowTitle = (el) => {
    const titled = el.querySelector('[class*="agent-item__title"]');
    if (titled) {
      const text = (titled.textContent || '').replace(/\s+/g, ' ').trim();
      if (text) return text;
    }
    const raw = (el.innerText || '').trim();
    return raw.split('\n')[0].trim();
  };
  const rowSelected = (el) => {
    if (el.getAttribute('aria-selected') === 'true') return true;
    if (el.getAttribute('aria-current') === 'true') return true;
    return !!el.querySelector('[aria-current="page"], [aria-selected="true"], [data-active="true"]');
  };
  const items = [];
  for (const el of document.querySelectorAll('[role=option], [role=listitem], [role=treeitem]')) {
    const line = rowTitle(el);
    if (!line || line.length > 120) continue;
    items.push({text: line, selected: rowSelected(el)});
    if (items.length >= 40) break;
  }
  return {labels, items};
})()
"""


def classify_surface(labels: list[str], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn a DOM probe into a sidebar observation.

    ``labels`` are button and link texts. ``items`` are rows with ``text``
    and ``selected``. A Sign-in label and no rows is ``signed_out``. Rows
    are ``chat_list`` and the selected row is ``active_title``. Anything
    else is ``unknown`` with no active title. No I/O.
    """
    titles: list[str] = []
    active: str | None = None
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text or text in titles:
            continue
        titles.append(text)
        if item.get("selected") and active is None:
            active = text
    signed_out = any(label.strip().lower() == "sign in" for label in labels)
    if signed_out and not titles:
        state = "signed_out"
        active = None
    elif titles:
        state = "chat_list"
    else:
        state = "unknown"
        active = None
    return {"state": state, "active_title": active, "titles": titles}


def wake_decision(surface: dict[str, Any], *, thread: str, slug: str) -> dict[str, Any]:
    """Refuse a wake unless ``surface`` shows the exact liaison title.

    ``signed_out`` and ``unknown`` fail before the title compare. A chat
    list uses ``selection_verdict``. Returns that verdict plus ``state``
    and ``titles``. No I/O.
    """
    expected = chat_title(thread, slug)
    state = str(surface.get("state") or "unknown")
    titles = list(surface.get("titles") or [])
    if state != "chat_list":
        return {
            "ok": False,
            "reason": state if state in {"signed_out", "unknown"} else "unknown",
            "state": state,
            "active_title": "",
            "expected_title": expected,
            "titles": titles,
        }
    verdict = selection_verdict(surface.get("active_title"), expected)
    verdict["state"] = state
    verdict["titles"] = titles
    return verdict


async def probe_surface(ws_url: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
    """Classify one page for a caller that already has an event loop.

    The pipeline step uses this. ``read_surface`` starts its own loop and
    cannot be nested under that step. Same probe, same exceptions, no paste.
    """
    return await _read_surface(ws_url, timeout_s=timeout_s)


def read_surface(ws_url: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
    """Evaluate the sidebar probe on one page websocket and classify it.

    ``ws_url`` is ``web_socket_debugger_url`` from ``list_pages``. Raises
    ``TimeoutError`` when the debug port does not answer, and ``RuntimeError``
    when the page evaluation fails. Does not paste or change the page.
    """
    return asyncio.run(_read_surface(ws_url, timeout_s=timeout_s))


async def _read_surface(ws_url: str, *, timeout_s: float) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": _PROBE_JS, "returnByValue": True},
                }
            )
        )
        raw = await asyncio.wait_for(_response(ws, 1), timeout_s)
    value = ((raw.get("result") or {}).get("result") or {}).get("value")
    if not isinstance(value, dict):
        detail = (raw.get("result") or {}).get("exceptionDetails")
        raise RuntimeError(f"surface probe failed: {detail or raw}")
    labels = [str(x) for x in value.get("labels") or []]
    items = [x for x in value.get("items") or [] if isinstance(x, dict)]
    return classify_surface(labels, items)


async def _response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg
