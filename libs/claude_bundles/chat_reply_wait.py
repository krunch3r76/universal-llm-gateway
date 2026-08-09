"""Fast completion detection for claude.ai assistant replies via CDP harvest.

Friction notes (keep when generalizing):
- Poll div[class*="font-claude"] or assistant-message nodes, NOT project header.
- Always pass ``before`` from pre-send harvest on follow-up turns.
- 500ms poll + stable length x2 beats 1s regex-marker waits.
- Do not treat completion as done until a new assistant turn is harvested (n grew).
- Fable bind 4917: ¬error_banner ∧ turn_count_incremented ∧ ¬tool_pause.
- Friction 25654: error_banner match text must surface; raise only when
  banner ∧ ¬in_flight (transient "Overloaded" while Stop/streaming must wait).
- Friction 25684: lingering Overloaded (delay overlay) after turn landed must
  ¬ block structural completion — banner ∧ ¬in_flight ∧ new turn ⇒ complete;
  fail-closed on banner only when the turn never completed.
- Friction 25486: error_banner scan scoped to banner/toast/alert nodes only —
  composer/chat-input text must not false-fire the banner regex.
- Completion is **structural** (new turn + idle + stable), not min_body/min_growth
  length gates — short replies are valid harvest products (operator bind 2026-07-18).
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
from collections.abc import Awaitable, Callable

from cdp_ask.structural_quiet import StructuralQuietTracker

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
      // Temp (a:27801): Cowork [role=article] matches user turns ("You said:…").
      // Skip those so wait keeps polling until a real assistant body appears.
      if (/^You said:\\s*/i.test(t)) continue;
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
  const errorBannerRe =
    /hit a limit|hit your .+ limit|weekly limit|rate limit|something went wrong|network error|try again later|usage limit|overloaded/i;
  const isInsideComposer = (el) => {
    if (!el) return false;
    if (el.closest('[data-testid="chat-input"]')) return true;
    if (el.closest('[class*="composer" i]')) return true;
    return false;
  };
  const bannerSelectors = [
    '[role="alert"]',
    '[role="status"]',
    '[class*="toast" i]',
    '[class*="banner" i]',
    '[data-testid*="toast" i]',
    '[data-testid*="banner" i]',
    '[data-testid*="alert" i]',
    '[data-testid*="error" i]',
  ];
  const bannerSeen = new Set();
  const bannerTexts = [];
  for (const sel of bannerSelectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (bannerSeen.has(el) || isInsideComposer(el)) continue;
      bannerSeen.add(el);
      const t = (el.innerText || '').trim();
      if (t) bannerTexts.push(t);
    }
  }
  const bannerScan = bannerTexts.join('\\n');
  const errorBannerMatch = bannerScan.match(errorBannerRe);
  const errorBanner = !!errorBannerMatch;
  // Context around first match for diagnostics (poll/CLI surfaces this).
  let errorBannerText = '';
  if (errorBannerMatch && typeof errorBannerMatch.index === 'number') {
    const i = errorBannerMatch.index;
    errorBannerText = bannerScan
      .slice(Math.max(0, i - 80), i + errorBannerMatch[0].length + 120)
      .replace(/\\s+/g, ' ')
      .trim();
  }
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
  const taskMapStepsText = taskMapSteps.join('\\n');
  const taskMapWorking =
    /working through/i.test(taskMapStepsText) ||
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
    error_banner_match: errorBannerMatch ? errorBannerMatch[0] : '',
    error_banner_text: errorBannerText,
    tool_pause: toolPause,
    model_label: modelLabel,
    task_map_present: taskMapPresent,
    task_map_working: taskMapWorking,
    task_map_idle: taskMapIdle,
  };
}
"""


async def harvest_assistant(page, *, min_msg_chars: int = 40) -> dict:
    """Evaluate ``HARVEST_JS`` on the page and return assistant-turn state."""
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
    return bool(state.get("streaming") or state.get("stop") or state.get("tool_pause"))


def _error_banner_message(state: dict, *, on_timeout: bool = False) -> str:
    """Human-readable HarvestIncomplete detail including matched banner text."""
    kind = "error_banner on timeout" if on_timeout else "error_banner detected"
    match = (state.get("error_banner_match") or "").strip()
    text = (state.get("error_banner_text") or "").strip()
    bits = [
        kind,
        f"url={state.get('url')}",
        f"len={state.get('body_len')}",
    ]
    if match:
        bits.append(f"match={match!r}")
    if text and text.lower() != match.lower():
        # Truncate so MCP/CLI errors stay skim-friendly.
        bits.append(f"ctx={text[:200]!r}")
    return " ".join(bits)


def _fatal_error_banner(state: dict) -> bool:
    """True when a banner is present AND the turn is idle (not recovering).

    Transient Claude overlays (``Overloaded``, rate-limit) often coexist with
    Stop/streaming while the product retries — aborting then orphans a live
    Cowork task (friction 25654). Only fail-closed once ¬in_flight.

    Callers must still prefer structural completion over this gate
    (friction 25684): a lingering delay overlay after the answer landed is
    not incompleteness.
    """
    return bool(state.get("error_banner")) and not _in_flight(state)


def _complete_enough(
    state: dict,
    *,
    base_len: int,
    base_n: int,
    min_growth: int,
    min_body: int,
    ignore_in_flight: bool = False,
) -> bool:
    """Structural turn complete — ¬ a prose-length gate.

    ``min_growth`` / ``min_body`` remain for call-site compat; ignored here.
    """
    del min_growth, min_body, base_len
    cur_len = state.get("body_len", 0)
    cur_n = state.get("n", 0)
    in_flight = _in_flight(state) and not ignore_in_flight
    return bool(cur_n > base_n and cur_len > 0 and not in_flight)


