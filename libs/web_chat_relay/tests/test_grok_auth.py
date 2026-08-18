"""Hermetic signed-in / conversation-id probes (no Chrome)."""

from __future__ import annotations

import pytest
from web_chat_relay.grok_session import (
    conversation_id_from_url,
    is_signed_in,
    strip_chrome,
)

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


def test_strip_chrome_drops_activity_chip() -> None:
    text = "Worked for 13s\n\nUnderstood. I'm going to speak directly to Claude now."
    assert strip_chrome(text) == "Understood. I'm going to speak directly to Claude now."


def test_strip_chrome_keeps_last_segment_after_multiple_chips() -> None:
    text = (
        "Worked for 14s\n"
        "Analyzing the estate conflict details\n"
        "+2 more\n"
        "Drafting the response to Kaywan\n"
        "Worked for 14s\n\n"
        "Claude, thanks for the careful recording."
    )
    assert strip_chrome(text) == "Claude, thanks for the careful recording."


def test_strip_chrome_returns_empty_for_chip_only_stub() -> None:
    # Confirmed live 2026-08-17: a mid-tool-call harvest with no answer text
    # yet appended must not be mistaken for a real, relayable reply.
    assert strip_chrome("Working for 4s") == ""


def test_strip_chrome_passes_through_plain_reply() -> None:
    assert strip_chrome("No tool calls here, just an answer.") == (
        "No tool calls here, just an answer."
    )
