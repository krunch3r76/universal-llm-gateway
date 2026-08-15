"""Sync CDP liveness probe for Cowork CSE pages — no Playwright session required."""

from __future__ import annotations

import json
from typing import Any

_PROBE_TIMEOUT_S = 2.0

# Harvest triple only — matches ``chat_reply_wait.HARVEST_JS`` / ``page_idle_from_state``.
CSE_LIVENESS_PROBE_JS = """
(() => {
  const streaming = !!document.querySelector(
    '[data-is-streaming="true"], [data-is-streaming=true]'
  );
  const isGenerationStopControl = (btn) => {
    const aria = (btn.getAttribute('aria-label') || '').trim();
    const text = (btn.innerText || '').trim();
    return (
      /^stop(\\s+(generating|response|generating response))?$/i.test(aria) ||
      /^stop(\\s+(generating|response|generating response))?$/i.test(text)
    );
  };
  const generationStopRoots = () => {
    const roots = [];
    const seen = new Set();
    const add = (el) => {
      if (el && !seen.has(el)) {
        seen.add(el);
        roots.push(el);
      }
    };
    add(document.querySelector('main'));
    add(document.querySelector('[role="main"]'));
    const chatInput = document.querySelector('[data-testid="chat-input"]');
    if (chatInput) {
      add(chatInput.closest('main'));
      add(chatInput.closest('form'));
      add(chatInput.closest('[class*="composer" i]'));
      add(chatInput.closest('[class*="Composer" i]'));
    }
    return roots;
  };
  let stop = false;
  for (const root of generationStopRoots()) {
    for (const b of root.querySelectorAll('button,[role=button]')) {
      if (isGenerationStopControl(b)) {
        stop = true;
        break;
      }
    }
    if (stop) break;
  }
  const toolPause = !!document.querySelector(
    '[data-testid*="tool"], [data-testid*="research"], [aria-label*="Searching" i]'
  ) && streaming;
  return { streaming, stop, tool_pause: toolPause, url: location.href || '' };
})()
"""


def page_idle_from_harvest_state(state: dict[str, Any]) -> bool:
    """Derive idle from harvest triple — any active signal means not idle."""
    return not (
        state.get("streaming")
        or state.get("stop")
        or state.get("tool_pause")
    )


def in_flight_from_state(state: dict[str, Any]) -> bool:
    """Cowork liveness — Stop / streaming / tool_pause means not idle."""
    return not page_idle_from_harvest_state(state)


def probe_page_liveness_sync(
    port: int,
    websocket_url: str,
    *,
    timeout_s: float = _PROBE_TIMEOUT_S,
) -> tuple[dict[str, Any] | None, bool]:
    """Evaluate the harvest triple on one page target; returns (state, probe_ok)."""
    import websocket

    try:
        conn = websocket.create_connection(
            websocket_url,
            timeout=timeout_s,
            header=[f"Origin: http://127.0.0.1:{port}"],
        )
    except Exception:
        return None, False
    message: dict[str, Any] = {}
    try:
        conn.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": CSE_LIVENESS_PROBE_JS,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            message = json.loads(conn.recv())
            if message.get("id") == 1:
                break
    except Exception:
        return None, False
    finally:
        conn.close()
    if "error" in message:
        return None, False
    result = message.get("result", {}).get("result", {})
    value = result.get("value")
    if not isinstance(value, dict):
        return None, False
    return value, True
