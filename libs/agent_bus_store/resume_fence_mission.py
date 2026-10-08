"""Mission block assembly and bundle shrink helpers (FIX-16..18)."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from cortex_store.transcript_cp_anchors import window_anchors_from_text
from cortex_store.transcript_projection_membership import extract_cp_object

from .checkpoint_projection import CHECKPOINT_SUBJECT_SQL, extract_authored_residue
from .continuity_card_scratchboards import extract_card_rule_rows, extract_card_skills
from .db.connection import connect
from .recipients import MEMO_NOTE_SUBJECT_RE

_SKETCHBOARD_SUFFIX = "-resume-fence-sketchboard.md"
_INFO_SUBJECT_RE = re.compile(r"\bINFO\b", re.IGNORECASE)
_MUST_READ_OTHER_RE = re.compile(r"(?i)(?:\bLIAISON\b|\bINFO\b|^TYPE:)")
_CLOSEOUT_MEMO_RE = re.compile(r"(?i)^closeout memo")
_UNREAD_LIST_CAP = 24
_UNREAD_BODY_CAP = 8
_UNREAD_BODY_CHARS = 4000
_ASSERTION_ID_RE = re.compile(r"\ba:(\d+)\b")
_CLAIM_CHARS = 240
_CLAIMS_BUDGET_CHARS = 4000
_SUPERSEDE_HOPS = 5
# cortex_store.routes.assertions._shared._VALID_REVIEW_STATUS. There is no
# withdrawn review_status. Expiry is valid_until
# (cortex_store.dispatch_ops._todo_gate_distillation_impl._retract_assertion).
_REJECTED_REVIEW_STATUS = "rejected"

logger = logging.getLogger(__name__)
_STANDING_RULES_STEP = (
    "Load mission.standing_rules (verbatim rows and resolved claims) "
    "before any other action"
)


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
            MEMO_NOTE_SUBJECT_RE.search(subject)
            or _MUST_READ_OTHER_RE.search(subject)
            or body.lstrip().startswith("TYPE:")
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

    skills = extract_card_skills(card_text or "")
    skills_to_use = [
        {"slug": slug, "use_line": f"Use the `{slug}` skill"} for slug in skills
    ]
    standing_rules = build_standing_rules(card_text)

    steps: list[str] = []
    if standing_rules:
        steps.append(_STANDING_RULES_STEP)
    steps.append("continuity(op=resume) was first hop")
    if skills_to_use:
        steps.append(
            "Use each mission.skills_to_use slug now — before orientation "
            "and before any act the slug governs"
        )
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
        "skills_to_use": skills_to_use,
        "standing_rules": standing_rules,
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
    rules = mission.get("standing_rules")
    return {
        "highlight": mission.get("highlight"),
        "summary_row": mission.get("summary_row"),
        "open_line": mission.get("open_line"),
        "sketchboard_uri": mission.get("sketchboard_uri"),
        "clone_mode": (mission.get("lifecycle") or {}).get("clone_mode"),
        "todo": handoff.get("todo"),
        "skills_to_use": mission.get("skills_to_use") or [],
        "standing_rules_count": len(rules) if isinstance(rules, list) else 0,
        "standing_rules_first": rules[0] if isinstance(rules, list) and rules else None,
    }


def _truncate_claim(claim: str, limit: int) -> tuple[str, bool]:
    if len(claim) <= limit:
        return claim, False
    return claim[:limit].rstrip(), True


def _valid_until_state(row: sqlite3.Row) -> tuple[bool, bool]:
    """Return ``(elapsed, unparseable)`` for ``valid_until``.

    Parse matches ``cortex_store.action_hints``: ``Z`` becomes ``+00:00``,
    naive values are UTC, and an unparseable stamp is not elapsed.
    """
    raw = row["valid_until"]
    if raw is None or str(raw).strip() == "":
        return False, False
    try:
        exp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False, True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return exp < datetime.now(UTC), False


def _row_status(row: sqlite3.Row) -> tuple[str, dict[str, str]]:
    """Status of one assertion row, plus a note when ``valid_until`` will not parse."""
    elapsed, unparseable = _valid_until_state(row)
    note: dict[str, str] = {"valid_until_note": "unparseable"} if unparseable else {}
    if elapsed:
        return "elapsed", note
    review = str(row["review_status"] or "").strip().lower()
    if review == _REJECTED_REVIEW_STATUS:
        return "rejected", note
    return "current", note


def _follow_supersede(
    start: int,
    loaded: dict[int, sqlite3.Row | None],
) -> tuple[sqlite3.Row, int, bool]:
    """Walk ``superseded_by`` at most ``_SUPERSEDE_HOPS`` steps. Cycle-safe."""
    row = loaded[start]
    assert row is not None
    seen = {start}
    current = row
    current_id = start
    moved = False
    for _ in range(_SUPERSEDE_HOPS):
        pointer = current["superseded_by"]
        if pointer is None:
            break
        try:
            nxt = int(pointer)
        except (TypeError, ValueError):
            break
        if nxt in seen:
            break
        nxt_row = loaded.get(nxt)
        if nxt_row is None:
            break
        seen.add(nxt)
        current = nxt_row
        current_id = nxt
        moved = True
    return current, current_id, moved


def _fetch_assertion_rows(
    conn: sqlite3.Connection,
    ids: list[int],
) -> dict[int, sqlite3.Row | None]:
    """One ``IN`` query, then at most five hop queries for ``superseded_by``."""
    loaded: dict[int, sqlite3.Row | None] = {}
    frontier = list(dict.fromkeys(ids))
    for hop in range(_SUPERSEDE_HOPS + 1):
        missing = [i for i in frontier if i not in loaded]
        if missing:
            marks = ",".join("?" * len(missing))
            rows = conn.execute(
                "SELECT id, claim, superseded_by, review_status, valid_until "
                f"FROM assertions WHERE id IN ({marks})",
                missing,
            ).fetchall()
            found = {int(row["id"]): row for row in rows}
            for assertion_id in missing:
                loaded[assertion_id] = found.get(assertion_id)
        if hop >= _SUPERSEDE_HOPS:
            break
        nxt: list[int] = []
        for assertion_id in frontier:
            row = loaded.get(assertion_id)
            if row is None or row["superseded_by"] is None:
                continue
            try:
                pointer = int(row["superseded_by"])
            except (TypeError, ValueError):
                continue
            if pointer not in loaded:
                nxt.append(pointer)
        if not nxt:
            break
        frontier = nxt
    return loaded


def lookup_assertions(assertion_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Resolve many assertion ids on one cortex connection.

    A store or query failure is ``lookup_error`` (logged). A missing row is
    ``unresolved``. A ``superseded_by`` chain (max 5 hops, cycle-safe) keeps
    the cited id and sets ``status`` to ``superseded``. The landed row is
    judged with the same status logic as a direct cite (``current_status``
    is ``current``, ``rejected``, or ``elapsed``). Only a ``current`` tip
    is returned as the claim; a rejected or elapsed tip keeps the cited
    row's claim.
    """
    ids = list(dict.fromkeys(int(i) for i in assertion_ids))
    if not ids:
        return {}
    try:
        from cortex_store.db import cortex_conn

        conn = cortex_conn()
        try:
            loaded = _fetch_assertion_rows(conn, ids)
        finally:
            conn.close()
    except Exception:
        logger.exception("assertion lookup failed")
        return {
            assertion_id: {
                "id": f"a:{assertion_id}",
                "status": "lookup_error",
                "claim": "",
                "claim_truncated": False,
            }
            for assertion_id in ids
        }

    out: dict[int, dict[str, Any]] = {}
    for assertion_id in ids:
        ref = f"a:{assertion_id}"
        row = loaded.get(assertion_id)
        if row is None:
            out[assertion_id] = {
                "id": ref,
                "status": "unresolved",
                "claim": "",
                "claim_truncated": False,
            }
            continue
        current, current_id, moved = _follow_supersede(assertion_id, loaded)
        tip_status, tip_note = _row_status(current)
        cited_claim = str(row["claim"] or "")
        tip_claim = str(current["claim"] or "")
        if moved or row["superseded_by"] is not None:
            entry: dict[str, Any] = {
                "id": ref,
                "status": "superseded",
                "current_status": tip_status,
                "claim": tip_claim if tip_status == "current" else cited_claim,
                "claim_truncated": False,
                **tip_note,
            }
            if current_id != assertion_id:
                entry["current_id"] = f"a:{current_id}"
            out[assertion_id] = entry
            continue
        out[assertion_id] = {
            "id": ref,
            "status": tip_status,
            "claim": tip_claim,
            "claim_truncated": False,
            **tip_note,
        }
    return out


