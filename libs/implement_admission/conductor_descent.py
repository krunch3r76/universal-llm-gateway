"""Conductor lineage for the manage lifecycle refuse (friction a:37762).

A cursor-sdk dispatch descends from ``contract=conductor`` when its own row
or any parent reached through ``nest_under``, ``hop_from``, or ``resume_of``
has that contract. The walk is server-side ledger data. A missing row is not
descent: the caller must already hold a dispatch id the ledger recorded.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

STATE_CHANGING_MANAGE_ACTIONS = frozenset(
    {"sync_restart", "restart", "stop", "start", "rebuild"}
)
CONDUCTOR_DESCENDED_MANAGE_REASON = "conductor_descended_manage_refused"
_MAX_WALK = 32

LineageLookup = Callable[[str], "LineageView | None"]


@dataclass(frozen=True, slots=True)
class LineageView:
    """One ledger row's contract and parent links."""

    contract: str
    nest_under: str | None = None
    hop_from: str | None = None
    resume_of: str | None = None


def refusal_body() -> dict[str, str]:
    """Structured manage refusal. The operator seat restarts after land."""
    return {
        "error": (
            "State-changing manage is refused for a cursor-sdk dispatch that "
            "descends from contract=conductor "
            f"({CONDUCTOR_DESCENDED_MANAGE_REASON}). "
            "The operator seat performs fleet restarts after land."
        ),
        "reason": CONDUCTOR_DESCENDED_MANAGE_REASON,
    }


def descends_from_conductor(dispatch_id: str, lookup: LineageLookup) -> bool:
    """True when *dispatch_id* or an ancestor row is ``contract=conductor``.

    Parent links are ``nest_under``, ``hop_from``, and ``resume_of``. A lookup
    miss stops that branch (the id is not a recorded conductor ancestor).
    """
    start = (dispatch_id or "").strip()
    if not start:
        return False
    pending = [start]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if not current or current in seen:
            continue
        if len(seen) >= _MAX_WALK:
            return False
        seen.add(current)
        row = lookup(current)
        if row is None:
            continue
        if (row.contract or "").strip().lower() == "conductor":
            return True
        for parent in (row.nest_under, row.hop_from, row.resume_of):
            parent_id = (parent or "").strip()
            if parent_id and parent_id not in seen:
                pending.append(parent_id)
    return False


def ledger_lineage_lookup(dispatch_id: str) -> LineageView | None:
    """Read one descent row from the cursor-sdk dispatch ledger.

    Returns None when the database or the row is absent. Callers fail open
    on None so a seat with no ledger row is not refused by this gate.
    """
    from services.git_integration_worker.cursor_dispatch_ledger import (
        resolve_cursor_sdk_dispatch_ledger_path,
    )

    db_path = resolve_cursor_sdk_dispatch_ledger_path()
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT contract, nest_under, hop_from, resume_of "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return LineageView(
        contract=str(row["contract"] or ""),
        nest_under=row["nest_under"],
        hop_from=row["hop_from"],
        resume_of=row["resume_of"],
    )
