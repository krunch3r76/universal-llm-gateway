"""Judgment-tier NOW rows — unread ear/judgment on the continuity root.

Detects cdp reply, SCORE_RESURFACE, and cursor-sdk CLOSEOUT turns that must
outrank stale ``policy.now_row`` binds (maestro-deafness-now).

**v1 detected classes:** ``cdp reply``, ``SCORE_RESURFACE``, ``cursor-sdk CLOSEOUT``.

**Documented omissions (v1 — not judgment rows):**

- ``cursor-auto CLOSEOUT`` subjects (different relay shape)
- ``TYPE: DIRECTIVE`` / ``RULING`` / ``DISPOSITION`` first-line body markers
- ``consult complete`` harvest subjects
- ``cdp reply`` on ask-only results (accepted false positive when subject matches)
- Verdict-prose subjects such as ``G4 SKEPTIC verdict …`` or ``cdp FAILED …``
- Senders outside ``{web-anthropic, operator, cursor}`` (e.g. ``dispatch``,
  ``conductor-hop`` relay traffic on the root)
"""

from __future__ import annotations

import re
from typing import Any

_JUDGMENT_FROM = frozenset({"web-anthropic", "operator", "cursor"})
_JUDGMENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^cdp reply", re.I), "cdp_reply"),
    (re.compile(r"SCORE_RESURFACE", re.I), "score_resurface"),
    (re.compile(r"cursor-sdk CLOSEOUT", re.I), "closeout"),
)
_EXCLUDED_SUBJECT_RES = (
    re.compile(r"^CHECKPOINT", re.I),
    re.compile(r"cursor-sdk generate", re.I),
    re.compile(r"^DIGEST", re.I),
)
_SUBJECT_CHARS = 56
_HARVEST_CAP = 5


def _turn_subject(turn: dict[str, Any]) -> str:
    return str(turn.get("subject") or "")


def _turn_from(turn: dict[str, Any]) -> str:
    return str(turn.get("from") or turn.get("from_agent") or "").strip()


def is_judgment_turn(turn: dict[str, Any], *, root_id: str) -> bool:
    """True when ``turn`` is an eligible unread judgment on ``root_id``."""
    if turn.get("read_at") is not None:
        return False
    if str(turn.get("status") or "").lower() == "superseded":
        return False
    thread = str(turn.get("thread") or root_id)
    if thread != str(root_id):
        return False
    subject = _turn_subject(turn)
    if any(pat.search(subject) for pat in _EXCLUDED_SUBJECT_RES):
        return False
    sender = _turn_from(turn)
    if sender not in _JUDGMENT_FROM:
        return False
    return any(pat.search(subject) for pat, _ in _JUDGMENT_PATTERNS)


def _format_pointer(root_id: str, turn: dict[str, Any]) -> str:
    turn_no = turn.get("turn_number")
    subject = _turn_subject(turn)[:_SUBJECT_CHARS]
    return f"{root_id}#{turn_no} — {subject}"


def _eligible_judgment_turns(
    digest: dict[str, Any],
    *,
    now_row_set_at: str | None = None,
) -> list[dict[str, Any]]:
    root = digest.get("root") or {}
    root_id = str(root.get("id") or "")
    if not root_id:
        return []
    raw = root.get("unread_turns")
    if not isinstance(raw, list):
        raw = root.get("recent_turns") or []
    out: list[dict[str, Any]] = []
    for turn in raw:
        if not isinstance(turn, dict):
            continue
        if not is_judgment_turn(turn, root_id=root_id):
            continue
        if now_row_set_at:
            created = str(turn.get("created_at") or "")
            if created and created <= now_row_set_at:
                continue
        out.append(turn)
    out.sort(key=lambda t: int(t.get("turn_number") or 0))
    return out


def judgment_now_row(digest: dict[str, Any]) -> tuple[str, str] | None:
    """Newest eligible unread judgment turn on root. Returns (raw_pointer, 'judgment')."""
    set_at = digest.get("now_row_set_at")
    if set_at is None:
        policy = digest.get("policy") or {}
        set_at = policy.get("now_row_set_at")
    turns = _eligible_judgment_turns(digest, now_row_set_at=str(set_at or "") or None)
    if not turns:
        return None
    tip = turns[-1]
    root_id = str((digest.get("root") or {}).get("id") or "")
    extra = len(turns) - 1
    pointer = _format_pointer(root_id, tip)
    if extra:
        pointer = f"{pointer} (+{extra} more)"
    return pointer, "judgment"


def harvest_judgment_turns(
    root_id: str,
    *,
    get_turns: Any,
    post_checkpoint: Any,
    mark_read: Any,
    now_row_set_at: str | None = None,
) -> dict[str, Any]:
    """Harvest unread judgment turns onto the root before ide-hop (C-R2-2).

    ``get_turns`` — callable(thread, unread=..., last=...) -> dict with turns list.
    ``post_checkpoint`` — callable(root_id, body) -> bool (durable write ok).
    ``mark_read`` — callable(root_id, turn_numbers) -> None.
    """
    payload = get_turns(root_id, unread=True, last=25) or {}
    turns = list(payload.get("turns") or [])
    if not turns:
        payload = get_turns(root_id, last=25) or {}
        turns = [t for t in (payload.get("turns") or []) if t.get("read_at") is None]
    eligible = [
        t
        for t in turns
        if isinstance(t, dict) and is_judgment_turn(t, root_id=root_id)
    ]
    if now_row_set_at:
        eligible = [
            t
            for t in eligible
            if str(t.get("created_at") or "") > now_row_set_at
        ]
    eligible.sort(key=lambda t: int(t.get("turn_number") or 0))
    harvested = eligible[-_HARVEST_CAP:]
    if not harvested:
        return {"ok": True, "harvested": [], "marked_read": []}
    lines = [
        f"HARVEST judgment {root_id}#{t.get('turn_number')} subject={_turn_subject(t)}"
        for t in harvested
    ]
    body = "\n".join(lines)
    if not post_checkpoint(root_id, body):
        return {"ok": False, "error": "checkpoint_write_failed", "harvested": []}
    turn_numbers = [int(t["turn_number"]) for t in harvested if t.get("turn_number")]
    if turn_numbers:
        mark_read(root_id, turn_numbers)
    return {"ok": True, "harvested": turn_numbers, "marked_read": turn_numbers}


__all__ = [
    "harvest_judgment_turns",
    "is_judgment_turn",
    "judgment_now_row",
]
