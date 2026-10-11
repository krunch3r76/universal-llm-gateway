"""Verbal tape adapter — LLM-safe {role, content} view of mechanical messages.

Uses ``CORE_KEYS`` from ``continuity_tape.messages``. Index overflow rows live
only in ``index[]`` on the tape route — they never appear in ``tape_verbal``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from continuity_tape.messages import CORE_KEYS

# Spoken when the pour cannot quote the last exchange (seal still open, or the
# budget dropped the verbal rows). Copied into operator-posture; do not reword
# one side only.
TAPE_LEFT_OFF_GAP = (
    "the last few turns aren't sealed yet, so I'm going from the checkpoint"
)
# Bound pour whose verbal rows are all notices, and the letter still quotes
# where the work stopped. Copied into operator-posture by the other lane.
TAPE_LEFT_OFF_LETTER = (
    "nothing after the letter but relays; where we left off is letter.where_we_left_off"
)
_SENTENCE_END = re.compile(r"[.!?…](?:\s|$)")
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
# Grok composer wrap on the operator's words. Specimen 14106.
_GROK_TURN_WRAP = re.compile(r"^\[t\d+u\]\s*(?:\[Sent from machine [^\]]+\]\s*)?")
# The operator says this to make the seat save. It is not where the work stopped.
_CHECKPOINT_UTTERANCE = re.compile(r"^/?checkpoint(?:\s+\S+)?\.?$", re.IGNORECASE)
# Applied after ``<user_query>`` unwrap. Grok prefixes are the 14106 specimen.
# Cursor ``<system_notification>`` / ``<system_reminder>`` and claude.ai
# ``TYPE:`` relays were not confirmed against a pour and are not in this tuple.
_NOTICE_PREFIXES = (
    "[GROK_BOT_HIDDEN_PROMPT",
    "[SAND_HIDDEN_PROMPT",
    "[agent]",
    "[A background task",
    "[event",
    "<agent_profile_update>",
    "<instructions_update>",
)


def project_role_content(message: Mapping[str, Any]) -> dict[str, str]:
    """Return a strict ``{role, content}`` copy; extra keys are dropped."""
    return {
        "role": str(message.get("role") or ""),
        "content": str(message.get("content") or ""),
    }


def project_role_content_list(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Map speech messages to role/content copies; omit index-role rows."""
    out: list[dict[str, str]] = []
    for message in messages:
        if str(message.get("role") or "") == "index":
            continue
        verbal = project_role_content(message)
        if set(verbal) <= set(CORE_KEYS):
            out.append(verbal)
    return out


def _trim_exchange(text: str, chars: int) -> str:
    """One or two sentences, capped. A mid-word cut is worse than a short quote."""
    flat = " ".join(text.split())
    if chars < 1 or len(flat) <= chars:
        return flat
    window = flat[:chars]
    end = 0
    for match in _SENTENCE_END.finditer(window):
        end = match.end()
    if end > chars // 3:
        return window[:end].strip()
    return window.rstrip() + "…"


def _operator_utterance(content: str) -> str:
    """The person's words, without the timestamp wrapper around ``user_query``.

    A leading grok turn tag ``[tNu]`` and a following
    ``[Sent from machine <id>]`` marker are composer chrome, not the utterance.
    """
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
    text = _operator_utterance(content)
    return any(text.startswith(prefix) for prefix in _NOTICE_PREFIXES)


def _is_checkpoint_command(content: str) -> bool:
    """True when the row is only the utterance that asks for a checkpoint."""
    return _CHECKPOINT_UTTERANCE.match(_operator_utterance(content)) is not None


def _drop_trailing_checkpoint(
    kept: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Drop a trailing checkpoint command and the reply that only acknowledges it.

    The command is how the save gets made. Quoting it restates the save.
    """
    checkpoint_at: int | None = None
    for index in range(len(kept) - 1, -1, -1):
        row = kept[index]
        if row["role"] == "user":
            if _is_checkpoint_command(row["content"]):
                checkpoint_at = index
            break
        if row["role"] != "assistant":
            break
    if checkpoint_at is None:
        return kept
    if all(
        kept[index]["role"] == "assistant"
        for index in range(checkpoint_at + 1, len(kept))
    ):
        return kept[:checkpoint_at]
    return kept


def tape_tail(
    verbal: Sequence[Mapping[str, Any]],
    *,
    turns: int = 2,
    chars: int = 320,
    exclude_notices: bool = False,
) -> list[dict[str, str]]:
    """Last substantive speech rows of a pour, each trimmed to a sentence or two.

    ``turns`` counts role/content rows from the end, skipping empty content.
    A trailing ``checkpoint`` command and the acknowledgement after it are
    omitted: that pair is how the save was asked for. The resume opening
    quotes this list as "Where we left off."
    """
    kept: list[dict[str, str]] = []
    for message in verbal:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        role = str(message.get("role") or "")
        if exclude_notices and is_notice_row(role, content):
            continue
        kept.append({"role": role, "content": content})
    kept = _drop_trailing_checkpoint(kept)
    tail = kept[-turns:] if turns > 0 else []
    return [
        {
            "role": row["role"],
            "content": _trim_exchange(
                _operator_utterance(row["content"])
                if row["role"] == "user"
                else row["content"],
                chars,
            ),
        }
        for row in tail
    ]


def tape_left_off_gap(
    seal_status: str,
    *,
    verbal_empty: bool,
    degraded: bool,
    truncated: bool,
) -> str | None:
    """Plain sentence when the pour cannot quote the last exchange.

    Only the unsealed and overflow cases. A sealed empty interval is not
    called unsealed.
    """
    if not verbal_empty:
        return None
    if seal_status == "seal_pending" or degraded or truncated:
        return TAPE_LEFT_OFF_GAP
    return None


__all__ = [
    "CORE_KEYS",
    "TAPE_LEFT_OFF_GAP",
    "TAPE_LEFT_OFF_LETTER",
    "is_notice_row",
    "project_role_content",
    "project_role_content_list",
    "tape_left_off_gap",
    "tape_tail",
]
