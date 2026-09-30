"""Type a skill slug so the Cowork Skills flyout scrolls to that row (a:36560).

The open menu already has keyboard focus from the Skills click. Typing the
slug is the menu's typeahead: it scrolls the matching row into view. A
scripted ``scrollTop`` walk is not how this list moves.

The last row stays highlighted. Clicking that highlight after typing replaces
the skill that was on it (a:37007), so the pre-type snapshot is what decides
which row may receive the pick.
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


# Mounted search owns the typeahead buffer. The highlighted menuitem does not.
_SEARCH_INPUT = '[role="menu"] input, [role="listbox"] input, [cmdk-input]'


def _row_label(row: dict[str, str]) -> str:
    return (row.get("text") or row.get("aria") or "").strip()


def _last_labeled_row(items: list[dict[str, str]]) -> dict[str, str] | None:
    """Last non-empty row — the Skills selector leaves that one highlighted."""
    found: dict[str, str] | None = None
    for row in items:
        if _row_label(row):
            found = row
    return found


def row_matching_slug(items: list[dict[str, str]], slug: str) -> dict[str, str] | None:
    """First mounted menu row whose label names ``slug``, else None."""
    for row in items:
        if label_matches_slug(slug, _row_label(row)):
            return row
    return None


async def _type_slug(page: Page, slug: str) -> None:
    """Type ``slug`` into a mounted search field, else into the focused menu.

    Keystrokes aimed at the highlighted menuitem commit over that row. A
    search field, when present, is the buffer that scrolls instead.
    """
    search = page.locator(_SEARCH_INPUT)
    if await search.count() > 0:
        await search.first.click()
        await page.keyboard.press("Control+A")
    await page.keyboard.type(slug, delay=_TYPEAHEAD_DELAY_MS)


async def click_skill_slug_typed(
    page: Page,
    slug: str,
    *,
    open_items: OpenItemsFn,
    click_label: ClickLabelFn,
) -> None:
    """Type ``slug``, then click a row that already named it before typing.

    The last Skills row stays highlighted, and the next pick commits over
    that highlight (a:37007). Snapshot it before keystrokes. Click a post-type
    match that is not that row when the pre-type label was a different skill.
    If the only post-type match is the overwritten highlight, raise rather
    than hand its label to ``click_label``. ``open_items`` / ``click_label``
    stay injected so session-skills owns DOM inventory and the click.
    """
    pre_items = await open_items(page)
    prior = _last_labeled_row(pre_items)
    prior_text = _row_label(prior) if prior else ""

    await _type_slug(page, slug)
    await page.wait_for_timeout(_TYPEAHEAD_SETTLE_MS)
    items = await open_items(page)
    hit = row_matching_slug(items, slug)
    if hit is None:
        raise SkillDeliveryError(
            f"skill {slug!r} not in Skills list — items={items[:30]!r}"
        )
    hit_text = _row_label(hit)
    if prior_text and hit_text == prior_text:
        await click_label(page, hit_text, slug)
        return

    highlight = _last_labeled_row(items)
    prior_names_slug = bool(prior_text) and label_matches_slug(slug, prior_text)
    for row in items:
        if not label_matches_slug(slug, _row_label(row)):
            continue
        label = _row_label(row)
        overwritten = (
            prior is not None
            and not prior_names_slug
            and highlight is not None
            and row is highlight
        )
        if overwritten or (not prior_names_slug and label == prior_text):
            continue
        await click_label(page, label, slug)
        return
    raise SkillDeliveryError(
        f"skill {slug!r} matches only the highlighted row {prior_text!r}, "
        "which named a different skill before typing"
    )
