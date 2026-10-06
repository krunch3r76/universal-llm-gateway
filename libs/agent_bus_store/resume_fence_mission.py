"""Mission block assembly and bundle shrink helpers (FIX-16..18)."""

from __future__ import annotations

import re
from typing import Any

from cortex_store.transcript_cp_anchors import window_anchors_from_text
from cortex_store.transcript_projection_membership import extract_cp_object

from .checkpoint_projection import CHECKPOINT_SUBJECT_SQL, extract_authored_residue
from .db.connection import connect

_SKETCHBOARD_SUFFIX = "-resume-fence-sketchboard.md"
_INFO_SUBJECT_RE = re.compile(r"\bINFO\b", re.IGNORECASE)
_MUST_READ_SUBJECT_RE = re.compile(
    r"(?i)(?:\bNOTE\b|\bMEMO\b|\bLIAISON\b|\bINFO\b|^TYPE:)",
)
_CLOSEOUT_MEMO_RE = re.compile(r"(?i)^closeout memo")
_UNREAD_LIST_CAP = 24
_UNREAD_BODY_CAP = 8
_UNREAD_BODY_CHARS = 4000


def _sketchboard_in_tip(tip_body: str) -> str | None:
    """Return sketchboard URI only when the tip body explicitly names one."""
    for match in re.finditer(r"cortex://[^\s)\]>`]+", tip_body or ""):
        uri = match.group(0)
        if "sketchboard" in uri:
            return uri
    return None


def sketchboard_uri(thread_id: str, tip_body: str) -> str:
    """Resolve sketchboard URI from tip artifact anchors or house default."""
    found = _sketchboard_in_tip(tip_body)
    if found:
        return found
    return f"cortex://notes/system/threads/{thread_id}{_SKETCHBOARD_SUFFIX}"


def _window_anchor(tip_body: str) -> dict[str, Any] | None:
    anchors = window_anchors_from_text(tip_body or "")
    if not anchors:
        return None
    transcript_id, turns_at_cp = anchors[-1]
    return {
        "transcript_id": transcript_id,
        "turns_at_cp": turns_at_cp,
    }


def house_unread_turns(thread_id: str) -> list[dict[str, Any]]:
    """Unread non-CHECKPOINT turns on the house root for resume pickup.

    Compact list always. Inline ``body`` for NOTE/MEMO/LIAISON/INFO/TYPE
    (not closeout-memo noise). ``from=self`` does not drop a NOTE.
    """
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT turn_number, from_agent, to_agent, subject, body
            FROM turns
            WHERE thread = ?
              AND read_at IS NULL
              AND status != 'superseded'
              AND NOT ({CHECKPOINT_SUBJECT_SQL})
            ORDER BY turn_number ASC
            LIMIT ?
            """,
            (thread_id, _UNREAD_LIST_CAP),
        ).fetchall()
    items: list[dict[str, Any]] = []
    bodies = 0
    for row in rows:
        subject = str(row["subject"] or "")
        body = str(row["body"] or "")
        entry: dict[str, Any] = {
            "turn": int(row["turn_number"]),
            "from": str(row["from_agent"] or ""),
            "to": str(row["to_agent"] or ""),
            "subject": subject,
            "body": None,
        }
        must_read = bool(
            _MUST_READ_SUBJECT_RE.search(subject) or body.lstrip().startswith("TYPE:")
        )
        closeout_noise = bool(_CLOSEOUT_MEMO_RE.match(subject))
        if must_read and not closeout_noise and bodies < _UNREAD_BODY_CAP:
            entry["body"] = body[:_UNREAD_BODY_CHARS]
            bodies += 1
        items.append(entry)
    return items


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
    thread_slug: str | None = None,
) -> dict[str, Any]:
    """Hoist orientation fields + clone handoff into ``mission`` (FIX-18)."""
    residue = extract_authored_residue(tip_body or "")
    window = _window_anchor(tip_body or "")
    tip_sketchboard = _sketchboard_in_tip(tip_body or "")
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

    steps: list[str] = ["continuity(op=resume) was first hop"]
    if thread_slug:
        steps.append(f"rename_chat → `{thread_id} {thread_slug}`")
    if tip_sketchboard:
        steps.append(f"read sketchboard {tip_sketchboard}")
    steps.extend(
        [
            "fold bus_tail INFO turns",
            (
                "read mission.house_unread; get() omitted ¬CHECKPOINT "
                "bodies; empty list ⇒ fetch_unread(thread=T, compact=true) once"
            ),
            (
                "orientation: aim, Where we left off, already, object, "
                "Are/Going, In one line; receipts after"
            ),
        ]
    )

    mission: dict[str, Any] = {
        "highlight": envelope.get("checkpoint_highlight"),
        "object": envelope.get("checkpoint_object")
        if "checkpoint_object" in envelope
        else extract_cp_object(tip_body or ""),
        "tape_tail": envelope.get("tape_tail") or [],
        "tape_left_off_gap": envelope.get("tape_left_off_gap"),
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
        "house_unread": house_unread_turns(thread_id),
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
                "parallel WIP outside this root's scoreboard",
                "new implement until oriented",
            ],
            "steps": steps,
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
    "house_unread_turns",
    "mission_marker_preview",
    "sketchboard_uri",
]
