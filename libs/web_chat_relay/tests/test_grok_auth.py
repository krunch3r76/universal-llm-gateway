"""Hermetic signed-in / conversation-id probes (no Chrome)."""

from __future__ import annotations

import pytest
from web_chat_relay.grok_session import conversation_id_from_url, is_signed_in

pytestmark = pytest.mark.offline


def test_conversation_id_from_url() -> None:
    url = "https://grok.com/c/47794c69-9fcc-4481-b1a6-f6c9cbf8b768?rid=abc"
    assert conversation_id_from_url(url) == "47794c69-9fcc-4481-b1a6-f6c9cbf8b768"
    assert conversation_id_from_url("https://accounts.google.com/") == ""


def test_is_signed_in_rejects_login_wall() -> None:
    probe = {
        "url": "https://grok.com/c/47794c69-9fcc-4481-b1a6-f6c9cbf8b768",
        "login_wall": True,
        "composer_count": 1,
        "body_start": "This chat is private\nSign in to request access",
    }
    assert not is_signed_in(probe)


def test_is_signed_in_rejects_google_identifier() -> None:
    probe = {
        "url": "https://accounts.google.com/v3/signin/identifier",
        "login_wall": False,
        "composer_count": 0,
        "body_start": "Email or phone",
    }
    assert not is_signed_in(probe)


def test_is_signed_in_accepts_chat() -> None:
    probe = {
        "url": "https://grok.com/c/47794c69-9fcc-4481-b1a6-f6c9cbf8b768",
        "login_wall": False,
        "composer_count": 1,
        "body_start": "What is the weather in Austin?",
    }
    assert is_signed_in(probe)
