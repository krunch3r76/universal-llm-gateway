"""ChatGPT work wake: structural empty ProseMirror + single-session CDP insert.

Used only from ``orion_wake._dom_deliver`` — generic ``dom_composer_paste`` unchanged.

Live Work UI may keep Send disabled until a frontend-specific input event; that
enable path is not synthesized here (no Space/Backspace/Enter nudge). When Send
never enables, callers get ``send_button_disabled`` with owned draft retained.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cursor_bridge.dom_composer_paste import (
    INSTALL_OPERATOR_ACTIVITY_PROBE_JS,
    dom_click_send,
    operator_activity_clear_suppress_js,
    operator_activity_suppress_js,
)

_INSERT_METHOD = "Input.insertText"


def _selector_list(selector: str) -> list[str]:
    return [s.strip() for s in selector.split(",") if s.strip()]


def _normalized_line(text: str, *, preserve_newlines: bool) -> str:
    if preserve_newlines:
        return (text or "").strip()
    return " ".join((text or "").splitlines()).strip()


def chatgpt_work_preflight_expression(
    *,
    selector: str,
    line: str,
    preserve_newlines: bool,
    expected_conversation_id: str,
    attempt_id: str = "",
) -> str:
    """Resolve composer once; safety scans before ownership; optional focus."""
    sels = json.dumps(_selector_list(selector) or [selector])
    lj = json.dumps(_normalized_line(line, preserve_newlines=preserve_newlines))
    exp_cid = json.dumps((expected_conversation_id or "").strip().lower())
    attempt = json.dumps(str(attempt_id or ""))
    return f"""
