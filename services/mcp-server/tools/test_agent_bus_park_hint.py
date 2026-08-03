"""Unit tests for advisory poll_hint park_hint gating (G5 / AC6)."""

from __future__ import annotations

from tools.agent_bus.park_hint import (
    build_poll_hint,
    default_park_hint,
    is_chat_delivery_capable,
)


def test_is_chat_delivery_capable_web_anthropic():
    assert is_chat_delivery_capable("web-anthropic") is True
    assert is_chat_delivery_capable("web") is True
    assert is_chat_delivery_capable("claude-web") is True


def test_is_chat_delivery_capable_ide_class_absent():
    assert is_chat_delivery_capable("cursor") is False
    assert is_chat_delivery_capable("") is False


def test_build_poll_hint_includes_park_hint_for_cowork():
    hint = build_poll_hint(
        thread_id="6655",
        after_turn=3,
        from_agent="web-anthropic",
    )
    assert hint["max_expected_latency_s"] == 300
    park = hint.get("park_hint")
    assert park == default_park_hint()
    assert park["park_after_s"] == 300
    assert park["wake"] == "chat_delivery"
    assert park["fallback"] == "bus_wake+pager"
    assert park["record"] == "PARKED"


def test_build_poll_hint_omits_park_hint_for_cursor():
    hint = build_poll_hint(
        thread_id="5867",
        after_turn=1,
        from_agent="cursor",
    )
    assert "park_hint" not in hint


def test_build_poll_hint_wait_arguments_unchanged():
    hint = build_poll_hint(
        thread_id="99",
        after_turn=7,
        from_agent="web-anthropic",
    )
    args = hint["arguments_json"]
    assert args["thread"] == "99"
    assert args["after_turn"] == 7
    assert args["completion"] == "status:done"
    assert args["wait_seconds"] == 0
