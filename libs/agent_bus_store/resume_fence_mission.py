"""Mission block assembly and bundle shrink helpers (FIX-16..18)."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from cortex_store.transcript_cp_anchors import window_anchors_from_text
from cortex_store.transcript_projection_membership import extract_cp_object

from agent_bus_store.resume_fence_memos import build_memos_to_check

from .checkpoint_projection import CHECKPOINT_SUBJECT_SQL, extract_authored_residue
from .continuity_card_scratchboards import extract_card_rule_rows, extract_card_skills
from .db.connection import connect
from .house_route_control import (
    _line_value,
    _subject_head_kind,
    _subject_work_key,
    parse_route_control_envelope,
)
from .recipients import MEMO_NOTE_SUBJECT_RE
from .tape_verbal import (
    TAPE_LEFT_OFF_GAP,
    TAPE_LEFT_OFF_LETTER,
    is_notice_row,
    tape_tail,
)

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
_SIDECAR_LINE_RE = re.compile(r"(?m)^Sidecar:\s*(cortex://notes/\S+)\s*$")
_DERIVED_SERVER_HEADER = "## Derived (server-owned)"
_PRIOR_CHECKPOINTS_HEADER = "### Prior checkpoints"
_PREAMBLE_SECTION_RE = {
    "stance": re.compile(r"(?ms)^## Stance\s*\n(.*?)(?=^## |\Z)"),
    "facts": re.compile(r"(?ms)^## Facts\s*\n(.*?)(?=^## |\Z)"),
    "next_action": re.compile(r"(?ms)^## Next action\s*\n(.*?)(?=^## |\Z)"),
}
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
# cortex_store.routes.assertions._shared._VALID_REVIEW_STATUS. There is no
# withdrawn review_status. Expiry is valid_until
# (cortex_store.dispatch_ops._todo_gate_distillation_impl._retract_assertion).
_REJECTED_REVIEW_STATUS = "rejected"

logger = logging.getLogger(__name__)

DEFAULT_ROOT_SKILLS: tuple[str, ...] = ("lean-dispatch", "ide-continuity")

_STANDING_RULES_STEP = (
    "Load mission.standing_rules (verbatim rows and resolved claims) "
    "before any other action"
)
_LETTER_BOUND_STEP = (
    "read bundle.letter.body first — it is the predecessor's letter; "
    "tip and tape confirm facts"
)
_ORIENTATION_STEP = (
    "orientation: aim, Where we left off, Rules I'm holding, already, object, "
    "Are/Going, In one line; receipts after"
)
_RULES_HELD_STEP = (
    "Rules I'm holding: say mission.rules_held.rows in brief (source {source}); "
    "never skip when the letter is bound"
)
_RULINGS_STEP = (
    "read bundle.rulings.items (turn > {tip}) — each is newer than the letter "
    "and overrides the letter ruling it names"
)
_RULINGS_QUERY_LIMIT = 24
_RULINGS_ITEM_CAP = 6
_RULING_BODY_CHARS = 1200
_LOAD_BEARING_ROWS = 12
_LOAD_BEARING_CHARS = 240
_SUPERSEDES_RULING_RE = re.compile(r"rulings\[(\d+)\]")
_LETTER_STALE_STEP = (
    "read bundle.letter.body as intent dated as_of_message_index "
    "({messages_behind} messages behind the seal); tip and tape for later facts"
)


def _tip_sidecar_search_region(tip_body: str) -> str:
    """Authored checkpoint head only — never treat derived history as the tip sidecar."""
    region = tip_body or ""
    for marker in (_DERIVED_SERVER_HEADER, _PRIOR_CHECKPOINTS_HEADER):
        idx = region.find(marker)
        if idx >= 0:
            region = region[:idx]
    return region


def extract_tip_preamble(tip_body: str) -> dict[str, Any] | None:
    """Load stance/facts/next_action from the tip turn's own Sidecar markdown file."""
    region = _tip_sidecar_search_region(tip_body)
    match = _SIDECAR_LINE_RE.search(region)
    if not match:
        return None
    uri = match.group(1).strip()
    rel = uri.removeprefix("cortex://")
    try:
        from implement_admission.closeout_helpers import cortex_files_root

        path = cortex_files_root() / rel
    except Exception:  # noqa: BLE001
        return None
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    content = _FRONTMATTER_RE.sub("", raw, count=1).strip()
    parsed: dict[str, Any] = {"sidecar_uri": uri, "raw": content}
    for key, pattern in _PREAMBLE_SECTION_RE.items():
        section = pattern.search(content)
        if section:
            parsed[key] = section.group(1).strip()
    if not any(parsed.get(k) for k in ("stance", "facts", "next_action")):
        return None
    return parsed


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


