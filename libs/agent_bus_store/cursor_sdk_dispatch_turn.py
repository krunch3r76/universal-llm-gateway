"""Classify cursor-sdk bus turns for poll_hint / execution-tracker continuity.

PARKED and RESUMED turns are advisory substrate signals — they must not
terminalize the caller's ``execution_id`` (steer-restart AC-SR-12).
"""

from __future__ import annotations

import re
from typing import Any

_PARKED_OR_RESUMED = re.compile(r"\b(PARKED|RESUMED)\b", re.IGNORECASE)


def is_cursor_sdk_dispatch_subject(subject: str) -> bool:
    return bool(subject) and subject.startswith("cursor-sdk dispatch")


def is_cursor_sdk_dispatch_terminal_subject(subject: str) -> bool:
    """True when a cursor-sdk seat turn should complete poll_hint / link recovery."""
    if not subject:
        return False
    if subject.startswith("CLOSEOUT"):
        return True
    if not is_cursor_sdk_dispatch_subject(subject):
        return False
    if _PARKED_OR_RESUMED.search(subject):
        return False
    return True


def sdk_terminal_closeout_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the newest cursor-sdk terminal closeout turn, skipping PARKED/RESUMED."""
    for turn in turns:
        author = turn.get("from_agent") or turn.get("from")
        if author != "cursor-sdk":
            continue
        subject = str(turn.get("subject") or "")
        if is_cursor_sdk_dispatch_terminal_subject(subject):
            return turn
    return None
