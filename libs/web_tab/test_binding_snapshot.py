"""Binding head reads current.actuation, not envelope top-level fields."""

from __future__ import annotations

from web_tab.binding_snapshot import read_binding_head


def test_binding_head_carries_conversation_from_actuation() -> None:
    """Conversation url and id come from current.actuation, not the envelope top."""
    payload = {
        "state": "bound",
        "as_of": "2026-10-31T00:00:00Z",
        "url": "https://wrong.example/c/nope",
        "conversation_id": "nope",
        "subscription_id": "top-level",
        "current": {
            "subscription_id": "sub-1",
            "actuation": {
                "conversation_url": "https://chatgpt.com/c/abc",
                "conversation_id": "abc",
            },
        },
    }
    head = read_binding_head(house_id="21207", agent="orion", resolve_payload=payload)
    assert head["url"] == "https://chatgpt.com/c/abc"
    assert head["conversation_id"] == "abc"
    assert head["epoch"]["row"] == "sub-1"


def test_top_level_url_without_actuation_is_absent() -> None:
    """A top-level url without current.actuation does not become the binding head."""
    payload = {
        "state": "bound",
        "url": "https://chatgpt.com/c/ignored",
        "conversation_id": "ignored",
    }
    head = read_binding_head(house_id="21207", agent="orion", resolve_payload=payload)
    assert head["url"] is None
    assert head["conversation_id"] is None