def _letter_handoff_steps(
    letter: dict[str, Any] | None,
    *,
    tip_turn: int,
    rulings_count: int,
) -> list[str]:
    if not letter or not isinstance(letter, dict):
        return []
    extra: list[str] = []
    binding = letter.get("binding")
    if binding == "bound":
        extra.append(_LETTER_BOUND_STEP)
        if rulings_count > 0:
            extra.append(_RULINGS_STEP.format(tip=tip_turn))
    elif binding == "stale" and letter.get("body") is not None:
        behind = letter.get("messages_behind")
        if isinstance(behind, int):
            extra.append(_LETTER_STALE_STEP.format(messages_behind=behind))
        else:
            extra.append(
                "read bundle.letter.body as intent dated as_of_message_index "
                "(unknown messages behind the seal); tip and tape for later facts"
            )
    lag = letter.get("lag_turns")
    if isinstance(lag, int) and lag > 2:
        extra.append(
            f"letter was written {lag} turns before the seal; check the tape tail"
        )
    return extra


def _mission_letter_preview(letter: dict[str, Any] | None) -> dict[str, Any] | None:
    if not letter or not isinstance(letter, dict):
        return None
    body = letter.get("body") if isinstance(letter.get("body"), dict) else None
    acts = body.get("next_acts") if body else None
    next_act = acts[0] if isinstance(acts, list) and acts else None
    bearing_rows, bearing_truncated = _capped_load_bearing(body)
    preview: dict[str, Any] = {
        "binding": letter.get("binding"),
        "mission": body.get("mission") if body else None,
        "where_we_left_off": body.get("where_we_left_off") if body else None,
        "next_act": next_act,
        "lag_turns": letter.get("lag_turns"),
        "messages_behind": letter.get("messages_behind"),
        "load_bearing": bearing_rows,
        "load_bearing_truncated": bearing_truncated,
    }
    if body and "rulings_superseded" in body:
        preview["rulings_superseded"] = body.get("rulings_superseded")
    return preview


def _capped_load_bearing(
    body: dict[str, Any] | None,
) -> tuple[list[str], bool]:
    raw = body.get("load_bearing") if body else None
    if not isinstance(raw, list):
        return [], False
    texts = [item for item in raw if isinstance(item, str)]
    truncated = len(texts) > _LOAD_BEARING_ROWS
    capped: list[str] = []
    for item in texts[:_LOAD_BEARING_ROWS]:
        if len(item) > _LOAD_BEARING_CHARS:
            capped.append(item[:_LOAD_BEARING_CHARS])
            truncated = True
        else:
            capped.append(item)
    return capped, truncated