def lookup_assertion(assertion_id: int) -> dict[str, Any]:
    """Resolve one assertion id. See ``lookup_assertions``."""
    return lookup_assertions([assertion_id])[assertion_id]


def cap_standing_rule_claims(
    rules: list[dict[str, Any]],
    *,
    budget: int = _CLAIMS_BUDGET_CHARS,
) -> list[dict[str, Any]]:
    """Shorten claim text until the total is within *budget*.

    Rule rows and assertion entries are kept. Only ``claim`` strings shrink.
    The resume pour calls this so the bundle size path sees ``standing_rules``.
    """
    items: list[dict[str, Any]] = []
    for row in rules:
        for item in row.get("assertions") or []:
            if not isinstance(item, dict):
                continue
            claim, truncated = _truncate_claim(str(item.get("claim") or ""), _CLAIM_CHARS)
            item["claim"] = claim
            item["claim_truncated"] = bool(item.get("claim_truncated")) or truncated
            items.append(item)

    def _total() -> int:
        return sum(len(str(item.get("claim") or "")) for item in items)

    while items and _total() > budget:
        longest = max(items, key=lambda item: len(str(item.get("claim") or "")))
        claim = str(longest.get("claim") or "")
        if not claim:
            break
        overflow = _total() - budget
        cut_to = max(0, len(claim) - overflow)
        if cut_to >= len(claim):
            cut_to = len(claim) - 1
        trimmed, truncated = _truncate_claim(claim, cut_to)
        longest["claim"] = trimmed
        longest["claim_truncated"] = bool(longest.get("claim_truncated")) or truncated
    return rules


