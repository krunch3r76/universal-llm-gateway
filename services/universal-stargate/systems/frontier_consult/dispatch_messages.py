"""Dispatch ingress helpers — latest-turn extraction for thread persistence."""

from __future__ import annotations

from typing import Any


def extract_last_user_message(messages: list[dict[str, Any]]) -> str:
    """Return the last non-empty user message content (latest-turn contract)."""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def wire_latest_user_turn(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse caller history to a single user turn for provider ingress."""
    text = extract_last_user_message(messages)
    return [{"role": "user", "content": text}]
