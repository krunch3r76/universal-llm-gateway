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
import logging
from typing import Any

from cursor_bridge.chat_target import (
    chat_title,
    normalize_agent_id,
    selection_verdict,
)

logger = logging.getLogger(__name__)

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
    const btn = el.querySelector('button[data-agent-id]');
    if (btn && btn.getAttribute('aria-label')) {
      return btn.getAttribute('aria-label').split(',')[0].trim();
    }
    const raw = (el.innerText || '').trim();
    return raw.split('\n')[0].trim();
  };
  const rowAgentId = (el) => {
    if (el.getAttribute && el.getAttribute('data-agent-id')) {
      return el.getAttribute('data-agent-id').trim();
    }
    const btn = el.querySelector ? el.querySelector('[data-agent-id]') : null;
    return btn ? (btn.getAttribute('data-agent-id') || '').trim() : '';
  };
  const rowSelected = (el) => {
    if (el.getAttribute('aria-selected') === 'true') return true;
    if (el.getAttribute('aria-current') === 'true') return true;
    return !!el.querySelector('[aria-current="page"], [aria-selected="true"], [data-active="true"]');
  };
  const items = [];
  const glassRows = Array.from(document.querySelectorAll('[data-perf-id="glass-sidebar-agent-row"]'));
  if (glassRows.length > 0) {
    for (const el of glassRows) {
      const trig = el.querySelector('.ui-sidebar-menu-button-trigger[role="button"]');
      const line = trig
        ? (trig.textContent || '').replace(/\s+/g, ' ').trim()
        : rowTitle(el);
      if (!line || line.length > 120) continue;
      const aid = (
        el.getAttribute('data-sidebar-file-drop-agent-id')
        || el.getAttribute('data-agent-id')
        || ''
      ).trim();
      const sel =
        el.getAttribute('data-active') === 'true'
        || !!(trig && trig.getAttribute('aria-current') === 'page')
        || rowSelected(el);
      items.push({text: line, selected: sel, agent_id: aid});
      if (items.length >= 40) break;
    }
  } else {
    for (const el of document.querySelectorAll('[role=listitem], [role=treeitem], [role=option]')) {
      if ((el.className || '').includes('mention-menu-item')) continue;
      const line = rowTitle(el);
      if (!line || line.length > 120) continue;
      items.push({text: line, selected: rowSelected(el), agent_id: rowAgentId(el)});
      if (items.length >= 40) break;
    }
  }
  let activeAgentId = '';
  for (const row of items) {
    if (row.selected && row.agent_id) {
      activeAgentId = String(row.agent_id || '').trim();
      break;
    }
  }
  const promptSel = activeAgentId
    ? '[aria-label="Prompt"][contenteditable="true"][data-sidebar-file-drop-editor-agent-id="' + activeAgentId + '"]'
    : '[aria-label="Prompt"][contenteditable="true"]';
  const promptEl = document.querySelector(promptSel);
  const composerDraft = promptEl ? (promptEl.innerText || '').trim() : '';
  const generating = !!Array.from(document.querySelectorAll('button')).find((b) => {
    const lbl = (b.getAttribute('aria-label') || b.getAttribute('title') || '').trim().toLowerCase();
    return lbl === 'stop' || lbl.startsWith('stop ') || lbl.includes('stop generating') || lbl.includes('stop response');
  });
  const prose = Array.from(document.querySelectorAll('.sand-message-prose'))
    .slice(-6)
    .map((el) => (el.innerText || '').trim());
  return {labels, items, composer_draft: composerDraft, generating, recent_messages: prose};
})()
"""


def classify_surface(
    labels: list[str],
    items: list[dict[str, Any]],
    *,
    composer_draft: str = "",
    generating: bool = False,
    recent_messages: list[str] | None = None,
) -> dict[str, Any]:
    """Turn a DOM probe into a sidebar observation.

    ``labels`` are button and link texts. ``items`` are rows with ``text``,
    ``selected``, and optional ``agent_id``. A Sign-in label and no rows is
    ``signed_out``. Rows are ``chat_list`` and the selected row yields
    ``active_title`` and ``active_agent_id``. Anything else is ``unknown``
    with no active title. No I/O.
    """
    titles: list[str] = []
    title_counts: dict[str, int] = {}
    agent_ids: dict[str, str] = {}
    active: str | None = None
    active_agent_id: str = ""
    for item in items:
        text = str(item.get("text") or "").strip()
        aid = str(item.get("agent_id") or "").strip()
        if not text:
            continue
        title_counts[text] = title_counts.get(text, 0) + 1
        if text not in titles:
            titles.append(text)
        if aid:
            agent_ids[aid] = text
        if item.get("selected") and active is None:
            active = text
            active_agent_id = aid
    signed_out = any(label.strip().lower() in {"sign in", "log in"} for label in labels)
    if signed_out and not titles:
        state = "signed_out"
        active = None
        active_agent_id = ""
    elif titles:
        state = "chat_list"
    else:
        state = "unknown"
        active = None
        active_agent_id = ""
    return {
        "state": state,
        "active_title": active,
        "active_agent_id": active_agent_id,
        "titles": titles,
        "title_counts": title_counts,
        "agent_ids": agent_ids,
        "composer_draft": composer_draft,
        "generating": bool(generating),
        "recent_messages": list(recent_messages or []),
    }


def wake_decision(
    surface: dict[str, Any],
    *,
    thread: str = "",
    slug: str = "",
    title: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Refuse a wake unless ``surface`` shows the exact liaison target.

    ``agent_id`` (when provided) matches ``active_agent_id`` first;
    ``title`` overrides ``"<thread> <slug>"`` when provided. ``signed_out``
    and ``unknown`` fail before the compare. Returns the verdict plus
    ``state`` and ``titles``. No I/O.
    """
    aid = normalize_agent_id(agent_id)
    expected = (
        chat_title(thread, slug, title=title)
        if (title or (thread and slug))
        else ""
    )
    state = str(surface.get("state") or "unknown")
    titles = list(surface.get("titles") or [])
    title_counts = dict(surface.get("title_counts") or {})
    agent_ids = dict(surface.get("agent_ids") or {})
    ambiguous_title = bool(expected and title_counts.get(expected, 0) > 1)
    if ambiguous_title and not (aid and aid in agent_ids):
        logger.warning(
            "wake_decision ambiguous_title expected=%r count=%s",
            expected,
            title_counts.get(expected),
        )
        return {
            "ok": False,
            "reason": "ambiguous_title",
            "state": state,
            "active_title": "",
            "active_agent_id": "",
            "expected_title": expected,
            "expected_agent_id": aid,
            "titles": titles,
        }
    if state != "chat_list":
        return {
            "ok": False,
            "reason": state if state in {"signed_out", "unknown"} else "unknown",
            "state": state,
            "active_title": "",
            "active_agent_id": "",
            "expected_title": expected,
            "expected_agent_id": aid,
            "titles": titles,
        }
    active_aid = str(surface.get("active_agent_id") or "").strip()
    active_title = str(surface.get("active_title") or "").strip()
    if aid:
        if (
            active_aid != aid
            and expected
            and aid not in agent_ids
            and active_title == expected
        ):
            ok = True
            reason = "match"
        else:
            ok = active_aid == aid
            reason = (
                "match"
                if ok
                else ("no_active_chat" if not active_title else "agent_id_mismatch")
            )
        verdict = {
            "ok": ok,
            "reason": reason,
            "active_title": active_title,
            "active_agent_id": active_aid,
            "expected_title": expected or (surface.get("agent_ids") or {}).get(aid, ""),
            "expected_agent_id": aid,
        }
    else:
        verdict = selection_verdict(active_title, expected)
        verdict["active_agent_id"] = active_aid
        verdict["expected_agent_id"] = ""
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
    return classify_surface(
        labels,
        items,
        composer_draft=str(value.get("composer_draft") or ""),
        generating=bool(value.get("generating")),
        recent_messages=[str(x) for x in value.get("recent_messages") or []],
    )


