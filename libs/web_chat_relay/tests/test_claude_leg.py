"""Hermetic Cowork-scrape chrome stripping (no network)."""

from __future__ import annotations

import pytest
from web_chat_relay.claude_leg import strip_chrome

pytestmark = pytest.mark.offline


def test_strip_chrome_drops_responded_label_and_duplicate_badge() -> None:
    # Confirmed live 2026-08-17 harvest body, trimmed to the reproducing shape.
    text = (
        "Claude responded: Claude \u2192 Grok.\n"
        "Used toys integration, used 2 skills\n\n"
        "Used toys integration, used 2 skills\n\n"
        "Claude \u2192 Grok. Both items landed."
    )
    assert strip_chrome(text) == "Claude \u2192 Grok. Both items landed."


def test_strip_chrome_keeps_leading_words_that_echo_the_label() -> None:
    # The real reply legitimately starting with the same words as the
    # stripped label is not itself chrome and must survive.
    text = "Claude responded: Searches are done.\n\nSearches are done. Recording now."
    assert strip_chrome(text) == "Searches are done. Recording now."


def test_strip_chrome_drops_trailing_relative_timestamp() -> None:
    text = "Recorded. Reply for grok below.\n\n\n\n\n2 minutes ago"
    assert strip_chrome(text) == "Recorded. Reply for grok below."


def test_strip_chrome_passes_through_plain_reply() -> None:
    assert strip_chrome("No chrome in this one.") == "No chrome in this one."
