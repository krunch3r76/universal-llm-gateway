"""Structural resume substrate — last-session verbal tape on root lanes only."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from cortex_store.transcript_projection_membership import extract_cp_highlight

from .checkpoint_windows_render import list_checkpoint_turns
from .db.connection import connect
from .tape_harvest import render_tape_with_harvest
from .tape_render import _find_jsonl_for_uuid
from .tape_verbal import to_verbal_messages

_MECHANICAL_PROJECTION_URI = (
    "cortex://notes/system/threads/{thread}-transcript-projection.md"
)


def _derive_seal_status(open_line: dict[str, Any]) -> str:
    """Classify whether the last-session cell is fully sealed for cross-surface pour."""
    mismatch = open_line.get("mismatch")
    if isinstance(mismatch, list) and mismatch:
        return "seal_pending"
    open_interval = open_line.get("open_interval")
    if isinstance(open_interval, dict):
        turns = int(open_interval.get("turns") or 0)
        transcript_ids = open_interval.get("transcript_ids") or []
        if turns > 0:
            for tid in transcript_ids:
                if _find_jsonl_for_uuid(str(tid)) is None:
                    return "seal_pending"
    return "sealed"


def _cap_turn_index_by_transcript(
    mismatch: list[dict[str, Any]],
) -> dict[str, int]:
    caps: dict[str, int] = {}
    for row in mismatch:
        tid = str(row.get("transcript_id") or "")
        if not tid:
            continue
        sealed_hi = int(row.get("sealed_turn_hi") or 0)
        caps[tid] = max(caps.get(tid, 0), sealed_hi)
    return caps


def _filter_messages_for_seal_pending(
    messages: list[dict[str, Any]],
    open_line: dict[str, Any],
) -> list[dict[str, Any]]:
    """Drop open-tail / unsealed speech when CP anchor exceeds sealed journal."""
    mismatch = open_line.get("mismatch")
    if isinstance(mismatch, list) and mismatch:
        caps = _cap_turn_index_by_transcript(mismatch)
        return [
            msg
            for msg in messages
            if int(msg.get("turn_index") or 0)
            <= caps.get(str(msg.get("transcript_id") or ""), 0)
        ]
    open_interval = open_line.get("open_interval")
    if not isinstance(open_interval, dict):
        return messages
    transcript_ids = open_interval.get("transcript_ids") or []
    open_turns = int(open_interval.get("turns") or 0)
    if not transcript_ids or open_turns <= 0:
        return messages
    if any(_find_jsonl_for_uuid(str(tid)) is not None for tid in transcript_ids):
        return messages
    last_cp = open_line.get("last_cp")
    if not isinstance(last_cp, dict):
        return messages
    turn_hi = int(last_cp.get("turn_hi") or 0)
    transcript_id = str(last_cp.get("transcript_id") or "")
    sealed_cap = max(0, turn_hi - open_turns)
    return [
        msg
        for msg in messages
        if str(msg.get("transcript_id") or "") != transcript_id
        or int(msg.get("turn_index") or 0) <= sealed_cap
    ]


def _tip_checkpoint_body(thread_id: str) -> tuple[int | None, str]:
    cps = list_checkpoint_turns(thread_id=thread_id)
    if not cps:
        return None, ""
    tip = cps[-1]
    with connect() as conn:
        row = conn.execute(
            "SELECT body FROM turns WHERE thread = ? AND turn_number = ?",
            (thread_id, tip.turn_number),
        ).fetchone()
    body = str(row["body"]) if row else ""
    return tip.turn_number, body


def _last_checkpoint_highlight(thread_id: str) -> str | None:
    _, body = _tip_checkpoint_body(thread_id)
    return extract_cp_highlight(body)


def _summary_row_from_tip_cp(body: str) -> str | None:
    """Fold line from tip CHECKPOINT residue when L3 card is unavailable."""
    for pattern in (
        r"(?m)^\*\*Highlight:\*\*\s*(.+)$",
        r"(?m)^In one line:\s*(.+)$",
        r"(?m)^\*\*Going:\*\*\s*(.+)$",
        r"(?m)^\*\*Mission:\*\*\s*(.+)$",
    ):
        match = re.search(pattern, body or "")
        if match:
            text = match.group(1).strip()
            if text:
                return text[:600]
    return None


def _read_l3_summary_row(thread_id: str) -> tuple[str | None, str | None]:
    """Best-effort read of consolidate card summary (L3) from cortex files mount."""
    root = Path(
        os.environ.get("CORTEX_FILES_ROOT", str(Path.home() / "mcp-data/files"))
    )
    card = root / "notes/system/threads" / f"{thread_id}-continuity.md"
    if not card.is_file():
        return None, None
    text = card.read_text(encoding="utf-8", errors="replace")
    for pattern in (
        r"(?m)^\*\*Settled:\*\*\s*(.+)$",
        r"(?m)^\*\*Live:\*\*\s*(.+)$",
        r"(?m)^\*\*Next:\*\*\s*(.+)$",
    ):
        match = re.search(pattern, text)
        if match:
            line = match.group(1).strip()
            if line:
                return line[:600], "l3_continuity_card"
    return None, None


def _resume_summary_row(
    thread_id: str,
    *,
    tip_turn: int | None = None,
    tip_body: str | None = None,
) -> tuple[str | None, str | None, int | None]:
    """summary_row + source + as_of_turn per session-effectiveness G-E1."""
    if tip_turn is None or tip_body is None:
        tip_turn, tip_body = _tip_checkpoint_body(thread_id)
    l3_row, l3_source = _read_l3_summary_row(thread_id)
    if l3_row:
        return l3_row, l3_source, tip_turn
    tip_row = _summary_row_from_tip_cp(tip_body or "")
    if tip_row:
        return tip_row, "tip_checkpoint_residue", tip_turn
    return None, None, tip_turn


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
    open_line = tape.get("open_line") if isinstance(tape.get("open_line"), dict) else {}
    seal_status = _derive_seal_status(open_line)
    messages = tape.get("messages") or []
    if seal_status == "seal_pending":
        messages = _filter_messages_for_seal_pending(messages, open_line)
    verbal = tape.get("verbal_messages")
    if seal_status == "seal_pending" or not isinstance(verbal, list):
        verbal = to_verbal_messages(messages)
    tip_turn, tip_body = _tip_checkpoint_body(thread_id)
    checkpoint_highlight = extract_cp_highlight(tip_body)
    summary_row, summary_row_source, summary_as_of_turn = _resume_summary_row(
        thread_id, tip_turn=tip_turn, tip_body=tip_body
    )
    return {
        "scope": open_line.get("scope") or "last_session",
        "seal_status": seal_status,
        "tape_verbal": verbal,
        "message_count": len(verbal),
        "open_line": open_line,
        "checkpoint_highlight": checkpoint_highlight,
        "mechanical_projection_uri": _MECHANICAL_PROJECTION_URI.format(
            thread=thread_id
        ),
        "word_projection_uri": None,
        "consolidate_summary_row": summary_row,
        "summary_row_source": summary_row_source,
        "summary_row_as_of_turn": summary_as_of_turn,
    }


__all__ = ["build_resume_envelope"]
