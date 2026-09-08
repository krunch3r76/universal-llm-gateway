"""Verbal tape adapter — LLM-safe {role, content} view of mechanical messages.

Callers: ``render_tape`` when ``format=verbal``. Mechanical extras (transcript_id,
session_id, turn_index / turns@cp, window_whole, bus_turn_id, transcript_span)
stay on ``messages``; this adapter never mutates the source dicts. Index-role
overflow rows remain on the verbal tape — role stays ``index``, extras drop.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

VERBAL_KEYS = ("role", "content")


def to_verbal_message(message: Mapping[str, Any]) -> dict[str, str]:
    """Return a strict ``{role, content}`` copy; extra keys are dropped.

    Index-role overflow rows are kept (not converted or omitted). Missing
    role/content become empty strings so degraded rows still serialize.
    """
    return {
        "role": str(message.get("role") or ""),
        "content": str(message.get("content") or ""),
    }


def to_verbal_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Map a mechanical messages list to verbal copies, preserving order."""
    return [to_verbal_message(message) for message in messages]


__all__ = ["VERBAL_KEYS", "to_verbal_message", "to_verbal_messages"]
