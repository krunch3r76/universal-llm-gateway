"""Structural resume substrate — last-session verbal tape on root lanes only."""

from __future__ import annotations

from typing import Any

from .tape_harvest import render_tape_with_harvest
from .tape_verbal import to_verbal_messages

_MECHANICAL_PROJECTION_URI = (
    "cortex://notes/system/threads/{thread}-transcript-projection.md"
)


def build_resume_envelope(thread_id: str) -> dict[str, Any]:
    """Last-session verbal pour + projection pointers (no graph/consolidation)."""
    # Read path: render-only. Harvest is explicit via tape?harvest=true (quick-fail).
    tape = render_tape_with_harvest(
        thread_id=thread_id,
        budget_bytes=512_000,
        harvest=False,
        format="verbal",
        scope="last_session",
    )
    if tape.get("error"):
        return {"error": tape["error"], "reason": "tape_render_failed"}
    verbal = tape.get("verbal_messages")
    if not isinstance(verbal, list):
        verbal = to_verbal_messages(tape.get("messages") or [])
    open_line = tape.get("open_line") if isinstance(tape.get("open_line"), dict) else {}
    return {
        "scope": open_line.get("scope") or "last_session",
        "tape_verbal": verbal,
        "message_count": len(verbal),
        "open_line": open_line,
        "mechanical_projection_uri": _MECHANICAL_PROJECTION_URI.format(
            thread=thread_id
        ),
        "word_projection_uri": None,
        "consolidate_summary_row": None,
    }


__all__ = ["build_resume_envelope"]
