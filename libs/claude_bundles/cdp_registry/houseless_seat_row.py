"""Meaning of ``tab_kept_reason == houseless_seat`` for STATUS_TAB rows.

Authority is ``active.json`` under ``ports_lock``. A parked houseless seat row
survives restarts (reap and sweep skip it) and leaves the registry only through
``detach``.
"""

from __future__ import annotations

from typing import Any

from .models import STATUS_TAB

HOUSELESS_SEAT = "houseless_seat"

__all__ = [
    "HOUSELESS_SEAT",
    "houseless_seat_exit_branch",
    "is_houseless_tab_registration",
    "is_parked_houseless_seat",
]


def is_parked_houseless_seat(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and row.get("status") == STATUS_TAB
        and row.get("tab_kept_reason") == HOUSELESS_SEAT
    )


def houseless_seat_exit_branch(
    row: Any, *, aborted: bool, ended_ok: bool
) -> str | None:
    from claude_bundles.what_is_running_view import is_houseless_purpose

    if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
        return None
    if is_parked_houseless_seat(row):
        return HOUSELESS_SEAT
    if aborted or not ended_ok:
        return None
    if not is_houseless_purpose(row.get("purpose")):
        return None
    if not str(row.get("chat_url") or "").strip():
        return None
    return HOUSELESS_SEAT


def is_houseless_tab_registration(reg: Any) -> bool:
    from claude_bundles.what_is_running_view import is_houseless_purpose

    host = str(getattr(reg, "host_registration_id", None) or "").strip()
    return bool(host) and is_houseless_purpose(getattr(reg, "purpose", None))
