"""Demote false orphan turns when a late cursor-sdk terminal turn arrives.

Caller inventory (insert_turn choke — demotion fires on cursor-sdk terminal):

| Caller | Path | Demotion fires? |
|---|---|---|
| ``routes/turns.py::create_turn`` | POST /turns | Yes — via insert_turn |
| ``routes/threads.py`` send/continue | send path | Yes — via insert_turn |
| ``reconcile.py::_reap_orphan_link`` | posts orphan (from=dispatch) | No — from_agent ≠ cursor-sdk |
| Tests | direct insert_turn | Yes when cursor-sdk terminal |

Worker delivery (``git_integration_worker`` bus.reply) lands on agent-bus HTTP
→ create_turn or send → insert_turn. No separate worker-side demotion.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .db.connection import connect
from .db.threads import load_dispatch_links
from .db.turns import get_turns, update_turn, update_turn_status
from .events.lifecycle import emit_dispatch_orphan_demoted

logger = logging.getLogger("agent-bus.orphan_demote")

_EXEC_ID_RE = re.compile(r"execution_id=([^\s\)]+)")

_SUPERSEDED_NOTE = (
    "\n\n[superseded: late cursor-sdk terminal turn arrived; "
    "orphan demoted — not a terminal failure signal]"
)


def is_cursor_sdk_terminal_subject(subject: str) -> bool:
    """True for explicit CLOSEOUT or cursor-sdk dispatch terminal forms."""
    if not subject:
        return False
    return subject.startswith("CLOSEOUT") or subject.startswith("cursor-sdk dispatch")


def parse_orphan_execution_id(body: str) -> str | None:
    """Extract execution_id= from an orphan turn body when present."""
    if not body:
        return None
    match = _EXEC_ID_RE.search(body)
    return match.group(1) if match else None


def is_dispatch_orphan_turn(turn: dict[str, Any]) -> bool:
    """True for open dispatch-originated orphan turns on a thread."""
    if turn.get("from_agent") != "dispatch":
        return False
    subject = (turn.get("subject") or "").lower()
    if "orphaned" not in subject:
        return False
    return turn.get("status") != "superseded"


def _orphan_matches_thread(orphan: dict[str, Any], thread_id: str) -> bool:
    """Apply optional execution_id correlation against thread dispatch links."""
    orphan_exec = parse_orphan_execution_id(str(orphan.get("body") or ""))
    if not orphan_exec:
        return True
    with connect() as conn:
        links = load_dispatch_links(conn, thread_id)
    if not links:
        return True
    return any(link["execution_id"] == orphan_exec for link in links)


def _annotate_orphan_body(orphan_id: int) -> None:
    """Best-effort body note when the orphan has not been read."""
    try:
        update_turn(orphan_id, append=_SUPERSEDED_NOTE)
    except Exception:
        logger.debug(
            "orphan body annotate skipped: orphan_turn_id=%s",
            orphan_id,
            exc_info=True,
        )


def demote_prior_orphan_turns(*, thread_id: str, closeout_turn_id: int) -> list[int]:
    """Mark open dispatch orphan turns superseded by a late terminal closeout.

    Returns orphan turn ids newly demoted (empty when none matched).
    """
    turns = get_turns(thread=thread_id, include_superseded=True)
    demoted: list[int] = []

    for turn in turns:
        if not is_dispatch_orphan_turn(turn):
            continue
        if not _orphan_matches_thread(turn, thread_id):
            continue

        orphan_id = int(turn["id"])
        if not update_turn_status(
            orphan_id,
            status="superseded",
            supersedes_turn=closeout_turn_id,
        ):
            continue

        if turn.get("read_at") is None:
            _annotate_orphan_body(orphan_id)

        emit_dispatch_orphan_demoted(
            thread_id=thread_id,
            orphan_turn_id=orphan_id,
            closeout_turn_id=closeout_turn_id,
        )
        demoted.append(orphan_id)

    return demoted


def maybe_demote_orphans_after_insert(
    *,
    thread_id: str,
    from_agent: str,
    subject: str,
    turn_id: int,
) -> None:
    """Hook from insert_turn — never raises into the insert path."""
    if from_agent != "cursor-sdk":
        return
    if not is_cursor_sdk_terminal_subject(subject):
        return
    try:
        demote_prior_orphan_turns(thread_id=thread_id, closeout_turn_id=turn_id)
    except Exception:
        logger.warning(
            "orphan demotion failed: thread=%s closeout_turn_id=%s",
            thread_id,
            turn_id,
            exc_info=True,
        )