(() => {{
  const sels = {sels};
  const line = {lj};
  const expectedCid = {exp_cid};
  const attemptId = {attempt};
  let el = null;
  let matchedSelector = '';
  for (const s of sels) {{
    const hit = document.querySelector(s);
    if (hit) {{ el = hit; matchedSelector = s; break; }}
  }}
  if (!el) return {{ ok: false, reason: 'no_composer' }};
  if (String(el.getAttribute('contenteditable') || '') !== 'true') {{
    return {{ ok: false, reason: 'no_composer' }};
  }}
  window.__ulgChatgptWorkComposer = {{ matchedSelector, composer: el }};
  const href = String(location.href || '');
  const m = href.match(/\\/c\\/([0-9a-fA-F-]{{36}})/);
  const pageCid = m ? m[1].toLowerCase() : '';
  if (expectedCid && pageCid !== expectedCid) {{
    return {{ ok: false, reason: 'conversation_mismatch', page_cid: pageCid }};
  }}
  const g = window.__ulgOperatorActivity = window.__ulgOperatorActivity || {{}};
  const now = Date.now();
  const last = Number(g.lastInputMs || 0);
  if (last > 0 && (now - last) < 10000) {{
    return {{ ok: false, reason: 'operator_active' }};
  }}
  if (el.isContentEditable && document.getSelection) {{
    const sel = document.getSelection();
    if (sel && sel.isCollapsed === false) {{
      return {{ ok: false, reason: 'composer_selection_active' }};
    }}
  }}
  const region =
    el.closest('[data-testid="composer"]') ||
    el.closest('form') ||
    el.parentElement?.parentElement ||
    el.parentElement ||
    el;
  const attachSels = [
    '[data-testid*="attachment" i]',
    '[class*="attachment" i]',
    '[data-testid*="file-chip" i]',
    '[data-testid*="uploaded" i]',
  ];
  for (const sel of attachSels) {{
    for (const node of region.querySelectorAll(sel)) {{
      if (!node || node.nodeType !== 1) continue;
      if (node.matches('input[type="file"]')) continue;
      const tag = String(node.tagName || '').toUpperCase();
      if (tag === 'INPUT' && String(node.getAttribute('type') || '').toLowerCase() === 'file') continue;
      const label = (node.getAttribute('aria-label') || node.innerText || '').toLowerCase();
      if ((tag === 'BUTTON' || node.getAttribute('role') === 'button') &&
          (label.includes('attach') || label.includes('voice'))) continue;
      return {{ ok: false, reason: 'composer_not_empty' }};
    }}
  }}
  if (el.querySelector('atom,img,svg,a[href],[contenteditable="false"]')) {{
    return {{ ok: false, reason: 'composer_not_empty' }};
  }}
  const draft = (el.innerText || el.value || '').trim();
  const sig = String(g.ourDraftSig || '').trim();
  if (draft && draft === line) {{
    if (sig !== line) {{
      return {{ ok: false, reason: 'composer_not_owned' }};
    }}
    return {{ ok: true, state: 'already_inserted', matchedSelector }};
  }}
  if (draft.length > 0) {{
    return {{ ok: false, reason: 'composer_not_empty' }};
  }}
  let visibleText = '';
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) visibleText += node.textContent || '';
  if (/[\\u00a0\\u200b\\u200c\\u200d\\ufeff\\t]/.test(visibleText)) {{
    return {{ ok: false, reason: 'composer_not_empty' }};
  }}
  if (/[^\\s\\n\\r]/.test(visibleText)) {{
    return {{ ok: false, reason: 'composer_not_empty' }};
  }}
  const blocks = Array.from(el.children).filter((c) => {{
    const tg = c.tagName;
    return tg === 'P' || tg === 'DIV' || /^H[1-6]$/.test(tg);
  }});
  if (blocks.length !== 1) {{
    return {{ ok: false, reason: 'composer_not_empty' }};
  }}
  const block = blocks[0];
  const elementKids = Array.from(block.childNodes).filter((n) => n.nodeType === 1);
  const textKids = Array.from(block.childNodes).filter(
    (n) => n.nodeType === 3 && (n.textContent || '').replace(/[\\s\\n\\r]/g, '').length > 0
  );
  if (textKids.length > 0) {{
    return {{ ok: false, reason: 'composer_not_empty' }};
  }}
  if (elementKids.length === 0) {{
    return {{ ok: true, state: 'empty', matchedSelector }};
  }}
  if (elementKids.length === 1 && elementKids[0].tagName === 'BR') {{
    return {{ ok: true, state: 'empty', matchedSelector }};
  }}
  return {{ ok: false, reason: 'composer_not_empty' }};
}})()
"""


def chatgpt_work_pre_insert_expression(
    *,
    line: str,
    preserve_newlines: bool,
    expected_conversation_id: str = "",
) -> str:
    lj = json.dumps(_normalized_line(line, preserve_newlines=preserve_newlines))
    exp_cid = json.dumps((expected_conversation_id or "").strip().lower())
    return f"""
