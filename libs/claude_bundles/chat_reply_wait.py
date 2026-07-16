"""Fast completion detection for claude.ai assistant replies via CDP harvest.

Friction notes (keep when generalizing):
- Poll div[class*="font-claude"] or assistant-message nodes, NOT project header.
- Always pass ``before`` from pre-send harvest on follow-up turns.
- 500ms poll + stable length x2 beats 1s regex-marker waits.
- Do not treat completion as done until message count or body length grows.
- Fable bind 4917: ¬error_banner ∧ turn_count_incremented ∧ ¬tool_pause.
- Friction 24666: ``timeout_s`` is an *idle* budget. While Stop / streaming /
  tool_pause is present the idle clock pauses — no wall ceiling (long Cowork
  tool-runs may run arbitrarily long). Idle with no completion still raises.
"""

from __future__ import annotations

import asyncio
import time

HARVEST_JS = """
({ minMsgChars }) => {
  const msgs = [];
  for (const el of document.querySelectorAll(
    '[data-testid="assistant-message"], [data-testid="assistant-turn"], div[class*="font-claude"]'
  )) {
    const t = (el.innerText || '').trim();
    if (t.length > minMsgChars) msgs.push(t);
  }
  const body = msgs.length ? msgs[msgs.length - 1] : '';
  const streaming = !!document.querySelector(
    '[data-is-streaming="true"], [data-is-streaming=true]'
  );
  const stop = [...document.querySelectorAll('button,[role=button]')].some(
    (b) => /\\bstop\\b/i.test(
      (b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')
    )
  );
  const pageText = (document.body && document.body.innerText) || '';
  // Check head+tail — banners may prepend (falsifier) or toast at bottom.
  const scan = pageText.slice(0, 4000) + pageText.slice(-4000);
  const errorBanner = /hit a limit|rate limit|something went wrong|network error|try again later|usage limit|overloaded/i.test(
    scan
  );
  const toolPause = !!document.querySelector(
    '[data-testid*="tool"], [data-testid*="research"], [aria-label*="Searching" i]'
  ) && streaming;
  const modelEl = document.querySelector(
    '[data-testid="model-selector-dropdown"], [data-testid*="model"]'
  );
  const modelLabel = modelEl
    ? (modelEl.getAttribute('aria-label') || modelEl.innerText || '').trim()
    : '';
  return {
    url: location.href,
    body,
    body_len: body.length,
    n: msgs.length,
    streaming,
    stop,
    error_banner: errorBanner,
    tool_pause: toolPause,
    model_label: modelLabel,
  };
}
"""


async def harvest_assistant(page, *, min_msg_chars: int = 40) -> dict:
    return await page.evaluate(HARVEST_JS, {"minMsgChars": min_msg_chars})


class HarvestIncomplete(RuntimeError):
    """Turn did not satisfy complete(turn) — caller must ¬delete."""


def _in_flight(state: dict) -> bool:
    """Cowork/tool liveness — Stop / streaming / tool_pause pause the idle clock."""
    return bool(
        state.get("streaming") or state.get("stop") or state.get("tool_pause")
    )


def _complete_enough(
    state: dict,
    *,
    base_len: int,
    base_n: int,
    min_growth: int,
    min_body: int,
) -> bool:
    cur_len = state.get("body_len", 0)
    cur_n = state.get("n", 0)
    grew = cur_len > base_len + min_growth or cur_n > base_n
    return bool(
        grew
        and cur_len >= min_body
        and cur_n > base_n
        and not _in_flight(state)
    )


async def wait_assistant_reply(
    page,
    *,
    before: dict | None = None,
    timeout_s: int = 360,
    poll_ms: int = 500,
    min_growth: int = 200,
    stable_polls: int = 2,
    min_body: int = 400,
    min_msg_chars: int | None = None,
) -> dict:
    """Wait until complete(turn) or idle timeout.

    ``timeout_s`` is idle wall-time without in-flight signals. While Stop,
    streaming, or tool_pause is observed the idle deadline is refreshed — there
    is no hard wall ceiling (friction 24666).
    """
    msg_floor = min_msg_chars if min_msg_chars is not None else max(10, min(40, min_body))
    base_len = (before or {}).get("body_len", 0)
    base_n = (before or {}).get("n", 0)
    stable = 0
    last_len = -1
    idle_deadline = time.monotonic() + max(timeout_s, 1)

    while True:
        state = await harvest_assistant(page, min_msg_chars=msg_floor)
        if state.get("error_banner"):
            raise HarvestIncomplete(
                f"error_banner detected url={state.get('url')} len={state.get('body_len')}"
            )
        cur_len = state.get("body_len", 0)
        cur_n = state.get("n", 0)
        in_flight = _in_flight(state)

        if in_flight:
            # Pause idle clock: refresh full idle budget from now.
            idle_deadline = time.monotonic() + max(timeout_s, 1)
            stable = 0
        else:
            grew = cur_len > base_len + min_growth or cur_n > base_n
            if grew and cur_len >= min_body and cur_n > base_n:
                if cur_len == last_len:
                    stable += 1
                else:
                    stable = 0
                last_len = cur_len
                if stable >= stable_polls:
                    return state

        if not in_flight and time.monotonic() >= idle_deadline:
            break
        await asyncio.sleep(poll_ms / 1000)

    state = await harvest_assistant(page, min_msg_chars=msg_floor)
    if state.get("error_banner"):
        raise HarvestIncomplete(
            f"error_banner on timeout url={state.get('url')}"
        )
    if _complete_enough(
        state,
        base_len=base_len,
        base_n=base_n,
        min_growth=min_growth,
        min_body=min_body,
    ):
        return state
    raise HarvestIncomplete(
        f"timed out incomplete (base_len={base_len}, last={state.get('body_len')}, "
        f"n={state.get('n')}) — ¬delete"
    )
