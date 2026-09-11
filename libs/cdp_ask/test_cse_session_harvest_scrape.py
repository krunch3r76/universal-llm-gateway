"""Unit tests for CSE harvest scrape helpers — loading predicate and URL picker."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cdp_ask.cse_session_harvest_scrape import (
    _dom_to_harvested,
    compute_incomplete_dom,
    enrich_dom,
    is_loading,
    is_shell_title,
    pick_page_for_chat_url,
)
from cdp_ask.cse_session_models import HarvestRequest


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("New task - Claude", True),
        ("New chat - Claude", True),
        ("Claude", True),
        ("", True),
        ("Life coding playbook - Claude", False),
        ("Mid-session task - Claude", False),
    ],
)
def test_is_shell_title(title: str, expected: bool) -> None:
    assert is_shell_title(title) is expected


def test_is_loading_spinner_or_shell() -> None:
    assert is_loading({"spinner": True, "turns": []}) is True
    assert is_loading({"aria_busy": True, "turns": []}) is True
    assert is_loading({"title": "New chat - Claude", "turns": []}) is True
    assert is_loading({"title": "Named session - Claude", "turns": []}) is False


def test_compute_incomplete_dom_requires_loading() -> None:
    assert (
        compute_incomplete_dom({"turns": [], "streaming": False, "loading": True})
        is True
    )
    assert (
        compute_incomplete_dom({"turns": [], "streaming": False, "loading": False})
        is False
    )
    assert (
        compute_incomplete_dom(
            {"turns": [{"text": "x"}], "streaming": False, "loading": True}
        )
        is False
    )
    assert (
        compute_incomplete_dom({"turns": [], "streaming": True, "loading": True})
        is False
    )


def test_enrich_dom_defaults_missing_loading_false() -> None:
    dom = enrich_dom({"turns": [], "streaming": False, "title": "Named - Claude"})
    assert dom["loading"] is False
    assert dom["incomplete_dom"] is False


@pytest.mark.asyncio
async def test_pick_page_for_chat_url_prefers_matching_cse_tab() -> None:
    fallback = MagicMock()
    match = MagicMock()
    match.url = "https://claude.ai/cowork/cse_abc/"
    other = MagicMock()
    other.url = "https://claude.ai/new"
    ctx = MagicMock()
    ctx.pages = [other, match]
    picked = await pick_page_for_chat_url(
        ctx,
        "https://claude.ai/cowork/cse_abc",
        fallback=fallback,
    )
    assert picked is match


def test_dom_to_harvested_maps_coverage_full() -> None:
    dom = {
        "turns": [
            {
                "author": "user",
                "text": "User turn with enough text here.",
                "ordinal": 1,
            },
            {
                "author": "assistant",
                "text": "Assistant reply with enough text here.",
                "ordinal": 2,
            },
        ],
        "truncated": False,
        "coverage": "full",
    }
    resp = _dom_to_harvested(dom, HarvestRequest(), None)
    assert resp.outcome == "harvested"
    assert resp.coverage == "full"
    assert resp.turns[0].author == "user"
    assert resp.turns[0].source == "cse-dom"


@pytest.mark.asyncio
async def test_pick_page_for_chat_url_falls_back() -> None:
    fallback = MagicMock()
    ctx = MagicMock()
    ctx.pages = [MagicMock(url="https://claude.ai/new")]
    picked = await pick_page_for_chat_url(
        ctx,
        "https://claude.ai/cowork/cse_abc",
        fallback=fallback,
    )
    assert picked is fallback
