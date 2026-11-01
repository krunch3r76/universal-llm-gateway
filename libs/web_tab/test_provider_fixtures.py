"""Static HTML fixtures — user/assistant turn selectors match provider DOM shapes."""

from __future__ import annotations

from pathlib import Path

from web_tab.provider_selectors import (
    _ASSISTANT_TURN_SELECTORS,
    _USER_TURN_SELECTORS,
    composer_selectors_for_site,
)


def _fixture(name: str) -> str:
    return (Path(__file__).resolve().parent / "fixtures" / name).read_text(
        encoding="utf-8"
    )


def _css_hits(html: str, selector: str) -> bool:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return selector.split(",")[0].strip() in html
    soup = BeautifulSoup(html, "html.parser")
    for part in selector.split(","):
        if soup.select_one(part.strip()):
            return True
    return False


def test_grok_fixture_user_assistant_turns() -> None:
    html = _fixture("grok_chat.html")
    assert _css_hits(html, _USER_TURN_SELECTORS["grok"])
    assert _css_hits(html, _ASSISTANT_TURN_SELECTORS["grok"])
    assert any(_css_hits(html, sel) for sel in composer_selectors_for_site("grok"))


def test_claude_fixture_user_assistant_turns() -> None:
    html = _fixture("claude_chat.html")
    assert _css_hits(html, _USER_TURN_SELECTORS["claude"])
    assert _css_hits(html, _ASSISTANT_TURN_SELECTORS["claude"])
    assert any(_css_hits(html, sel) for sel in composer_selectors_for_site("claude"))


def test_chatgpt_fixture_user_turn_and_composer() -> None:
    html = _fixture("chatgpt_composer.html")
    assert _css_hits(html, _USER_TURN_SELECTORS["chatgpt"])
    assert any(_css_hits(html, sel) for sel in composer_selectors_for_site("chatgpt"))
