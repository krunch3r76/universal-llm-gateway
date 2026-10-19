"""L24/L28 liaison attention — unrelayed K43 replies and dead-letter maestro turns."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bus_watch.fable_lock import _derived_expires_at, fable_lock_path, read_lock

CDP_REPLY_SUBJECT_RE = re.compile(r"^cdp reply\s*[—-]\s*(\S+)", re.I)
EXECUTION_ID_BODY_RE = re.compile(
    r"(?m)(?:^|\s)execution_id:\s*`?([0-9a-f-]{8,36})`?",
    re.I,
)
VERDICT_IN_TURN_RE = re.compile(r"VERDICT\s*:", re.I)
MAESTRO_AMEND_REQUEST_RE = re.compile(r"^(AMEND|REQUEST)\|", re.I)

UNRELAYED_K43_THRESHOLD_S = 900
DEAD_LETTER_MAESTRO_THRESHOLD_S = 1800
_ALERT_SUBJECT_PREFIX = "BLOCKED|"


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _turn_number(turn: dict[str, Any]) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def _turn_text(turn: dict[str, Any]) -> str:
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    return f"{subject}\n{body}"


def _cdp_reply_identity(turn: dict[str, Any]) -> tuple[str | None, str | None]:
    subject = str(turn.get("subject") or "")
    match = CDP_REPLY_SUBJECT_RE.match(subject.strip())
    short_id = match.group(1).strip() if match else None
    execution_ids = turn.get("execution_ids")
    exec_id: str | None = None
    if isinstance(execution_ids, list):
        for item in execution_ids:
            token = str(item or "").strip()
            if token:
                exec_id = token
                break
    if not exec_id:
        body = str(turn.get("body") or "")
        body_match = EXECUTION_ID_BODY_RE.search(body)
        if body_match:
            exec_id = body_match.group(1)
    if not short_id and exec_id:
        short_id = exec_id[:8]
    sender = str(turn.get("from") or turn.get("from_agent") or "").strip()
    if not short_id and sender == "web-anthropic" and exec_id:
        short_id = exec_id[:8]
    if subject.lower().startswith("cdp reply") or (
        sender == "web-anthropic" and exec_id
    ):
        return short_id, exec_id
    return None, None


def _relay_references_reply(
    turn: dict[str, Any], *, short_id: str | None, execution_id: str | None
) -> bool:
    text = _turn_text(turn)
    if not VERDICT_IN_TURN_RE.search(text):
        return False
    needles = [n for n in (short_id, execution_id) if n]
    if not needles:
        return False
    lowered = text.lower()
    return any(n.lower() in lowered for n in needles)


def check_unrelayed_k43_replies(
    turns: list[dict[str, Any]],
    now_epoch: float,
    *,
    threshold_s: int = UNRELAYED_K43_THRESHOLD_S,
) -> list[dict[str, Any]]:
    """Flag cdp reply turns with no VERDICT relay after ``threshold_s`` seconds."""
    ordered = sorted(turns, key=_turn_number)
    alerts: list[dict[str, Any]] = []
    for index, turn in enumerate(ordered):
        if not isinstance(turn, dict):
            continue
        short_id, exec_id = _cdp_reply_identity(turn)
        if not short_id and not exec_id:
            continue
        created = _parse_iso_ts(str(turn.get("created_at") or ""))
        if created is None:
            continue
        age_s = max(0, int(now_epoch - created))
        if age_s < threshold_s:
            continue
        reply_no = _turn_number(turn)
        relayed = False
        for later in ordered[index + 1 :]:
            if _turn_number(later) <= reply_no:
                continue
            if _relay_references_reply(later, short_id=short_id, execution_id=exec_id):
                relayed = True
                break
        if relayed:
            continue
        alerts.append(
            {
                "kind": "unrelayed_k43_reply",
                "turn_number": reply_no,
                "execution_id": exec_id or short_id,
                "age_s": age_s,
                "subject": str(turn.get("subject") or "")[:120],
            }
        )
    return alerts


def _maestro_dead_letter_turn(turn: dict[str, Any]) -> bool:
    if turn.get("read_at") is not None:
        return False
    sender = str(turn.get("from") or turn.get("from_agent") or "").strip()
    if sender != "maestro":
        return False
    subject = str(turn.get("subject") or "")
    return bool(MAESTRO_AMEND_REQUEST_RE.match(subject.strip()))


def _seat_lock_blocks_alert(
    lock: dict[str, Any], root_id: str, now_epoch: float
) -> bool:
    holder = str(lock.get("holder") or "").strip()
    if not holder:
        return False
    expires = _derived_expires_at(
        lock, lock_path=fable_lock_path(root_id) if root_id else None
    )
    if expires is None:
        return True
    return now_epoch <= expires


def check_dead_letter_maestro_turns(
    turns_by_root: dict[str, list[dict[str, Any]]],
    locks_by_root: dict[str, dict[str, Any]],
    now_epoch: float,
    *,
    threshold_s: int = DEAD_LETTER_MAESTRO_THRESHOLD_S,
    alerted_state: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Emit one alert per unread maestro AMEND/REQUEST when the seat lock is free."""
    state = dict(alerted_state or {})
    alerts: list[dict[str, Any]] = []
    for thread_id, turns in turns_by_root.items():
        lock = locks_by_root.get(thread_id) or {}
        if _seat_lock_blocks_alert(lock, thread_id, now_epoch):
            continue
        seen = set(state.get(thread_id) or [])
        for turn in turns or []:
            if not isinstance(turn, dict) or not _maestro_dead_letter_turn(turn):
                continue
            turn_no = _turn_number(turn)
            turn_key = f"{thread_id}#{turn_no}"
            if turn_key in seen:
                continue
            created = _parse_iso_ts(str(turn.get("created_at") or ""))
            if created is None:
                continue
            age_s = max(0, int(now_epoch - created))
            if age_s < threshold_s:
                continue
            seen.add(turn_key)
            alerts.append(
                {
                    "kind": "dead_letter_maestro_turn",
                    "thread": thread_id,
                    "turn_number": turn_no,
                    "age_s": age_s,
                    "alert_subject_prefix": _ALERT_SUBJECT_PREFIX,
                }
            )
        if seen:
            state[thread_id] = sorted(seen)
    return alerts, state