def _rules_held(
    letter: dict[str, Any] | None,
    standing_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Single field the seat reads for "Rules I'm holding"."""
    body = None
    binding = None
    if isinstance(letter, dict):
        binding = letter.get("binding")
        if isinstance(letter.get("body"), dict):
            body = letter["body"]
    use_letter = binding == "bound" or (binding == "stale" and body is not None)
    if use_letter and body is not None:
        raw = body.get("load_bearing")
        rows = (
            [item for item in raw if isinstance(item, str)]
            if isinstance(raw, list)
            else []
        )
        return {"source": "letter.load_bearing", "rows": rows, "truncated": False}
    texts = [
        str(row.get("text") or "")
        for row in standing_rules
        if isinstance(row, dict) and str(row.get("text") or "")
    ]
    if texts:
        return {"source": "card.standing_rules", "rows": texts, "truncated": False}
    return {"source": "none", "rows": [], "truncated": False}


def _supersedes_letter_index(body: str, rulings: object) -> int | None:
    raw = _line_value(body, "supersedes")
    if not raw:
        return None
    match = _SUPERSEDES_RULING_RE.search(raw)
    if match is None:
        return None
    index = int(match.group(1))
    if isinstance(rulings, list) and index < len(rulings):
        return index
    return None


def build_rulings_block(
    thread_id: str,
    *,
    tip_turn: int,
    tip_created_at: str | None,
    letter: dict[str, Any],
) -> dict[str, Any]:
    """RULING turns on the root strictly after the tip that cites the letter.

    Connect failures propagate, same as ``house_unread_turns``. Selection is
    the transaction snapshot: a concurrent post is in or out by turn number.
    """
    body = letter.get("body") if isinstance(letter.get("body"), dict) else None
    stamped = body.get("stamped_at") if body else None
    letter_as_of = stamped if isinstance(stamped, str) and stamped else tip_created_at
    letter_rulings = body.get("rulings") if body else None
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT turn_number, from_agent, to_agent, subject, body, created_at
            FROM turns
            WHERE thread = ? AND turn_number > ? AND status != 'superseded'
            ORDER BY turn_number DESC
            LIMIT ?
            """,
            (thread_id, int(tip_turn), _RULINGS_QUERY_LIMIT),
        ).fetchall()
    matched: list[dict[str, Any]] = []
    for row in rows:
        subject = str(row["subject"] or "")
        if _subject_head_kind(subject) != "RULING":
            continue
        turn_body = str(row["body"] or "")
        parsed, _missing = parse_route_control_envelope(
            from_agent=str(row["from_agent"] or ""),
            subject=subject,
            body=turn_body,
        )
        if parsed is None:
            authority = None
            work_key = _subject_work_key(subject)
            read_shas = None
            envelope = "malformed"
        else:
            authority = parsed.authority
            work_key = parsed.work_key
            read_shas = parsed.read_shas
            envelope = "ok"
        turn = int(row["turn_number"])
        matched.append(
            {
                "turn": turn,
                "ref": f"agent-bus:{thread_id}#{turn}",
                "from": str(row["from_agent"] or ""),
                "to": str(row["to_agent"] or ""),
                "subject": subject,
                "work_key": work_key,
                "authority": authority,
                "read_shas": read_shas,
                "envelope": envelope,
                "created_at": row["created_at"],
                "newer_than_letter": True,
                "supersedes_letter_ruling": _supersedes_letter_index(
                    turn_body, letter_rulings
                ),
                "body": turn_body[:_RULING_BODY_CHARS],
            }
        )
    truncated = len(matched) > _RULINGS_ITEM_CAP
    items = list(reversed(matched[:_RULINGS_ITEM_CAP]))
    return {
        "as_of_turn": int(tip_turn),
        "letter_as_of": letter_as_of,
        "count": len(items),
        "truncated": truncated,
        "items": items,
    }


def _notice_rows_dropped(verbal: list[Any]) -> int:
    dropped = 0
    for message in verbal:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if is_notice_row(str(message.get("role") or ""), content):
            dropped += 1
    return dropped