(() => {{
  const line = {lj};
  const expectedCid = {exp_cid};
  const stash = window.__ulgChatgptWorkComposer;
  const el = stash && stash.composer;
  if (!el) return {{ ok: false, reason: 'no_composer' }};
  const href = String(location.href || '');
  const m = href.match(/\\/c\\/([0-9a-fA-F-]{{36}})/);
  const pageCid = m ? m[1].toLowerCase() : '';
  if (expectedCid && pageCid !== expectedCid) {{
    return {{ ok: false, reason: 'conversation_mismatch', page_cid: pageCid }};
  }}
  const g = window.__ulgOperatorActivity = window.__ulgOperatorActivity || {{}};
  const now = Date.now();
  const last = Number(g.lastInputMs || 0);
  if (last > 0 && (now - last) < 10000) {{
    return {{ ok: false, reason: 'operator_active' }};
  }}
  if (el.isContentEditable && document.getSelection) {{
    const sel = document.getSelection();
    if (sel && sel.isCollapsed === false) {{
      return {{ ok: false, reason: 'composer_selection_active' }};
    }}
  }}
  const draft = (el.innerText || el.value || '').trim();
  if (draft.length > 0) {{
    return {{ ok: false, reason: 'composer_not_empty' }};
  }}
  const active = document.activeElement;
  const focused = active === el || el.contains(active);
  if (!focused) {{
    const tag = active && active.tagName ? String(active.tagName).toUpperCase() : '';
    const otherText =
      active &&
      active !== document.body &&
      tag !== 'BODY' &&
      (active.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA');
    if (otherText) {{
      return {{ ok: false, reason: 'composer_not_focused_other_control' }};
    }}
    el.focus();
  }}
  const after = document.activeElement;
  if (!(after === el || el.contains(after))) {{
    return {{ ok: false, reason: 'composer_not_focused' }};
  }}
  return {{ ok: true, matchedSelector: stash.matchedSelector || '' }};
}})()
"""


def chatgpt_work_readback_expression_with_attempt(
    *, line: str, preserve_newlines: bool, attempt_id: str
) -> str:
    lj = json.dumps(_normalized_line(line, preserve_newlines=preserve_newlines))
    attempt = json.dumps(str(attempt_id or ""))
    return f"""
(() => {{
  const line = {lj};
  const attemptId = {attempt};
  const el = window.__ulgChatgptWorkComposer && window.__ulgChatgptWorkComposer.composer;
  if (!el) return {{ ok: false, reason: 'no_composer' }};
  const text = (el.innerText || el.value || '').trim();
  if (text !== line) {{
    return {{ ok: false, reason: 'insert_readback_mismatch' }};
  }}
  const g = window.__ulgOperatorActivity = window.__ulgOperatorActivity || {{}};
  g.ourDraftSig = line;
  if (attemptId) g.ourDraftWriter = attemptId;
  return {{ ok: true, text }};
}})()
"""


async def _cdp_response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg


async def _runtime_eval_ws(
    ws: Any, mid: int, expression: str, *, await_promise: bool = False
) -> Any:
    await ws.send(
        json.dumps(
            {
                "id": mid,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "awaitPromise": await_promise,
                    "returnByValue": True,
                },
            }
        )
    )
    raw = await _cdp_response(ws, mid)
    if raw.get("error"):
        return {
            "ok": False,
            "reason": "cdp_protocol_error",
            "protocol_error": raw.get("error"),
        }
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def _input_insert_text_ws(ws: Any, mid: int, text: str) -> dict[str, Any]:
    await ws.send(
        json.dumps({"id": mid, "method": _INSERT_METHOD, "params": {"text": text}})
    )
    raw = await _cdp_response(ws, mid)
    if raw.get("error"):
        return {
            "ok": False,
            "reason": "cdp_protocol_error",
            "protocol_error": raw.get("error"),
            "insert_attempted": True,
            "retryable": False,
        }
    return {"ok": True}


def _unknown_insert_outcome() -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "insert_outcome_unknown",
        "insert_attempted": True,
        "retryable": False,
    }


async def chatgpt_work_insert_and_send(
    ws_url: str,
    *,
    selector: str,
    line: str,
    preserve_newlines: bool = False,
    expected_conversation_id: str = "",
    expected_target_id: str = "",
    attempt_id: str = "",
) -> dict[str, Any]:
    """Single websocket: preflight, optional one Input.insertText, readback, dom send."""
    del expected_target_id  # identity enforced by caller tab match before lock
    import websockets

    insert_attempted = False
    owned_replay = False
    matched_selector = selector

    try:
        async with websockets.connect(ws_url, max_size=2_000_000) as ws:
            await _runtime_eval_ws(ws, 1, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
            pre = await _runtime_eval_ws(
                ws,
                2,
                chatgpt_work_preflight_expression(
                    selector=selector,
                    line=line,
                    preserve_newlines=preserve_newlines,
                    expected_conversation_id=expected_conversation_id,
                    attempt_id=attempt_id,
                ),
            )
            if not isinstance(pre, dict):
                return {"ok": False, "reason": "cdp_eval_failed"}
            if pre.get("protocol_error"):
                return pre
            if not pre.get("ok"):
                return pre
            matched_selector = str(pre.get("matchedSelector") or selector)
            need_insert = pre.get("state") != "already_inserted"
            owned_replay = pre.get("state") == "already_inserted"

            if need_insert:
                before = await _runtime_eval_ws(
                    ws,
                    3,
                    chatgpt_work_pre_insert_expression(
                        line=line,
                        preserve_newlines=preserve_newlines,
                        expected_conversation_id=expected_conversation_id,
                    ),
                )
                if not isinstance(before, dict) or not before.get("ok"):
                    reason = "composer_not_focused"
                    if isinstance(before, dict) and before.get("reason"):
                        reason = str(before["reason"])
                    return {"ok": False, "reason": reason}

                await _runtime_eval_ws(ws, 4, operator_activity_suppress_js())
                insert_attempted = True
                try:
                    ins = await _input_insert_text_ws(ws, 5, line)
                finally:
                    await _runtime_eval_ws(ws, 6, operator_activity_clear_suppress_js())
                if not ins.get("ok"):
                    ins.setdefault("insert_attempted", True)
                    ins.setdefault("retryable", False)
                    return ins

            readback = await _runtime_eval_ws(
                ws,
                7,
                chatgpt_work_readback_expression_with_attempt(
                    line=line,
                    preserve_newlines=preserve_newlines,
                    attempt_id=attempt_id,
                ),
            )
            if not isinstance(readback, dict) or not readback.get("ok"):
                if insert_attempted:
                    return _unknown_insert_outcome()
                reason = "insert_readback_mismatch"
                if isinstance(readback, dict) and readback.get("reason"):
                    reason = str(readback["reason"])
                return {"ok": False, "reason": reason}

            setattr(ws, "_ulg_cdp_seq", 7)

            try:
                send = await dom_click_send(
                    ws_url,
                    selector=matched_selector,
                    line=line,
                    preserve_newlines=preserve_newlines,
                    ws=ws,
                )
            except Exception:
                if insert_attempted or owned_replay:
                    return _unknown_insert_outcome()
                raise
            if not send.get("ok"):
                reason = str(send.get("reason") or "send_failed")
                owned_line = insert_attempted or owned_replay
                if reason == "no_send_button" and owned_line:
                    reason = "send_button_disabled"
                out: dict[str, Any] = {
                    "ok": False,
                    "reason": reason,
                    "send_fallback_clicked": False,
                }
                if owned_line:
                    out["insert_attempted"] = True
                if reason == "send_button_disabled":
                    out["retryable"] = True
                    out["owned_draft_retained"] = True
                if send.get("census") is not None:
                    out["census"] = send["census"]
                if send.get("clicked_unknown"):
                    out["clicked_unknown"] = True
                    out["retryable"] = False
                elif owned_line and reason not in {"send_button_disabled"}:
                    out.setdefault("retryable", False)
                return out
            return {"ok": True, "send_fallback_clicked": bool(send.get("send_clicked"))}
    except (TimeoutError, asyncio.CancelledError):
        if insert_attempted:
            return _unknown_insert_outcome()
        raise
    except Exception:
        if insert_attempted:
            return _unknown_insert_outcome()
        raise


# Backward-compatible exports for tests that evaluate JS in isolation
def chatgpt_work_composer_state_expression(
    selector: str, *, line: str, preserve_newlines: bool
) -> str:
    return chatgpt_work_preflight_expression(
        selector=selector,
        line=line,
        preserve_newlines=preserve_newlines,
        expected_conversation_id="",
    )


__all__ = [
    "chatgpt_work_composer_state_expression",
    "chatgpt_work_insert_and_send",
    "chatgpt_work_preflight_expression",
    "chatgpt_work_pre_insert_expression",
    "chatgpt_work_readback_expression_with_attempt",
]
