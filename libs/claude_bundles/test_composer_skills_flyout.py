"""Offline tests for typeahead Skills flyout pick (a:36560)."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from claude_bundles.composer_skills_flyout import (
    click_skill_slug_typed,
    row_matching_slug,
)
from claude_bundles.cowork_skill_delivery import SkillDeliveryError

pytestmark = pytest.mark.offline


def test_row_matching_slug_prefers_collapse_equal() -> None:
    """A spaced H1 must match the kebab slug so the picker lands on that skill."""
    items = [
        {"text": "handoff-pickup", "aria": ""},
        {"text": "Hypothesize simulate", "aria": ""},
        {"text": "journal-digest", "aria": ""},
    ]
    hit = row_matching_slug(items, "hypothesize-simulate")
    assert hit is not None
    assert hit["text"] == "Hypothesize simulate"


def test_row_matching_slug_absent() -> None:
    """An absent slug has no row, so the matcher must not invent a neighbor."""
    assert (
        row_matching_slug([{"text": "reasoning-posture"}], "hypothesize-simulate")
        is None
    )


def _page_without_search() -> AsyncMock:
    """Menu page whose flyout has no search field, so typing stays on the menu."""
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    search = AsyncMock()
    search.count = AsyncMock(return_value=0)
    page.locator = Mock(return_value=search)
    return page


def _snapshot_open(snapshots: list[list[dict[str, str]]]):
    """Return open_items that yields each pre-type and post-type inventory once."""

    async def open_items(_page):
        if len(snapshots) > 1:
            return snapshots.pop(0)
        return snapshots[0]

    return open_items


@pytest.mark.asyncio
async def test_click_skill_slug_typed_types_then_clicks() -> None:
    """Typing the slug is what scrolls the row into view (a:36560).

    No row is overwritten: the matching label is what gets clicked, and the
    inventory is readable both before and after the keystrokes.
    """
    page = _page_without_search()
    clicks: list[tuple[str, str]] = []
    rows = [
        {"text": "handoff-pickup", "aria": ""},
        {"text": "hypothesize-simulate", "aria": ""},
        {"text": "journal-digest", "aria": ""},
    ]
    seen = {"n": 0}

    async def open_items(_page):
        seen["n"] += 1
        if seen["n"] == 1:
            assert page.keyboard.type.await_count == 0
        return rows

    async def click_label(_page, label: str, slug: str) -> None:
        clicks.append((label, slug))

    await click_skill_slug_typed(
        page,
        "hypothesize-simulate",
        open_items=open_items,
        click_label=click_label,
    )

    assert clicks == [("hypothesize-simulate", "hypothesize-simulate")]
    assert seen["n"] == 2
    page.keyboard.type.assert_awaited_once_with("hypothesize-simulate", delay=0)
    page.keyboard.press.assert_not_awaited()


@pytest.mark.asyncio
async def test_click_skill_slug_typed_clicks_earlier_row_not_overwritten_highlight() -> (
    None
):
    """The earlier slug row is the pick; the highlighted last row is not clicked again.

    Pre-type the last row is journal-digest. After typing, that same slot reads
    hypothesize-simulate and an earlier row does too. One click must carry the
    earlier label so the overwritten highlight does not receive a second pick.
    """
    page = _page_without_search()
    clicks: list[tuple[str, str]] = []
    open_items = _snapshot_open(
        [
            [
                {"text": "handoff-pickup", "aria": ""},
                {"text": "hypothesize-simulate", "aria": ""},
                {"text": "journal-digest", "aria": ""},
            ],
            [
                {"text": "handoff-pickup", "aria": ""},
                {"text": "hypothesize-simulate", "aria": ""},
                {"text": "hypothesize-simulate", "aria": ""},
            ],
        ]
    )

    async def click_label(_page, label: str, slug: str) -> None:
        clicks.append((label, slug))

    await click_skill_slug_typed(
        page,
        "hypothesize-simulate",
        open_items=open_items,
        click_label=click_label,
    )

    assert clicks == [("hypothesize-simulate", "hypothesize-simulate")]


@pytest.mark.asyncio
async def test_click_skill_slug_typed_refuses_overwritten_highlight() -> None:
    """The only post-type match is the highlight whose pre-type text was another skill.

    That row read journal-digest before typing and hypothesize-simulate after.
    Clicking it would replace the highlighted skill, so the pick must raise
    and call click_label zero times.
    """
    page = _page_without_search()
    clicks: list[tuple[str, str]] = []
    open_items = _snapshot_open(
        [
            [
                {"text": "handoff-pickup", "aria": ""},
                {"text": "journal-digest", "aria": ""},
            ],
            [
                {"text": "handoff-pickup", "aria": ""},
                {"text": "hypothesize-simulate", "aria": ""},
            ],
        ]
    )

    async def click_label(_page, label: str, slug: str) -> None:
        clicks.append((label, slug))

    with pytest.raises(SkillDeliveryError, match="journal-digest"):
        await click_skill_slug_typed(
            page,
            "hypothesize-simulate",
            open_items=open_items,
            click_label=click_label,
        )

    assert clicks == []


@pytest.mark.asyncio
async def test_click_skill_slug_typed_raises_when_still_absent() -> None:
    """When typing leaves the slug unmounted, refusing to click avoids another skill."""
    page = _page_without_search()

    async def open_items(_page):
        return [{"text": "reasoning-posture", "aria": ""}]

    async def click_label(_page, label: str, slug: str) -> None:  # noqa: ARG001
        raise AssertionError("must not click")

    with pytest.raises(SkillDeliveryError, match="hypothesize-simulate"):
        await click_skill_slug_typed(
            page,
            "hypothesize-simulate",
            open_items=open_items,
            click_label=click_label,
        )

    page.keyboard.type.assert_awaited_once_with("hypothesize-simulate", delay=0)