def _cowork_complete_enough(
    state: dict,
    *,
    base_len: int,
    base_n: int,
    min_growth: int,
    min_body: int,
    saw_working: bool,
    ignore_in_flight: bool = False,
) -> bool:
    """URL-guarded Cowork fallback (24864) with positive new-turn guard.

    Global gate ``cur_n > base_n`` is preserved on Chat paths via
    ``_complete_enough``. Cowork completion requires ``n`` growth (S1-c) —
    body-length growth or working→idle alone must not terminalize.
    """
    if not _is_cowork_cse_url(state.get("url", "")):
        return False
    # Lingering delay overlays (Overloaded) must not veto Cowork completion
    # once the turn is idle (friction 25684) — same as chat path.
    if _in_flight(state) and not ignore_in_flight:
        return False
    del min_body, min_growth
    cur_len = state.get("body_len", 0)
    cur_n = state.get("n", 0)
    if cur_len < 1:
        return False

    grew_n = cur_n > base_n
    # Body-length / working→idle without n growth must not terminalize (S1-c).
    return bool(grew_n)


def _is_user_prompt_echo(body: str) -> bool:
    """True when harvested text is the Cowork user-turn chrome (a:27801)."""
    return (body or "").lstrip().lower().startswith("you said:")


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
    on_harvest: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Wait until complete(turn) or idle timeout.

    ``timeout_s`` is idle wall-time without in-flight signals. While Stop,
    streaming, or tool_pause is observed the idle deadline is refreshed — there
    is no hard wall ceiling (friction 24666).

    ``on_harvest`` receives each successful sample (held-page only — dual-completion
    ladder consumers must not open a competing CDP connect; friction 25671).
    """
    msg_floor = min_msg_chars if min_msg_chars is not None else 10
    base_len = (before or {}).get("body_len", 0)
    base_n = (before or {}).get("n", 0)
    stable = 0
    cowork_stable = 0
    last_len = -1
    saw_working = False
    idle_deadline = time.monotonic() + max(timeout_s, 1)
    structural_quiet = StructuralQuietTracker()

    while True:
        state = await harvest_assistant(page, min_msg_chars=msg_floor)
        # Belt: even if HARVEST_JS still returns user chrome, do not complete.
        if _is_user_prompt_echo(str(state.get("body") or "")):
            state = {
                **state,
                "body": "",
                "body_len": 0,
                "n": base_n,
                "user_prompt_echo": True,
            }
        if on_harvest is not None:
            await on_harvest(state)
        structural_quiet.observe(state)
        # Never raise mid-poll on banner alone (friction 25654): Overloaded /
        # rate-limit overlays often appear while Stop/streaming is still up, or
        # briefly between product retries. Fail-closed only after idle timeout
        # with match text attached.
        cur_len = state.get("body_len", 0)
        cur_n = state.get("n", 0)
        in_flight = _in_flight(state)
        tier_a_escape = structural_quiet.quiet_satisfied and cur_n > base_n
        tier_b_unlatch = structural_quiet.quiet_satisfied and cur_n <= base_n
        effective_in_flight = in_flight and not tier_a_escape

        if state.get("task_map_working"):
            saw_working = True

        if effective_in_flight:
            if not tier_b_unlatch:
                idle_deadline = time.monotonic() + max(timeout_s, 1)
            stable = 0
            cowork_stable = 0
        else:
            # Structural / Cowork completion wins over a lingering delay overlay
            # (Overloaded can remain in the DOM after the answer landed —
            # friction 25684). Banner without a completed turn still holds
            # stable counters so a delayed retry can resume before fail-closed.
            ignore_in_flight = tier_a_escape
            if _complete_enough(
                state,
                base_len=base_len,
                base_n=base_n,
                min_growth=min_growth,
                min_body=min_body,
                ignore_in_flight=ignore_in_flight,
            ):
                if cur_len == last_len:
                    stable += 1
                else:
                    stable = 0
                last_len = cur_len
                if stable >= stable_polls:
                    return state
            elif _cowork_complete_enough(
                state,
                base_len=base_len,
                base_n=base_n,
                min_growth=min_growth,
                min_body=min_body,
                saw_working=saw_working,
                ignore_in_flight=ignore_in_flight,
            ):
                if cur_len == last_len:
                    cowork_stable += 1
                else:
                    cowork_stable = 0
                last_len = cur_len
                if cowork_stable >= stable_polls:
                    return state
            elif state.get("error_banner"):
                stable = 0
                cowork_stable = 0
            else:
                cowork_stable = 0

        if (not effective_in_flight or tier_b_unlatch) and time.monotonic() >= idle_deadline:
            break
        await asyncio.sleep(poll_ms / 1000)

    state = await harvest_assistant(page, min_msg_chars=msg_floor)
    if on_harvest is not None:
        await on_harvest(state)
    # Prefer structural completion over banner fail-closed (25684).
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
    if _fatal_error_banner(state):
        raise HarvestIncomplete(_error_banner_message(state, on_timeout=True))
    raise HarvestIncomplete(
        f"timed out incomplete (base_len={base_len}, last={state.get('body_len')}, "
        f"n={state.get('n')}) — ¬delete"
    )
