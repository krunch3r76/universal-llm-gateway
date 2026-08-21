"""Shared CSR obligation constants and session lookup helpers."""

from __future__ import annotations

import re
from typing import Any

WAKE_TTL_S = 300
STOP_ACK_TTL_S = 300
OBLIGATION_KIND_WAKE_OWED = "wake_owed"
OBLIGATION_KIND_STOP_ACK_OWED = "stop_ack_owed"
STATUS_OPEN = "open"
STATUS_DISCHARGED = "discharged"
STATUS_ALARMED = "alarmed"

DEFAULT_WAKE = "chat_delivery"
DEFAULT_FALLBACK = "bus_wake+pager"

PARKED_PREFIX = "TYPE: PARKED"
WAITING_PREFIX = "TYPE: WAITING"
FIELD_RE = re.compile(
    r"^(wake|fallback|cse_chat_url|cse_registration_id|waiting_on):\s*(.+)$"
)


def is_parked_body(body: str) -> bool:
    first = (body or "").strip().split("\n", 1)[0].strip()
    return first == PARKED_PREFIX


def is_waiting_body(body: str) -> bool:
    first = (body or "").strip().split("\n", 1)[0].strip()
    return first == WAITING_PREFIX


def is_parked_waiting_body(body: str) -> bool:
    """True for WAITING, or PARKED that still names ``waiting_on``."""
    if is_waiting_body(body):
        return True
    if not is_parked_body(body):
        return False
    return bool(parse_parked_fields(body).get("waiting_on"))


def parse_parked_fields(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (body or "").splitlines():
        m = FIELD_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def session_key(*, registration_id: str | None, thread: str) -> str:
    if registration_id:
        return f"reg:{registration_id}"
    return f"thread:{thread}"


def find_session_by_thread(
    sessions: dict[str, dict[str, Any]], thread: str
) -> tuple[str, dict[str, Any]] | None:
    for key, row in sessions.items():
        ids = row.get("ids") or {}
        if str(ids.get("lane_thread") or "") == str(thread):
            return key, row
    return None


def find_session_by_registration(
    sessions: dict[str, dict[str, Any]], registration_id: str
) -> tuple[str, dict[str, Any]] | None:
    for key, row in sessions.items():
        ids = row.get("ids") or {}
        if str(ids.get("registration_id") or "") == str(registration_id):
            return key, row
    return None


def is_registered_lane(
    sessions: dict[str, dict[str, Any]], *, thread: str, registration_id: str | None
) -> bool:
    if find_session_by_thread(sessions, thread):
        return True
    if registration_id and find_session_by_registration(sessions, registration_id):
        return True
    return False