async def select_sidebar_target(
    ws_url: str,
    *,
    expected_title: str = "",
    expected_agent_id: str = "",
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Click a sidebar row by ``data-agent-id`` or exact title in the page DOM."""
    import websockets

    js = (
        r"""
((targetId, targetTitle) => {
  const rowTitle = (el) => {
    const titled = el.querySelector('[class*="agent-item__title"]');
    if (titled) {
      const text = (titled.textContent || '').replace(/\s+/g, ' ').trim();
      if (text) return text;
    }
    const glassTrig = el.querySelector('.ui-sidebar-menu-button-trigger[role="button"]');
    if (glassTrig) {
      const text = (glassTrig.textContent || '').replace(/\s+/g, ' ').trim();
      if (text) return text;
    }
    const raw = (el.innerText || '').trim();
    return raw.split('\n')[0].trim();
  };
  const rowNodes = () =>
    document.querySelectorAll('[data-perf-id="glass-sidebar-agent-row"]').length > 0
      ? document.querySelectorAll('[data-perf-id="glass-sidebar-agent-row"]')
      : document.querySelectorAll('[role=listitem], [role=treeitem]');
  const rowClickTarget = (el) =>
    el.querySelector('.ui-sidebar-menu-button-trigger[role="button"]')
    || el.querySelector('button[data-agent-id]')
    || el.querySelector('button, a, [role=button]')
    || el;
  const rowAgentId = (el, btn) =>
    (el.getAttribute && (
      el.getAttribute('data-sidebar-file-drop-agent-id')
      || el.getAttribute('data-agent-id')
    ) || '').trim()
    || (btn.getAttribute && btn.getAttribute('data-agent-id') || '').trim();
  let titleMatches = 0;
  for (const el of rowNodes()) {
    const title = rowTitle(el);
    if (targetTitle && title === targetTitle) titleMatches += 1;
  }
  if (targetTitle && titleMatches > 1 && !targetId) {
    return {clicked: false, reason: 'ambiguous_title'};
  }
  for (const el of rowNodes()) {
    const btn = rowClickTarget(el);
    const aid = rowAgentId(el, btn);
    const title = rowTitle(el);
    if (targetId && aid === targetId) {
      btn.click();
      return {clicked: true, matched_by: 'agent_id', title, agent_id: aid};
    }
    if (!targetId && targetTitle && title === targetTitle) {
      btn.click();
      return {clicked: true, matched_by: 'title', title, agent_id: aid};
    }
  }
  if (targetId && targetTitle && titleMatches === 1) {
    for (const el of rowNodes()) {
      const btn = rowClickTarget(el);
      const aid = rowAgentId(el, btn);
      const title = rowTitle(el);
      if (title === targetTitle) {
        btn.click();
        return {clicked: true, matched_by: 'title_fallback', title, agent_id: aid};
      }
    }
  }
  return {clicked: false};
})("""
        + json.dumps(expected_agent_id)
        + ", "
        + json.dumps(expected_title)
        + ")"
    )
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True},
                }
            )
        )
        raw = await asyncio.wait_for(_response(ws, 1), timeout_s)
    value = ((raw.get("result") or {}).get("result") or {}).get("value")
    return value if isinstance(value, dict) else {"clicked": False}


async def _response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg
