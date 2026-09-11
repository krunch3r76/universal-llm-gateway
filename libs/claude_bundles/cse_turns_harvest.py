"""Ordered multi-turn CSE DOM harvest — bounded read-only extraction."""

from __future__ import annotations

from typing import Any

from claude_bundles.chat_reply_wait import _in_flight
from claude_bundles.project_ask import strip_thinking_prefix

CSE_TURNS_JS = """
async ({ limit, afterTurn }) => {
  const url = location.href || '';
  const title = document.title || '';
  const streaming = !!document.querySelector(
    'button[aria-label*="Stop" i], button[data-testid*="stop" i]'
  );
  const toolPause = !!document.querySelector('[data-testid*="tool" i][class*="pause" i]');
  const stop = streaming;
  const spinner = !!document.querySelector(
    '[class*="spinner" i], [class*="loading" i], svg[class*="animate" i]'
  );
  const ariaBusy = !!document.querySelector('[aria-busy="true"]');

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const rows = document.querySelectorAll('[data-testid="transcript-row"]');
  if (rows.length === 0) {
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
    const firstAuthor = turns.length ? turns[0].author : null;
    return {
      title,
      url,
      turns,
      coverage: 'tail',
      scroll_iterations: 0,
      first_row_author: firstAuthor,
      streaming,
      stop,
      tool_pause: toolPause,
      spinner,
      aria_busy: ariaBusy,
      truncated: ordinal > (afterTurn || 0) + limit,
    };
  }

  const listEl = document.querySelector('[data-testid="transcript-list"]');
  let scroller = null;
  if (listEl && rows[0]) {
    let el = rows[0];
    while (el) {
      const style = window.getComputedStyle(el);
      const oy = style.overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.contains(listEl)) {
        scroller = el;
        break;
      }
      el = el.parentElement;
    }
  }

  let scrollIterations = 0;
  let stable = false;
  let savedScrollTop = 0;
  if (scroller) {
    savedScrollTop = scroller.scrollTop;
    let stableRounds = 0;
    let prevCount = -1;
    for (let round = 0; round < 20; round++) {
      scroller.scrollTop = 0;
      await sleep(300);
      scrollIterations = round + 1;
      const count = document.querySelectorAll('[data-testid="transcript-row"]').length;
      if (count === prevCount) {
        stableRounds += 1;
        if (stableRounds >= 3) {
          stable = true;
          break;
        }
      } else {
        stableRounds = 0;
      }
      prevCount = count;
    }
  }

  const allRows = Array.from(document.querySelectorAll('[data-testid="transcript-row"]'));
  const rawTurns = [];
  let ordinal = 0;
  let firstRowAuthor = null;
  for (const row of allRows) {
    let author = null;
    let bodyEl = null;
    if (row.querySelector('[data-testid="user-message"]')) {
      author = 'user';
      bodyEl = row.querySelector('[data-testid="user-message"]');
    } else {
      bodyEl = row.querySelector('div.font-claude-response, div[class*="font-claude"]');
      if (bodyEl) author = 'assistant';
    }
    if (!author || !bodyEl) continue;
    if (firstRowAuthor === null) firstRowAuthor = author;
    const text = (bodyEl.innerText || '').trim();
    if (/^You said:\\s*/i.test(text)) continue;
    if (text.length < 1) continue;
    const timeEl = row.querySelector('time[datetime]');
    const timestamp = timeEl ? timeEl.getAttribute('datetime') : null;
    ordinal += 1;
    if (afterTurn !== null && afterTurn !== undefined && ordinal <= afterTurn) continue;
    rawTurns.push({ author, timestamp, text, ordinal });
  }

  const turns = rawTurns.slice(-limit);
  const truncated = rawTurns.length > limit;
  const hasUser = rawTurns.some((t) => t.author === 'user');
  const atTop = scroller ? scroller.scrollTop === 0 : false;
  let coverage = 'tail';
  if (hasUser && stable && atTop && !streaming && !ariaBusy) {
    coverage = 'full';
  }

  if (scroller) {
    scroller.scrollTop = savedScrollTop;
  }

  return {
    title,
    url,
    turns,
    coverage,
    scroll_iterations: scrollIterations,
    first_row_author: firstRowAuthor,
    streaming,
    stop,
    tool_pause: toolPause,
    spinner,
    aria_busy: ariaBusy,
    truncated,
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
        text = str(row.get("text") or "")
        author = str(row.get("author") or "assistant")
        if author == "assistant":
            text = strip_thinking_prefix(text)
        turns.append(
            {
                "author": author,
                "timestamp": row.get("timestamp"),
                "text": text,
                "ordinal": row.get("ordinal"),
            }
        )
    raw["turns"] = turns
    raw["in_flight"] = _in_flight(raw)
    raw.pop("incomplete_dom", None)
    return raw
