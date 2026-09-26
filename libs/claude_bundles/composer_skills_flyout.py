"""Type a skill slug so the Cowork Skills flyout scrolls to that row (a:36560).

The open menu already has keyboard focus from the Skills click. Typing the
slug is the menu's typeahead: it scrolls the matching row into view. A
scripted ``scrollTop`` walk is not how this list moves.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from playwright.async_api import Page

from claude_bundles.composer_skill_match import label_matches_slug
from claude_bundles.cowork_skill_delivery import SkillDeliveryError

# Radix-style typeahead clears its buffer after about a second of idle.
# Delay 0 keeps the whole slug in one search.
_TYPEAHEAD_DELAY_MS = 0
_TYPEAHEAD_SETTLE_MS = 200

OpenItemsFn = Callable[[Page], Awaitable[list[dict[str, str]]]]
ClickLabelFn = Callable[[Page, str, str], Awaitable[None]]


def row_matching_slug(items: list[dict[str, str]], slug: str) -> dict[str, str] | None:
    """First mounted menu row whose label names ``slug``, else None."""
    for row in items:
        label = row.get("text") or row.get("aria") or ""
        if label_matches_slug(slug, label):
            return row
    return None


async def click_skill_slug_typed(
    page: Page,
    slug: str,
    *,
    open_items: OpenItemsFn,
    click_label: ClickLabelFn,
) -> None:
    """Type ``slug`` so the flyout scrolls to it, then click that row.

    ``open_items`` / ``click_label`` are injected so the session-skills module
    keeps ownership of DOM inventory and click heuristics.
    """
    await page.keyboard.type(slug, delay=_TYPEAHEAD_DELAY_MS)
    await page.wait_for_timeout(_TYPEAHEAD_SETTLE_MS)
    items = await open_items(page)
    hit = row_matching_slug(items, slug)
    if hit is None:
        raise SkillDeliveryError(
            f"skill {slug!r} not in Skills list — items={items[:30]!r}"
        )
    label = hit.get("text") or hit.get("aria") or ""
    await click_label(page, label, slug)
