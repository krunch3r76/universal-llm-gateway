"""Runtime.evaluate witness for Cursor Agents composer selection (B5)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

_PROMPT_SELECTOR = '[aria-label="Prompt"][contenteditable="true"]'


def composer_witness_expression(selector: str) -> str:
    """Return JS that witnesses one composer selector on the active page.

    The expression must not call Input.*. ``selector`` is embedded as JSON.
    """
    sel = json.dumps(selector)
    prompt = json.dumps(_PROMPT_SELECTOR)
    body = """
(() => {
  const selector = __SEL__;
  const promptSel = __PROMPT__;
  const nodes = document.querySelectorAll(selector);
  const match_count = nodes.length;
  const promptNode = document.querySelector(promptSel);
  let attributes = null;
  let outer_html = null;
  let inner_text_raw = null;
  let inner_text_trimmed = null;
  let prompt_selector_matches_same_node = false;
  let composer_holds_user_draft = false;
  let resolved_selector = null;
  if (match_count === 1) {
    const el = nodes[0];
    attributes = {};
    for (const attr of el.attributes || []) {
      attributes[attr.name] = attr.value;
    }
    outer_html = el.outerHTML;
    inner_text_raw = el.innerText;
    inner_text_trimmed = (el.innerText || '').trim();
    prompt_selector_matches_same_node = el === promptNode;
    const ph = (el.getAttribute('data-placeholder') || el.getAttribute('aria-placeholder') || '').trim();
    composer_holds_user_draft =
      inner_text_trimmed.length > 0 && inner_text_trimmed !== ph;
    if (prompt_selector_matches_same_node) {
      resolved_selector = promptSel;
    } else {
      const label = (el.getAttribute('aria-label') || '').trim();
      if (label) {
        const built = '[aria-label="' + label.replace(/"/g, '\\"') + '"][contenteditable="true"]';
        if (document.querySelectorAll(built).length === 1) {
          resolved_selector = built;
        }
      }
    }
  }
  return {
    match_count,
    attributes,
    outer_html,
    inner_text_raw,
    inner_text_trimmed,
    prompt_selector_matches_same_node,
    composer_holds_user_draft,
    resolved_selector,
  };
})()
""".strip()
    return body.replace("__SEL__", sel).replace("__PROMPT__", prompt)


async def evaluate_composer_witness(ws_url: str, selector: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
    """Evaluate ``composer_witness_expression`` on one debug websocket."""
    import websockets

    expr = composer_witness_expression(selector)
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expr, "returnByValue": True},
                }
            )
        )
        raw = await asyncio.wait_for(_response(ws, 1), timeout_s)
    value = ((raw.get("result") or {}).get("result") or {}).get("value")
    if not isinstance(value, dict):
        detail = (raw.get("result") or {}).get("exceptionDetails")
        raise RuntimeError(f"composer witness failed: {detail or raw}")
    return value


async def _response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg
