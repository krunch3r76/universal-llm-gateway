"""Project-scoped ChatGPT URL parsing."""

from __future__ import annotations

from web_tab.conversation_id import conversation_id_from_url


def test_project_scoped_url_parses() -> None:
    url = (
        "https://chatgpt.com/g/g-p-ulg-orion/c/6ae5f6be-0000-0000-0000-000000000001"
    )
    assert conversation_id_from_url(url) == "6ae5f6be-0000-0000-0000-000000000001"


def test_bare_c_url_parses() -> None:
    assert conversation_id_from_url("https://chatgpt.com/c/abc-123") == "abc-123"


def test_non_chat_url_none() -> None:
    assert conversation_id_from_url("https://example.com/c/nope") is None
