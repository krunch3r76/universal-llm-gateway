"""Fast completion detection for claude.ai assistant replies via CDP harvest.

Friction notes (keep when generalizing):
- Poll div[class*="font-claude"] or assistant-message nodes, NOT project header.
- Pass ``before`` from pre-send harvest on legacy follow-up turns, or ``anchor=``
  with ``tail_hold=True`` on anchored CDP dispatches.
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
- Anchored harvest: ``tail_hold`` blocks completion on tool-row tails; idle
  expiry on an unresolved tail or missing anchor classifies ``observer_unverified``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from cdp_ask.structural_quiet import StructuralQuietTracker
from chat_harvest.chrome import badge_scrape_change_key, is_prompt_echo

from claude_bundles.cse_idle_probe import in_flight_from_state
from claude_bundles.harvest_dom import HARVEST_JS
from claude_bundles.reply_completion import (
    badge_only_body,
    complete_enough,
    cowork_complete_enough,
    error_banner_message,
    fatal_error_banner,
)

if TYPE_CHECKING:
    from claude_bundles.reply_anchor import ReplyAnchor


async def harvest_assistant(
    page,
    *,
    min_msg_chars: int = 40,
    anchor: ReplyAnchor | None = None,
) -> dict:
    """Evaluate ``HARVEST_JS`` on the page and return assistant-turn state."""
    args: dict[str, object] = {"minMsgChars": min_msg_chars}
    if anchor is not None:
        args.update(anchor.js_args())
    return await page.evaluate(HARVEST_JS, args)


class HarvestIncompleteError(RuntimeError):
    """Turn did not satisfy complete(turn) — caller must ¬delete.

    ``body`` is the last scraped assistant text when present so callers can
    still surface a nonzero partial harvest on the bus instead of
    ``FAILED body_len=0`` (a:37226 / agent-bus:14163#3).

    ``tail_hold`` and ``anchor_found`` classify observer-unverified stalls when set.
    """

    def __init__(
        self,
        message: str,
        *,
        body: str = "",
        state: dict | None = None,
        tail_hold: bool = False,
        anchor_found: bool | None = None,
    ) -> None:
        super().__init__(message)
        if body:
            self.body = body
        elif state is not None:
            self.body = str(state.get("body") or "")
        else:
            self.body = ""
        self.state = state
        self.tail_hold = tail_hold
        self.anchor_found = anchor_found


async def wait_assistant_reply(
    page,
    *,
    before: dict | None = None,
    anchor: ReplyAnchor | None = None,
    tail_hold: bool = False,
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

    Pass ``anchor`` or ``before``, not both. Anchored mode uses window-relative
    ``n`` / ``body_len`` (``base_n`` and ``base_len`` are 0).
    """
    if anchor is not None and before is not None:
        raise ValueError("wait_assistant_reply: pass anchor= or before=, not both")
    msg_floor = min_msg_chars if min_msg_chars is not None else 10
    if anchor is not None:
        base_len = 0
        base_n = 0
    else:
        base_len = (before or {}).get("body_len", 0)
        base_n = (before or {}).get("n", 0)
    stable = 0
    cowork_stable = 0
    last_len = -1
    saw_working = False
    idle_deadline = time.monotonic() + max(timeout_s, 1)
    structural_quiet = StructuralQuietTracker()
    prev_badge_key: str | None = None
    prev_progress_key: tuple[int, str] | None = None

    async def _sample() -> dict:
        state = await harvest_assistant(page, min_msg_chars=msg_floor, anchor=anchor)
        if is_prompt_echo(str(state.get("body") or "")):
            state = {
                **state,
                "body": "",
                "body_len": 0,
                "n": base_n,
                "user_prompt_echo": True,
            }
        return state

    def _completion_kwargs(*, ignore_in_flight: bool = False) -> dict:
        return {
            "base_len": base_len,
            "base_n": base_n,
            "min_growth": min_growth,
            "min_body": min_body,
            "ignore_in_flight": ignore_in_flight,
            "require_review_verdict": require_review_verdict,
            "tail_hold": tail_hold,
        }

    while True:
        state = await _sample()
        if on_harvest is not None:
            await on_harvest(state)
        structural_quiet.observe(state)
        cur_len = state.get("body_len", 0)
        cur_n = state.get("n", 0)
        in_flight = in_flight_from_state(state)
        tier_a_escape = structural_quiet.quiet_satisfied and cur_n > base_n
        tier_b_unlatch = structural_quiet.quiet_satisfied and cur_n <= base_n
        effective_in_flight = in_flight and not tier_a_escape

        if state.get("task_map_working"):
            saw_working = True

        body_text = str(state.get("body") or "")
        if tail_hold:
            progress_key = (cur_n, badge_scrape_change_key(body_text))
            if (
                prev_progress_key is not None
                and progress_key != prev_progress_key
            ):
                idle_deadline = time.monotonic() + max(timeout_s, 1)
            prev_progress_key = progress_key
        elif badge_only_body(state):
            badge_key = badge_scrape_change_key(body_text)
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
            ignore_in_flight = tier_a_escape
            kwargs = _completion_kwargs(ignore_in_flight=ignore_in_flight)
            if complete_enough(state, **kwargs):
                if cur_len == last_len:
                    stable += 1
                else:
                    stable = 0
                last_len = cur_len
                if stable >= stable_polls:
                    return state
            elif cowork_complete_enough(
                state, saw_working=saw_working, **kwargs
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

    state = await _sample()
    if on_harvest is not None:
        await on_harvest(state)
    kwargs = _completion_kwargs()
    if complete_enough(state, **kwargs):
        return state
    if cowork_complete_enough(state, saw_working=saw_working, **kwargs):
        return state
    if fatal_error_banner(state):
        raise HarvestIncompleteError(
            error_banner_message(state, on_timeout=True),
            state=state,
        )
    if anchor is not None and not state.get("anchor_found"):
        matches = state.get("anchor_matches", 0)
        prior = anchor.prior_matches
        raise HarvestIncompleteError(
            "anchor not found at idle budget: no committed user turn matched the "
            f"prompt marker (matches={matches}, prior={prior}) — ¬delete",
            state=state,
            anchor_found=False,
        )
    if tail_hold:
        from chat_harvest.chrome import ends_with_tool_row

        if ends_with_tool_row(str(state.get("body") or "")):
            raise HarvestIncompleteError(
                "tail_hold unresolved at idle budget: reply still ends on a tool row "
                f"(n={state.get('n')}, body_len={state.get('body_len')}) — ¬delete",
                state=state,
                tail_hold=True,
            )
    raise HarvestIncompleteError(
        f"timed out incomplete (base_len={base_len}, last={state.get('body_len')}, "
        f"n={state.get('n')}) — ¬delete",
        state=state,
    )
