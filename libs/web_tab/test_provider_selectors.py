"""Provider selector table and URL classification."""

from __future__ import annotations

from pathlib import Path

from web_tab.provider_selectors import (
    composer_selector_for_site,
    site_from_url,
    verify_committed_js,
)


def test_site_from_url_chatgpt() -> None:
    assert site_from_url("https://chatgpt.com/c/abc") == "chatgpt"


def test_site_from_url_grok() -> None:
    assert site_from_url("https://grok.com/c/uuid") == "grok"


def test_site_from_url_claude() -> None:
    assert site_from_url("https://claude.ai/chat/uuid") == "claude"


def test_composer_selectors_non_empty() -> None:
    for site in ("chatgpt", "grok", "claude", "x_grok"):
        assert composer_selector_for_site(site)  # noqa: S101


def test_verify_committed_js_references_prompt() -> None:
    js = verify_committed_js(site="grok", prompt_text="hello")
    assert "hello" in js
    assert "dom_committed" in js


def test_chatgpt_composer_js_includes_prosemirror() -> None:
    from web_tab.provider_selectors import composer_selectors_for_site

    sels = composer_selectors_for_site("chatgpt")
    assert "div.ProseMirror[contenteditable='true']" in sels


def test_read_reply_js_grok_strips_activity_chip() -> None:
    from web_tab.provider_selectors import read_reply_js

    js = read_reply_js(site="grok", baseline_assistant_count=0)
    assert "Working|Worked" in js
    assert "stripGrokChip" in js


def test_fixtures_exist_on_disk() -> None:
    root = Path(__file__).resolve().parent / "fixtures"
    for name in ("chatgpt_composer.html", "grok_chat.html", "claude_chat.html"):
        assert (root / name).is_file()
