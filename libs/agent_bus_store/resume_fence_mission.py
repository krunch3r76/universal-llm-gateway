"""Mission block assembly and bundle shrink helpers (FIX-16..18)."""

from __future__ import annotations

import re
from typing import Any

from cortex_store.transcript_cp_anchors import window_anchors_from_text

from .checkpoint_projection import extract_authored_residue
from .db.connection import connect

_SKETCHBOARD_SUFFIX = "-resume-fence-sketchboard.md"
_INFO_SUBJECT_RE = re.compile(r"\bINFO\b", re.IGNORECASE)


def sketchboard_uri(thread_id: str, tip_body: str) -> str:
    """Resolve sketchboard URI from tip artifact anchors or house default."""
    for match in re.finditer(r"cortex://[^\s)\]>`]+", tip_body or ""):
        uri = match.group(0)
        if "sketchboard" in uri:
            return uri
    return f"cortex://notes/system/threads/{thread_id}{_SKETCHBOARD_SUFFIX}"


def _window_anchor(tip_body: str) -> dict[str, Any] | None:
    anchors = window_anchors_from_text(tip_body or "")
    if not anchors:
        return None
    transcript_id, turns_at_cp = anchors[-1]
    return {
        "transcript_id": transcript_id,
        "turns_at_cp": turns_at_cp,
        "scope": "window",
        "prior_cells": 1,
        "harvest": False,
    }


def _bus_tail_turns(
    thread_id: str,
    *,
    after_turn: int | None,
    limit: int = 6,
) -> list[str]:
    """Recent INFO-class turns on the root since *after_turn* (FIX-18 residue)."""
    floor = int(after_turn or 0)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT turn_number, subject
            FROM turns
            WHERE thread = ? AND turn_number > ?
            ORDER BY turn_number DESC
            LIMIT ?
            """,
            (thread_id, floor, limit * 3),
        ).fetchall()
    refs: list[str] = []
    for row in rows:
        subject = str(row["subject"] or "")
        if not _INFO_SUBJECT_RE.search(subject):
            continue
        refs.append(f"{thread_id}#{int(row['turn_number'])}")
        if len(refs) >= limit:
            break
    return list(reversed(refs))


def build_mission_block(
    *,
    thread_id: str,
    tip_body: str,
    tip_turn: int,
    supersedes_turn: int | None,
    card_text: str | None,
    envelope: dict[str, Any],
    pools_row: str | None,
    open_line: str | None,
    fence_id: str,
) -> dict[str, Any]:
    """Hoist orientation fields + clone handoff into ``mission`` (FIX-18)."""
    residue = extract_authored_residue(tip_body or "")
    window = _window_anchor(tip_body or "")
    sketchboard = sketchboard_uri(thread_id, tip_body or "")
    supersedes_num: int | None = None
    if supersedes_turn:
        with connect() as conn:
            row = conn.execute(
                "SELECT turn_number FROM turns WHERE id = ?",
                (supersedes_turn,),
            ).fetchone()
        if row:
            supersedes_num = int(row["turn_number"])

    mission: dict[str, Any] = {
        "highlight": envelope.get("checkpoint_highlight"),
        "open_line": open_line,
        "summary_row": envelope.get("consolidate_summary_row"),
        "summary_row_source": envelope.get("summary_row_source"),
        "summary_row_as_of_turn": envelope.get("summary_row_as_of_turn"),
        "resume_open": _extract_resume_open(card_text),
        "pools_row": pools_row,
        "fence_id": fence_id,
        "residue": residue or None,
        "sketchboard_uri": sketchboard,
        "bus_tail": _bus_tail_turns(
            thread_id,
            after_turn=supersedes_num,
        ),
        "window_anchor": window,
        "lifecycle": {
            "clone_mode": "B" if window else "A",
            "release": "pour_terminal",
            "fence_path": ["armed", "poured", "released"],
        },
        "handoff": {
            "role": "orchestrator_successor",
            "todo": "todo:continuity-resume-fence",
            "question": (
                f"Reconstitute orchestrator dialogue + bus WIP on agent-bus:{thread_id} "
                "from this bundle — not a byte-identical tab clone."
            ),
            "out_of_scope": [
                "parallel WIP (canonical.yaml, treasury scripts)",
                "new FIX implement until oriented",
            ],
            "steps": [
                "continuity(op=resume) was first hop",
                f"rename_chat → `{thread_id} human-continuity-speech-tape-design`",
                f"read sketchboard {sketchboard}",
                "fold bus_tail INFO turns",
                (
                    "tape: agent_bus_read(scope=window, transcript_id=<window_anchor>, "
                    "prior_cells=1) — not scope=full"
                ),
                "orientation: Mission + Been→Are→Going + In one line",
            ],
        },
    }
    return mission


def mission_marker_preview(mission: dict[str, Any]) -> dict[str, Any]:
    """Lightweight mission slice for hook marker cache (FIX-17)."""
    handoff = mission.get("handoff") if isinstance(mission.get("handoff"), dict) else {}
    return {
        "highlight": mission.get("highlight"),
        "summary_row": mission.get("summary_row"),
        "open_line": mission.get("open_line"),
        "sketchboard_uri": mission.get("sketchboard_uri"),
        "clone_mode": (mission.get("lifecycle") or {}).get("clone_mode"),
        "todo": handoff.get("todo"),
    }


def _extract_resume_open(card_text: str | None) -> str | None:
    if not card_text or "## Resume open" not in card_text:
        return None
    chunk = card_text.split("## Resume open", 1)[1]
    for marker in ("## Opportunities", "## Pools", "## Sidecars", "## "):
        if marker in chunk:
            chunk = chunk.split(marker, 1)[0]
            break
    text = chunk.strip()
    return text or None


__all__ = [
    "build_mission_block",
    "mission_marker_preview",
    "sketchboard_uri",
]
