"""Offline tests for typeahead Skills flyout pick (a:36560)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claude_bundles.composer_skills_flyout import (
    click_skill_slug_typed,
    row_matching_slug,
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
async def test_click_skill_slug_typed_types_then_clicks() -> None:
    """Typing the slug is what scrolls the row into view (a:36560)."""
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    clicks: list[tuple[str, str]] = []

    async def open_items(_page):
        page.keyboard.type.assert_awaited_once_with("hypothesize-simulate", delay=0)
        return [
            {"text": "handoff-pickup", "aria": ""},
            {"text": "hypothesize-simulate", "aria": ""},
            {"text": "journal-digest", "aria": ""},
        ]

    async def click_label(_page, label: str, slug: str) -> None:
        clicks.append((label, slug))

    await click_skill_slug_typed(
        page,
        "hypothesize-simulate",
        open_items=open_items,
        click_label=click_label,
    )

    assert clicks == [("hypothesize-simulate", "hypothesize-simulate")]
    page.keyboard.type.assert_awaited_once_with("hypothesize-simulate", delay=0)


@pytest.mark.asyncio
async def test_click_skill_slug_typed_raises_when_still_absent() -> None:
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()

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
