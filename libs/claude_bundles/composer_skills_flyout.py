"""Scroll-aware Cowork Skills flyout pick (friction a:36560).

``_open_menu_items`` only sees *mounted* menuitems. Customize catalogs are long
enough that mid-alphabet slugs (e.g. ``hypothesize-simulate``) sit below the
fold and never enter the DOM query until the flyout scrolls. Reporting
"not in Skills list" from the first viewport is a false absence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from playwright.async_api import Page

from claude_bundles.composer_skill_match import label_matches_slug
from claude_bundles.cowork_skill_delivery import SkillDeliveryError

_MAX_SCROLL_STEPS = 24
_SCROLL_STEP_PX = 280
_SETTLE_MS = 120

# Find the tallest scrollable container under an open menu/listbox and advance it.
_SCROLL_STEP_JS = """(stepPx) => {
  const overflowY = (el) => getComputedStyle(el).overflowY;
  const isScrollable = (el) => {
    if (!el || el.scrollHeight <= el.clientHeight + 8) return false;
    const oy = overflowY(el);
    return oy === 'auto' || oy === 'scroll' || oy === 'overlay';
  };
  const roots = [
    ...document.querySelectorAll(
      '[role="menu"], [role="listbox"], [cmdk-list], [data-radix-scroll-area-viewport]'
    ),
  ];
  let best = null;
  let bestScore = 0;
  const consider = (el) => {
    if (!isScrollable(el)) return;
    const score = el.scrollHeight;
    if (score > bestScore) {
      best = el;
      bestScore = score;
    }
  };
  for (const root of roots) {
    consider(root);
    for (const child of root.querySelectorAll('*')) {
      consider(child);
    }
  }
  if (!best) return {ok: false, reason: 'no_scroller'};
  const before = best.scrollTop;
  const max = Math.max(0, best.scrollHeight - best.clientHeight);
  best.scrollTop = Math.min(max, before + Math.max(1, stepPx | 0));
  return {
    ok: true,
    before,
    after: best.scrollTop,
    max,
    at_end: best.scrollTop >= max - 1,
    moved: best.scrollTop > before + 0.5,
  };
}"""

OpenItemsFn = Callable[[Page], Awaitable[list[dict[str, str]]]]
ClickLabelFn = Callable[[Page, str, str], Awaitable[None]]


async def scroll_skills_flyout(
    page: Page, *, step_px: int = _SCROLL_STEP_PX
) -> dict[str, Any]:
    """Advance the Skills flyout scroller by ``step_px``. Returns evaluate payload."""
    result = await page.evaluate(_SCROLL_STEP_JS, step_px)
    return result if isinstance(result, dict) else {"ok": False, "reason": "bad_eval"}


def row_matching_slug(items: list[dict[str, str]], slug: str) -> dict[str, str] | None:
    """First mounted menu row whose label names ``slug``, else None."""
    for row in items:
        label = row.get("text") or row.get("aria") or ""
        if label_matches_slug(slug, label):
            return row
    return None


async def click_skill_slug_scrolling(
    page: Page,
    slug: str,
    *,
    open_items: OpenItemsFn,
    click_label: ClickLabelFn,
) -> None:
    """Scroll the Skills flyout until ``slug`` mounts, then click it.

    ``open_items`` / ``click_label`` are injected so the session-skills module
    keeps ownership of DOM inventory and click heuristics.
    """
    seen_labels: set[str] = set()
    last_items: list[dict[str, str]] = []
    end_streak = 0

    for _ in range(_MAX_SCROLL_STEPS):
        items = await open_items(page)
        last_items = items
        hit = row_matching_slug(items, slug)
        if hit is not None:
            label = hit.get("text") or hit.get("aria") or ""
            await click_label(page, label, slug)
            return

        labels = {
            (row.get("text") or row.get("aria") or "").strip()
            for row in items
            if (row.get("text") or row.get("aria") or "").strip()
        }
        progress = bool(labels - seen_labels)
        seen_labels |= labels

        scroll = await scroll_skills_flyout(page)
        if scroll.get("ok") and scroll.get("moved"):
            end_streak = 0
            await page.wait_for_timeout(_SETTLE_MS)
            continue

        # No dedicated scroller, or scrollTop stuck: PageDown may still remount.
        await page.keyboard.press("PageDown")
        await page.wait_for_timeout(_SETTLE_MS)
        if scroll.get("at_end") or not scroll.get("ok"):
            if not progress:
                end_streak += 1
            else:
                end_streak = 0
            if end_streak >= 2:
                break

    raise SkillDeliveryError(
        f"skill {slug!r} not in Skills list — items={last_items[:30]!r}"
    )
