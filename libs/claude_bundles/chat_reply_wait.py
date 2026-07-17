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
- Friction 24864: Cowork ``/cowork/cse_`` URLs extend harvest selectors (A5) and
  add a URL-guarded fallback with positive new-turn guard (body growth OR
  working→idle transition) — never idle-on-stale-content alone.
- Friction 24873: ``stop`` scoped to generation/composer subtree only — sidebar
  thread menus must not match (R-amendment: structural scope, not blocklist).
"""

from __future__ import annotations

import asyncio
import time

HARVEST_JS = """
({ minMsgChars }) => {
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
  const msgs = [];
  for (const sel of [...baseSelectors, ...coworkSelectors]) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const t = (el.innerText || '').trim();
      if (t.length > minMsgChars) msgs.push(t);
    }
  }
  const body = msgs.length ? msgs[msgs.length - 1] : '';
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
    const msg = document.querySelector(
      '[data-testid="assistant-message"], [data-testid*="assistant"]'
    );
    if (msg) {
      add(msg.closest('main'));
      add(msg.closest('[role="main"]'));
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
  const pageText = (document.body && document.body.innerText) || '';
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
  const taskMapSteps = [
    ...document.querySelectorAll(
      '[data-testid*="step" i],[class*="task" i] li,[role="listitem"]'
    ),
  ]
    .map((e) => (e.innerText || '').trim())
    .filter(Boolean)
    .slice(0, 40);
  const taskMapPresent = taskMapSteps.length > 0;
  const taskMapWorking =
    /working through/i.test(pageText) ||
    !!document.querySelector(
      '[class*="spinner" i],[aria-busy="true"],[data-testid*="progress" i]'
    );
  const taskMapIdle = taskMapPresent ? !taskMapWorking : false;
  return {
    url,
    cowork_cse: isCoworkCse,
    body,
    body_len: body.length,
    n: msgs.length,
    streaming,
    stop,
    error_banner: errorBanner,
    tool_pause: toolPause,
    model_label: modelLabel,
    task_map_present: taskMapPresent,
    task_map_working: taskMapWorking,
    task_map_idle: taskMapIdle,
  };
}
"""


async def harvest_assistant(page, *, min_msg_chars: int = 40) -> dict:
    return await page.evaluate(HARVEST_JS, {"minMsgChars": min_msg_chars})


class HarvestIncomplete(RuntimeError):
    """Turn did not satisfy complete(turn) — caller must ¬delete."""


def _is_cowork_cse_url(url: str) -> bool:
    return "/cowork/cse_" in (url or "")


def _in_flight(state: dict) -> bool:
    """Cowork/tool liveness — Stop / streaming / tool_pause pause the idle clock.

    ``streaming`` is the defense-in-depth backstop when ``stop`` is momentarily
    false during generation (24873 R-amendment).
    """
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


def _cowork_complete_enough(
    state: dict,
    *,
    base_len: int,
    base_n: int,
    min_growth: int,
    min_body: int,
    saw_working: bool,
) -> bool:
    """URL-guarded Cowork fallback (24864) with positive new-turn guard.

    Global gate ``cur_n > base_n`` is preserved on Chat paths via
    ``_complete_enough``. This fallback is an explicit Cowork-only narrowing:
    requires body growth OR (observed working→idle transition with growth).
    """
    if not _is_cowork_cse_url(state.get("url", "")):
        return False
    if state.get("error_banner") or _in_flight(state):
        return False
    cur_len = state.get("body_len", 0)
    cur_n = state.get("n", 0)
    if cur_len < min_body:
        return False

    grew_n = cur_n > base_n
    grew_len = cur_len > base_len + min_growth or cur_len > base_len
    working_to_idle = (
        saw_working
        and state.get("task_map_present")
        and state.get("task_map_idle")
        and not state.get("task_map_working")
        and cur_len > base_len
    )
    return bool(grew_n or grew_len or working_to_idle)


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
    cowork_stable = 0
    last_len = -1
    saw_working = False
    idle_deadline = time.monotonic() + max(timeout_s, 1)

    while True:
        state = await harvest_assistant(page, min_msg_chars=msg_floor)
        if state.get("error_banner"):
            raise HarvestIncomplete(
                f"error_banner detected url={state.get('url')} len={state.get('body_len')}"
            )
        cur_len = state.get("body_len", 0)
        in_flight = _in_flight(state)

        if state.get("task_map_working"):
            saw_working = True

        if in_flight:
            idle_deadline = time.monotonic() + max(timeout_s, 1)
            stable = 0
            cowork_stable = 0
        else:
            cur_n = state.get("n", 0)
            grew = cur_len > base_len + min_growth or cur_n > base_n
            if grew and cur_len >= min_body and cur_n > base_n:
                if cur_len == last_len:
                    stable += 1
                else:
                    stable = 0
                last_len = cur_len
                if stable >= stable_polls:
                    return state

            if _cowork_complete_enough(
                state,
                base_len=base_len,
                base_n=base_n,
                min_growth=min_growth,
                min_body=min_body,
                saw_working=saw_working,
            ):
                if cur_len == last_len:
                    cowork_stable += 1
                else:
                    cowork_stable = 0
                last_len = cur_len
                if cowork_stable >= stable_polls:
                    return state
            else:
                cowork_stable = 0

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
    if _cowork_complete_enough(
        state,
        base_len=base_len,
        base_n=base_n,
        min_growth=min_growth,
        min_body=min_body,
        saw_working=saw_working,
    ):
        return state
    raise HarvestIncomplete(
        f"timed out incomplete (base_len={base_len}, last={state.get('body_len')}, "
        f"n={state.get('n')}) — ¬delete"
    )
