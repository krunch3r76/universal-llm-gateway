"""Ordered multi-turn CSE DOM harvest — bounded read-only extraction.

``coverage`` is ``full`` only when the observable says so: a complete sweep
pass added zero new turns, no load-more button remained, at least one user
turn was seen, nothing was streaming, and the caller's ``limit`` did not
truncate the result. Every other outcome is ``tail`` and carries a
``coverage_reason`` naming what blocked ``full`` — a label with no basis is
the same defect in either direction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_bundles.chat_reply_wait import _in_flight
from claude_bundles.project_ask import strip_thinking_prefix

# The harvest routine lives beside this module as real JavaScript so it can be
# syntax-checked and diffed as JS rather than as a Python string literal.
CSE_TURNS_JS = (Path(__file__).with_suffix(".js")).read_text(encoding="utf-8")


async def harvest_turns(
    page,
    *,
    limit: int = 10,
    after_turn: int | None = None,
) -> dict[str, Any]:
    """Evaluate ``CSE_TURNS_JS`` and normalize assistant turn bodies."""
    raw = await page.evaluate(
        CSE_TURNS_JS,
        {"limit": limit, "afterTurn": after_turn},
    )
    turns = []
    for row in raw.get("turns") or []:
        text = str(row.get("text") or "")
        author = str(row.get("author") or "assistant")
        if author == "assistant":
            text = strip_thinking_prefix(text)
        turns.append(
            {
                "author": author,
                "timestamp": row.get("timestamp"),
                "text": text,
                "ordinal": row.get("ordinal"),
            }
        )
    raw["turns"] = turns
    raw["in_flight"] = _in_flight(raw)
    raw.pop("incomplete_dom", None)
    user_count = sum(1 for row in turns if row.get("author") == "user")
    raw["user_turn_count"] = user_count
    if user_count == 0 and turns:
        raw["coverage"] = "tail"
        raw["coverage_reason"] = "zero_user_turns"
        raw["zero_user_turns"] = True
        raw["harvest_failed"] = "zero_user_turns"
    return raw
