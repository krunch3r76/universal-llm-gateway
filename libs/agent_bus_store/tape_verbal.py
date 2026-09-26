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
_SENTENCE_END = re.compile(r"[.!?…](?:\s|$)")
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
# The operator says this to make the seat save. It is not where the work stopped.
_CHECKPOINT_UTTERANCE = re.compile(r"^/?checkpoint(?:\s+\S+)?\.?$", re.IGNORECASE)


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
    """The person's words, without the timestamp wrapper around ``user_query``."""
    match = _USER_QUERY.search(content)
    text = match.group(1) if match else content
    return " ".join(text.split())


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
        kept.append(
            {
                "role": str(message.get("role") or ""),
                "content": content,
            }
        )
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
    "project_role_content",
    "project_role_content_list",
    "tape_left_off_gap",
    "tape_tail",
]