def _bound_tape_tail(
    letter: dict[str, Any],
    envelope: dict[str, Any],
) -> tuple[list[dict[str, str]], str | None, str, int]:
    """Recompute the tail only when the letter is bound and verbal rows exist."""
    verbal = envelope.get("tape_verbal")
    gap = envelope.get("tape_left_off_gap")
    tail = envelope.get("tape_tail") or []
    if not isinstance(tail, list):
        tail = []
    if not isinstance(letter, dict) or letter.get("binding") != "bound":
        return tail, gap, ("gap" if gap else "tape"), 0
    if not isinstance(verbal, list):
        return tail, gap, "gap", 0
    tail = tape_tail(verbal, exclude_notices=True)
    dropped = _notice_rows_dropped(verbal)
    if tail:
        return tail, gap, "tape_filtered", dropped
    body = letter.get("body") if isinstance(letter.get("body"), dict) else None
    where = body.get("where_we_left_off") if body else None
    if isinstance(where, str) and where.strip():
        return [], TAPE_LEFT_OFF_LETTER, "letter", dropped
    return [], TAPE_LEFT_OFF_GAP, "gap", dropped


_PROTOCOL_NOTE = (
    "Generic resume protocol defaults; not arc constraints. "
    "The letter (bundle.letter.body) governs scope, constraints and next acts."
)


