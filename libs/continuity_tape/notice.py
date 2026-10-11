"""Harness notice rows. Shared so continuity_tape does not import agent_bus_store."""

from __future__ import annotations

import re

_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_GROK_TURN_WRAP = re.compile(r"^\[t\d+u\]\s*(?:\[Sent from machine [^\]]+\]\s*)?")

# Applied after ``<user_query>`` unwrap. Grok prefixes are the 14106 specimen.
NOTICE_PREFIXES = (
    "[GROK_BOT_HIDDEN_PROMPT",
    "[SAND_HIDDEN_PROMPT",
    "[agent]",
    "[A background task",
    "[event",
    "<agent_profile_update>",
    "<instructions_update>",
)


def unwrap_operator_utterance(content: str) -> str:
    """Person's words, without the timestamp wrapper around ``user_query``."""
    match = _USER_QUERY.search(content)
    text = match.group(1) if match else content
    text = _GROK_TURN_WRAP.sub("", text.lstrip(), count=1)
    return " ".join(text.split())


def is_notice_row(role: str, content: str) -> bool:
    """True when a user row is harness chrome rather than an utterance.

    Assistant rows are never notices. The predicate runs after the
    ``<user_query>`` unwrap, so a ``<timestamp>`` wrapper does not hide a
    hidden-prompt row.
    """
    if role != "user":
        return False
    text = unwrap_operator_utterance(content)
    return any(text.startswith(prefix) for prefix in NOTICE_PREFIXES)