def l28_scan_root_ids(
    root_id: str,
    lanes: list[dict[str, Any]],
    policy: dict[str, Any],
) -> set[str]:
    """House root, child lane roots, and configured sibling roots for L28 scan."""
    roots: set[str] = {root_id}
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("id") or "").strip()
        if lane_id:
            roots.add(lane_id)
    sibling = policy.get("sibling_roots")
    if sibling is None and root_id == "16416":
        sibling = ("18374", "15851")
    if isinstance(sibling, (list, tuple, set, frozenset)):
        for item in sibling:
            token = str(item or "").strip()
            if token:
                roots.add(token)
    return roots


def build_l28_scan_context(
    client: Any,
    bus_get: Any,
    *,
    root_id: str,
    root_recent_turns: list[dict[str, Any]],
    lanes: list[dict[str, Any]],
    policy: dict[str, Any],
    last_turns: int = 25,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Fetch recent turns and seat locks for every L28 scan root."""
    scan_ids = l28_scan_root_ids(root_id, lanes, policy)
    turns_by_root: dict[str, list[dict[str, Any]]] = {
        root_id: list(root_recent_turns or []),
    }
    locks_by_root: dict[str, dict[str, Any]] = {}
    for thread_id in scan_ids:
        if thread_id not in turns_by_root:
            payload = bus_get(client, "/turns", thread=thread_id, last=last_turns) or {}
            turns_by_root[thread_id] = list(payload.get("turns") or [])
        locks_by_root[thread_id] = read_lock(thread_id)
    return turns_by_root, locks_by_root


def extend_digest_attention_l24_l28(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    turns_by_root: dict[str, list[dict[str, Any]]] | None = None,
    locks_by_root: dict[str, dict[str, Any]] | None = None,
    now_epoch: float | None = None,
) -> None:
    """Append L24/L28 attention items; mutates ``digest`` and ``state``."""
    import time

    now = now_epoch if now_epoch is not None else time.time()
    root = digest.get("root") or {}
    root_id = str(root.get("id") or "")
    recent = list(root.get("recent_turns") or [])
    attention = list(digest.get("attention") or [])
    attention.extend(check_unrelayed_k43_replies(recent, now))
    by_root = dict(turns_by_root or {})
    if root_id and root_id not in by_root:
        by_root[root_id] = recent
    locks = dict(locks_by_root or {})
    if root_id and root_id not in locks:
        locks[root_id] = digest.get("fable_lock") or {}
    l28, alerted = check_dead_letter_maestro_turns(
        by_root,
        locks,
        now,
        alerted_state=state.get("l28_alerted"),
    )
    attention.extend(l28)
    if alerted:
        state["l28_alerted"] = alerted
    digest["attention"] = attention


__all__ = [
    "build_l28_scan_context",
    "check_dead_letter_maestro_turns",
    "check_unrelayed_k43_replies",
    "extend_digest_attention_l24_l28",
    "l28_scan_root_ids",
]
