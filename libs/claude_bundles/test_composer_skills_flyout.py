"""Offline tests for scroll-aware Skills flyout pick (a:36560)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claude_bundles.composer_skills_flyout import (
    click_skill_slug_scrolling,
    row_matching_slug,
    scroll_skills_flyout,
)
from claude_bundles.cowork_skill_delivery import SkillDeliveryError

pytestmark = pytest.mark.offline


def test_row_matching_slug_prefers_collapse_equal() -> None:
    items = [
        {"text": "handoff-pickup", "aria": ""},
        {"text": "Hypothesize simulate", "aria": ""},
        {"text": "journal-digest", "aria": ""},
    ]
    hit = row_matching_slug(items, "hypothesize-simulate")
    assert hit is not None
    assert hit["text"] == "Hypothesize simulate"


def test_row_matching_slug_absent() -> None:
    assert (
        row_matching_slug([{"text": "reasoning-posture"}], "hypothesize-simulate")
        is None
    )


@pytest.mark.asyncio
async def test_scroll_skills_flyout_forwards_step() -> None:
    page = AsyncMock()
    page.evaluate = AsyncMock(
        return_value={"ok": True, "moved": True, "at_end": False, "after": 280}
    )
    result = await scroll_skills_flyout(page, step_px=120)
    assert result["moved"] is True
    page.evaluate.assert_awaited_once()
    assert page.evaluate.await_args.args[1] == 120


@pytest.mark.asyncio
async def test_click_skill_slug_scrolling_finds_after_scroll() -> None:
    """First viewport misses mid-list slug; one scroll mounts it (a:36560)."""
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    rounds = [
        [
            {"text": "handoff-pickup", "aria": ""},
            {"text": "journal-digest", "aria": ""},
        ],
        [
            {"text": "handoff-pickup", "aria": ""},
            {"text": "hypothesize-simulate", "aria": ""},
            {"text": "journal-digest", "aria": ""},
        ],
    ]

    async def open_items(_page):
        return rounds.pop(0)

    clicks: list[tuple[str, str]] = []

    async def click_label(_page, label: str, slug: str) -> None:
        clicks.append((label, slug))

    page.evaluate = AsyncMock(
        return_value={"ok": True, "moved": True, "at_end": False, "after": 280}
    )

    await click_skill_slug_scrolling(
        page,
        "hypothesize-simulate",
        open_items=open_items,
        click_label=click_label,
    )

    assert clicks == [("hypothesize-simulate", "hypothesize-simulate")]
    page.evaluate.assert_awaited()  # scrolled once before second inventory


@pytest.mark.asyncio
async def test_click_skill_slug_scrolling_raises_when_never_mounted() -> None:
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    page.evaluate = AsyncMock(
        return_value={"ok": True, "moved": False, "at_end": True, "after": 0}
    )

    async def open_items(_page):
        return [{"text": "reasoning-posture", "aria": ""}]

    async def click_label(_page, label: str, slug: str) -> None:  # noqa: ARG001
        raise AssertionError("must not click")

    with pytest.raises(SkillDeliveryError, match="hypothesize-simulate"):
        await click_skill_slug_scrolling(
            page,
            "hypothesize-simulate",
            open_items=open_items,
            click_label=click_label,
        )