def build_standing_rules(card_text: str | None) -> list[dict[str, Any]]:
    """Card ``## Rules`` rows with cited assertions resolved.

    Row text is never dropped. Claims are truncated to the per-claim cap and
    then to ``_CLAIMS_BUDGET_CHARS`` in total. One cortex connection serves
    every id on the card. Resolution does not depend on entity-view recency.
    """
    rows = extract_card_rule_rows(card_text or "")
    pending: list[dict[str, Any]] = []
    wanted: list[tuple[dict[str, Any], str, int]] = []
    for text in rows:
        assertions: list[dict[str, Any]] = []
        seen: set[str] = set()
        entry = {"text": text, "assertions": assertions}
        pending.append(entry)
        for match in _ASSERTION_ID_RE.finditer(text):
            ref = f"a:{match.group(1)}"
            if ref in seen:
                continue
            seen.add(ref)
            wanted.append((entry, ref, int(match.group(1))))
    resolved = lookup_assertions([aid for _entry, _ref, aid in wanted])
    for entry, ref, aid in wanted:
        item = resolved.get(aid) or {
            "id": ref,
            "status": "unresolved",
            "claim": "",
            "claim_truncated": False,
        }
        extra: dict[str, Any] = {}
        if item.get("current_id"):
            extra["current_id"] = item["current_id"]
        if item.get("current_status"):
            extra["current_status"] = item["current_status"]
        if item.get("valid_until_note"):
            extra["valid_until_note"] = item["valid_until_note"]
        entry["assertions"].append(
            {
                "id": ref,
                "status": item.get("status") or "unresolved",
                "claim": str(item.get("claim") or ""),
                "claim_truncated": bool(item.get("claim_truncated")),
                **extra,
            }
        )
    return cap_standing_rule_claims(pending)


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
    "build_standing_rules",
    "cap_standing_rule_claims",
    "house_unread_turns",
    "lookup_assertion",
    "lookup_assertions",
    "mission_marker_preview",
    "sketchboard_uri",
]
