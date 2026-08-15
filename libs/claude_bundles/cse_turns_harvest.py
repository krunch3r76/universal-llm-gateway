"""Ordered multi-turn CSE DOM harvest — bounded read-only extraction."""

from __future__ import annotations

from typing import Any

from claude_bundles.chat_reply_wait import _in_flight
from claude_bundles.project_ask import strip_thinking_prefix

CSE_TURNS_JS = """
({ limit, afterTurn }) => {
  const url = location.href || '';
  const isCoworkCse = /\\/cowork\\/cse_/.test(url);
  const baseSelectors = [
    '[data-testid="assistant-message"]',
    '[data-testid="assistant-turn"]',
    'div[class*="font-claude"]',
  ];
  const coworkSelectors = isCoworkCse
    ? [
        '[data-testid*="assistant"]',
        '[class*="AssistantMessage"]',
        'article[class*="message"]',
        '[role="article"]',
      ]
    : [];
  const seen = new Set();
  const turns = [];
  let ordinal = 0;
  for (const sel of [...baseSelectors, ...coworkSelectors]) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const t = (el.innerText || '').trim();
      if (/^You said:\\s*/i.test(t)) continue;
      if (t.length < 1) continue;
      ordinal += 1;
      if (afterTurn !== null && afterTurn !== undefined && ordinal <= afterTurn) continue;
      turns.push({
        author: 'assistant',
        timestamp: null,
        text: t,
        ordinal,
      });
      if (turns.length >= limit) break;
    }
    if (turns.length >= limit) break;
  }
  const streaming = !!document.querySelector(
    'button[aria-label*="Stop" i], button[data-testid*="stop" i]'
  );
  const toolPause = !!document.querySelector('[data-testid*="tool" i][class*="pause" i]');
  const stop = streaming;
  const incomplete = turns.length === 0 && !streaming;
  return {
    turns,
    streaming,
    stop,
    tool_pause: toolPause,
    incomplete_dom: incomplete,
    truncated: ordinal > (afterTurn || 0) + limit,
  };
}
"""


async def harvest_turns(
    page,
    *,
    limit: int = 10,
    after_turn: int | None = None,
) -> dict[str, Any]:
    """Evaluate ``CSE_TURNS_JS`` and normalize assistant turn bodies."""
    raw = await page.evaluate(
        CSE_TURNS_JS,
        {"limit": limit, "afterTurn": after_turn},
    )
    turns = []
    for row in raw.get("turns") or []:
        text = strip_thinking_prefix(str(row.get("text") or ""))
        turns.append(
            {
                "author": str(row.get("author") or "assistant"),
                "timestamp": row.get("timestamp"),
                "text": text,
                "ordinal": row.get("ordinal"),
            }
        )
    raw["turns"] = turns
    raw["in_flight"] = _in_flight(raw)
    return raw
