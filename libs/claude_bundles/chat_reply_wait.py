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
  A tool-badge body is not that reply. ``streaming`` pauses between tools, so
  ``¬streaming`` plus a badge must not complete. The dispatch's proof is the
  later agent-bus reply, which is posted only after this wait returns.
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
from chat_harvest.chrome import (
    badge_scrape_change_key,
    is_chrome_only,
    is_tool_status_body,
)
from review_verdict.grammar import has_parseable_verdict

from claude_bundles.harvest_dom import HARVEST_JS


async def harvest_assistant(page, *, min_msg_chars: int = 40) -> dict:
    """Evaluate ``HARVEST_JS`` on the page and return assistant-turn state."""
    return await page.evaluate(HARVEST_JS, {"minMsgChars": min_msg_chars})


class HarvestIncompleteError(RuntimeError):
    """Turn did not satisfy complete(turn) — caller must ¬delete.

    ``body`` is the last scraped assistant text when present so callers can
    still surface a nonzero partial harvest on the bus instead of
    ``FAILED body_len=0`` (a:37226 / agent-bus:14163#3).
    """

    def __init__(
        self,
        message: str,
        *,
        body: str = "",
        state: dict | None = None,
    ) -> None:
        super().__init__(message)
        if body:
            self.body = body
        elif state is not None:
            self.body = str(state.get("body") or "")
        else:
            self.body = ""
        self.state = state


def _is_cowork_cse_url(url: str) -> bool:
    return "/cowork/cse_" in (url or "")


def _in_flight(state: dict) -> bool:
    """Cowork/tool liveness — Stop / streaming / tool_pause pause the idle clock.

    ``streaming`` is the defense-in-depth backstop when ``stop`` is momentarily
    false during generation (24873 R-amendment).
    """
    return bool(state.get("streaming") or state.get("stop") or state.get("tool_pause"))


def _badge_only_body(state: dict) -> bool:
    """True when the scrape is tool-badge chrome, not assistant prose.

    Cowork drops ``data-is-streaming`` between tool calls. That pause is not
    the end of the turn, and it is not the agent-bus proof reply.
    """
    body = str(state.get("body") or "")
    return is_chrome_only(body) or is_tool_status_body(body)


def _error_banner_message(state: dict, *, on_timeout: bool = False) -> str:
    """Human-readable HarvestIncompleteError detail including matched banner text."""
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
    require_review_verdict: bool = False,
) -> bool:
    """Structural turn complete — ¬ a prose-length gate.

    ``min_growth`` / ``min_body`` remain for call-site compat; ignored here.
    ``require_review_verdict`` (job=delivery-review): refuse skill-induction /
    mid-tool prose that lacks a parseable verdict line (a:37156 / a:37034).
    """
    del min_growth, min_body, base_len
    if _badge_only_body(state):
        return False
    if require_review_verdict and not has_parseable_verdict(
        str(state.get("body") or "")
    ):
        return False
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
    require_review_verdict: bool = False,
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
    del min_body, min_growth, saw_working
    cur_len = state.get("body_len", 0)
    cur_n = state.get("n", 0)
    if cur_len < 1 or _badge_only_body(state):
        return False
    if require_review_verdict and not has_parseable_verdict(
        str(state.get("body") or "")
    ):
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
    require_review_verdict: bool = False,
) -> dict:
    """Wait until complete(turn) or idle timeout.

    ``timeout_s`` is idle wall-time without in-flight signals. While Stop,
    streaming, or tool_pause is observed the idle deadline is refreshed — there
    is no hard wall ceiling (friction 24666). A badge-only scrape refreshes
    that same deadline when its chrome-narrowed text changes, including while
    those in-flight flags are false. A static badge page still expires.

    ``on_harvest`` receives each successful sample (held-page only — dual-completion
    ladder consumers must not open a competing CDP connect; friction 25671).

    ``require_review_verdict``: when True (``job=delivery-review``), structural idle
    alone is not enough — the harvested body must carry a parseable verdict.
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
    prev_badge_key: str | None = None

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

        if _badge_only_body(state):
            badge_key = badge_scrape_change_key(str(state.get("body") or ""))
            if prev_badge_key is not None and badge_key != prev_badge_key:
                idle_deadline = time.monotonic() + max(timeout_s, 1)
            prev_badge_key = badge_key
        else:
            prev_badge_key = None

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
                require_review_verdict=require_review_verdict,
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
                require_review_verdict=require_review_verdict,
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

        if (
            not effective_in_flight or tier_b_unlatch
        ) and time.monotonic() >= idle_deadline:
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
        require_review_verdict=require_review_verdict,
    ):
        return state
    if _cowork_complete_enough(
        state,
        base_len=base_len,
        base_n=base_n,
        min_growth=min_growth,
        min_body=min_body,
        saw_working=saw_working,
        require_review_verdict=require_review_verdict,
    ):
        return state
    if _fatal_error_banner(state):
        raise HarvestIncompleteError(
            _error_banner_message(state, on_timeout=True),
            state=state,
        )
    raise HarvestIncompleteError(
        f"timed out incomplete (base_len={base_len}, last={state.get('body_len')}, "
        f"n={state.get('n')}) — ¬delete",
        state=state,
    )
