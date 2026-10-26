"""Durable idempotency for notice-class LANE / cursor-sdk CLOSEOUT turns (a:39664).

In-memory liaison state can reset; replays must not duplicate parent-root debrief
rows or worker terminal closeouts on the dispatch thread.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bus_watch.lane_closeout_parse import parse_closeout_turn
from fastapi.responses import JSONResponse

from .db.turns import get_turns
from .turns_models import TurnCreated

_SDK_CLOSEOUT_SUBJECT_RE = re.compile(
    r"^(?:CLOSEOUT\|)?cursor-sdk CLOSEOUT (?P<dispatch_id>\S+)",
    re.IGNORECASE,
)
_LANE_CLOSEOUT_SUBJECT_RE = re.compile(
    r"^LANE (?:CLOSEOUT|ABANDONED) agent-bus:(?P<lane_id>\d+)",
    re.IGNORECASE,
)


def _sdk_dispatch_id(subject: str) -> str | None:
    match = _SDK_CLOSEOUT_SUBJECT_RE.match(subject.strip())
    return None if match is None else match.group("dispatch_id")


def _lane_id_from_subject(subject: str) -> str | None:
    match = _LANE_CLOSEOUT_SUBJECT_RE.match(subject.strip())
    return None if match is None else match.group("lane_id")


def _lane_closeout_fingerprint(subject: str, body: str) -> tuple[str, str, str] | None:
    parsed = parse_closeout_turn({"subject": subject, "body": body})
    if parsed is None:
        lane = _lane_id_from_subject(subject)
        if lane is None:
            return None
        return (lane, "", "")
    lane = str(parsed.get("lane") or _lane_id_from_subject(subject) or "")
    if not lane:
        return None
    settled = str(parsed.get("settled") or "")
    landed = str(parsed.get("landed") or "")
    return (lane, settled, landed)


def _is_dedupe_candidate(*, from_agent: str, subject: str) -> bool:
    subj = subject or ""
    if _SDK_CLOSEOUT_SUBJECT_RE.match(subj):
        return from_agent == "cursor-sdk"
    if _LANE_CLOSEOUT_SUBJECT_RE.match(subj):
        return from_agent == "cursor"
    return False


def find_duplicate_notice_closeout(
    thread_id: str,
    *,
    from_agent: str,
    subject: str,
    body: str,
    scan_last: int = 250,
) -> dict[str, Any] | None:
    """Return an existing turn row when this send would duplicate a closeout notice."""
    if not _is_dedupe_candidate(from_agent=from_agent, subject=subject):
        return None
    sdk_id = _sdk_dispatch_id(subject)
    lane_fp = None if sdk_id else _lane_closeout_fingerprint(subject, body)
    if sdk_id is None and lane_fp is None:
        return None
    rows = get_turns(thread=thread_id, last=scan_last)
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        prior_subject = str(row.get("subject") or "")
        prior_from = str(row.get("from_agent") or row.get("from") or "")
        if prior_from != from_agent:
            continue
        if sdk_id is not None:
            if _sdk_dispatch_id(prior_subject) == sdk_id:
                return row
            continue
        prior_body = str(row.get("body") or "")
        prior_fp = _lane_closeout_fingerprint(prior_subject, prior_body)
        if prior_fp is None:
            continue
        if prior_fp == lane_fp:
            return row
    return None


def turn_created_from_row(row: dict[str, Any]) -> TurnCreated:
    """Build the replay TurnCreated stamp from a stored closeout row."""
    created_raw = str(row.get("created_at") or "")
    created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    return TurnCreated(
        id=int(row["id"]),
        thread=str(row["thread"]),
        turn_number=int(row["turn_number"]),
        created_at=created_at,
    )


def json_turn_replay(row: dict[str, Any]) -> JSONResponse:
    """Return the idempotent 200 body for a duplicate closeout notice."""
    content = turn_created_from_row(row).model_dump(mode="json")
    content["idempotent_replay"] = True
    return JSONResponse(status_code=200, content=content)


__all__ = [
    "find_duplicate_notice_closeout",
    "json_turn_replay",
    "turn_created_from_row",
]