def _handoff_steps_structured(
    steps: list[str],
    *,
    preamble: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Numbered handoff steps; inject read_preamble immediately after rename_chat."""
    structured: list[dict[str, Any]] = []
    for index, detail in enumerate(steps, start=1):
        structured.append({"step": index, "action": "handoff", "detail": detail})
    if not preamble:
        return structured
    read_detail = (
        "Read mission.preamble (stance -> facts -> next_action) before any reference "
        "section; mission.next_action is repeated at the bundle tail"
    )
    insert_at = next(
        (i + 1 for i, row in enumerate(structured) if "rename_chat" in row["detail"]),
        len(structured),
    )
    structured.insert(
        insert_at,
        {"step": insert_at + 1, "action": "read_preamble", "detail": read_detail},
    )
    for offset, row in enumerate(structured):
        row["step"] = offset + 1
    return structured


def _mission_handoff(
    thread_id: str,
    steps: list[str],
    letter: dict[str, Any] | None,
    *,
    preamble: dict[str, Any] | None = None,
) -> dict[str, Any]:
    protocol = {
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
        "handoff_steps": _handoff_steps_structured(steps, preamble=preamble),
    }
    if isinstance(letter, dict) and letter.get("binding") == "bound":
        return {"protocol_defaults": protocol, "note": _PROTOCOL_NOTE}
    return protocol


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
    letter: dict[str, Any] | None = None,
    rulings: dict[str, Any] | None = None,
    memos: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hoist orientation fields + clone handoff into ``mission`` (FIX-18)."""
    preamble = extract_tip_preamble(tip_body or "")
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

    card_skills = extract_card_skills(card_text or "")
    standing_rules = build_standing_rules(card_text)
    card_has_directives = bool(standing_rules) or bool(card_skills)
    skills = list(dict.fromkeys([*DEFAULT_ROOT_SKILLS, *card_skills]))
    skills_to_use = [
        {"slug": slug, "use_line": f"Use the `{slug}` skill"} for slug in skills
    ]

    steps: list[str] = []
    if standing_rules:
        steps.append(_STANDING_RULES_STEP)
    steps.append("continuity(op=resume) was first hop")
    steps.append(
        "Use each mission.skills_to_use slug now — before orientation "
        "and before any act the slug governs"
    )
    if thread_slug:
        steps.append(f"rename_chat → `{thread_id} {thread_slug}`")
    if tip_sketchboard:
        steps.append(f"read sketchboard {tip_sketchboard}")
    rulings_block: dict[str, Any] | None = rulings
    if (
        rulings_block is None
        and isinstance(letter, dict)
        and letter.get("binding") == "bound"
    ):
        rulings_block = build_rulings_block(
            thread_id,
            tip_turn=tip_turn,
            tip_created_at=None,
            letter=letter,
        )
    rulings_count = int(rulings_block["count"]) if rulings_block else 0
    letter_steps = _letter_handoff_steps(
        letter, tip_turn=tip_turn, rulings_count=rulings_count
    )
    insert_at = 1 if standing_rules else 0
    if letter_steps:
        for offset, step in enumerate(letter_steps):
            steps.insert(insert_at + offset, step)
    if memos is None:
        with connect() as conn:
            tip_row = conn.execute(
                "SELECT from_agent FROM turns WHERE thread = ? AND turn_number = ?",
                (thread_id, tip_turn),
            ).fetchone()
        memos = build_memos_to_check(
            thread_id,
            tip_turn=tip_turn,
            tip_from_agent=str(tip_row["from_agent"]) if tip_row else None,
            supersedes_turn=supersedes_turn,
        )
    memo_counts = memos.get("counts") if isinstance(memos.get("counts"), dict) else {}
    memo_shown = int(memo_counts.get("shown") or 0)
    if memo_shown > 0:
        prev_cp = (memos.get("window") or {}).get("previous_checkpoint_turn")
        steps.insert(
            insert_at + len(letter_steps),
            "check bundle.memos_to_check first: "
            f"{memo_shown} unread memo(s) to {memos.get('seat')} since checkpoint "
            f"#{prev_cp}; read each (get the full turn when body_truncated); "
            "act or say why not; then run memos_to_check.mark_read",
        )
    steps.extend(
        [
            "fold bus_tail INFO turns",
            (
                "read mission.house_unread; get() omitted ¬CHECKPOINT "
                "bodies; empty list ⇒ fetch_unread(thread=T, compact=true) once"
            ),
            _RULES_HELD_STEP.format(
                source=_rules_held(letter, standing_rules)["source"]
            ),
            _ORIENTATION_STEP,
        ]
    )
    if preamble:
        read_step = (
            "read mission.preamble (stance -> facts -> next_action) before any "
            "reference section; mission.next_action is repeated at the bundle tail"
        )
        rename_idx = next(
            (i for i, step in enumerate(steps) if step.startswith("rename_chat")),
            None,
        )
        insert_at = (rename_idx + 1) if rename_idx is not None else 0
        steps.insert(insert_at, read_step)

    tail, gap, tail_source, notices_dropped = _bound_tape_tail(letter or {}, envelope)
    mission: dict[str, Any] = {
        "preamble": preamble,
        "highlight": envelope.get("checkpoint_highlight"),
        "object": envelope.get("checkpoint_object")
        if "checkpoint_object" in envelope
        else extract_cp_object(tip_body or ""),
        "tape_tail": tail,
        "tape_tail_source": tail_source,
        "tape_tail_notices_dropped": notices_dropped,
        "tape_left_off_gap": gap,
        "open_line": open_line,
        "summary_row": envelope.get("consolidate_summary_row"),
        "summary_row_source": envelope.get("summary_row_source"),
        "summary_row_as_of_turn": envelope.get("summary_row_as_of_turn"),
        "resume_open": _extract_resume_open(card_text),
        "pools_row": pools_row,
        "skills_to_use": skills_to_use,
        "card_has_directives": card_has_directives,
        "standing_rules": standing_rules,
        "fence_id": fence_id,
        "residue": residue or None,
        "sketchboard_uri": sketchboard,
        "bus_tail": _bus_tail_turns(
            thread_id,
            after_turn=supersedes_num,
        ),
        "house_unread": house_unread_turns(thread_id),
        "memos_to_check": {
            "shown": memo_shown,
            "memos": int(memo_counts.get("memos") or 0),
            "truncated": int(memo_counts.get("truncated") or 0),
            "refs": [
                item["ref"]
                for item in (memos.get("items") or [])
                if isinstance(item, dict) and item.get("ref")
            ],
        },
        "window_anchor": window,
        "lifecycle": {
            "clone_mode": "B" if window else "A",
            "release": "pour_terminal",
            "fence_path": ["armed", "poured", "released"],
        },
        "handoff": _mission_handoff(thread_id, steps, letter, preamble=preamble),
        "rules_held": _rules_held(letter, standing_rules),
    }
    if preamble:
        mission["next_action"] = preamble.get("next_action")
    if rulings_block is not None:
        mission["rulings_after_letter"] = [
            item["ref"] for item in rulings_block["items"]
        ]
    preview = _mission_letter_preview(letter)
    if preview is not None:
        if rulings_block is not None:
            preview["rulings_superseded_by_bus"] = [
                item["supersedes_letter_ruling"]
                for item in rulings_block["items"]
                if isinstance(item.get("supersedes_letter_ruling"), int)
            ]
        mission["letter"] = preview
    return mission


def mission_marker_preview(mission: dict[str, Any]) -> dict[str, Any]:
    """Lightweight mission slice for hook marker cache (FIX-17)."""
    handoff = mission.get("handoff") if isinstance(mission.get("handoff"), dict) else {}
    defaults = handoff.get("protocol_defaults")
    if isinstance(defaults, dict):
        handoff = defaults
    rules = mission.get("standing_rules")
    letter = mission.get("letter") if isinstance(mission.get("letter"), dict) else {}
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
        "letter_binding": letter.get("binding"),
        "rules_held_source": (mission.get("rules_held") or {}).get("source"),
        "rulings_after_letter_count": len(mission["rulings_after_letter"])
        if isinstance(mission.get("rulings_after_letter"), list)
        else None,
        "memos_to_check_shown": (mission.get("memos_to_check") or {}).get("shown"),
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
) -> tuple[sqlite3.Row, int, bool, int | None]:
    """Walk ``superseded_by`` at most ``_SUPERSEDE_HOPS`` steps. Cycle-safe.

    A successor id that was fetched and has no row stops the walk. The
    fourth value is that missing id. A cycle, a non-integer pointer, or
    a hop-cap stop (id never fetched) returns None for that value.
    """
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
        if nxt not in loaded:
            break
        nxt_row = loaded[nxt]
        if nxt_row is None:
            return current, current_id, moved, nxt
        seen.add(nxt)
        current = nxt_row
        current_id = nxt
        moved = True
    return current, current_id, moved, None


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
    row's claim. When the next id was fetched and the row is missing,
    ``status`` stays ``superseded``, ``current_status`` is ``unresolved``,
    ``dangling_successor`` names that id, and the claim stays the cited
    row's claim. ``current_id`` is set only when the walk already moved
    to a resolved row before that break.
    """
    ids = list(dict.fromkeys(int(i) for i in assertion_ids))
    if not ids:
        return {}
    try:
        from cortex_store.db import cortex_conn

        conn = cortex_conn(read_only=True)
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
        current, current_id, moved, dangling_id = _follow_supersede(
            assertion_id, loaded
        )
        cited_claim = str(row["claim"] or "")
        if dangling_id is not None:
            entry = {
                "id": ref,
                "status": "superseded",
                "current_status": "unresolved",
                "dangling_successor": f"a:{dangling_id}",
                "claim": cited_claim,
                "claim_truncated": False,
            }
            if current_id != assertion_id:
                entry["current_id"] = f"a:{current_id}"
            out[assertion_id] = entry
            continue
        tip_status, tip_note = _row_status(current)
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
            claim, truncated = _truncate_claim(
                str(item.get("claim") or ""), _CLAIM_CHARS
            )
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
        if item.get("dangling_successor"):
            extra["dangling_successor"] = item["dangling_successor"]
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
    "build_rulings_block",
    "build_standing_rules",
    "cap_standing_rule_claims",
    "house_unread_turns",
    "lookup_assertion",
    "lookup_assertions",
    "mission_marker_preview",
    "sketchboard_uri",
]
