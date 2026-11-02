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


def test_read_reply_js_chatgpt_uses_class_turn_selectors() -> None:
    from web_tab.provider_selectors import read_reply_js

    js = read_reply_js(site="chatgpt", baseline_assistant_count=0)
    assert "text-user-message" in js
    assert "MarkdownRoot" in js
    assert "turn-action-controls" in js
    assert "stop-streaming" in js


def test_verify_committed_js_chatgpt_dedupes_user_turns() -> None:
    js = verify_committed_js(
        site="chatgpt",
        prompt_text="web_tab smoke ping: reply with the single word PONG",
    )
    assert "dedupeTexts" in js
    assert "text-user-message" in js


def test_fixtures_exist_on_disk() -> None:
    root = Path(__file__).resolve().parent / "fixtures"
    from web_tab.config import WEB_TAB_SMOKE_PROVIDER_FIXTURES

    for name in WEB_TAB_SMOKE_PROVIDER_FIXTURES:
        assert (root / name).is_file()
