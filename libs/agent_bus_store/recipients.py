"""Recipient slug expansion for turn inbox queries."""

from __future__ import annotations

import re

from agent_seat.registry import expand_recipient_slugs

# Shared with resume_fence_mission house_unread must-read (MEMO/NOTE tokens).
# Colon/bracket forms (``MEMO: …``, ``[MEMO] …``) must match here too — INSTR
# space-padding missed them (a:38363 review agent-bus:15453#2).
MEMO_NOTE_SUBJECT_RE = re.compile(r"(?i)\b(?:MEMO|NOTE)\b")


def recipient_in_clause(seat: str, *, include_team: bool) -> tuple[str, list[str]]:
    """Build ``to_agent IN (...)`` SQL fragment and bind values for a seat."""
    recipients = expand_recipient_slugs(seat)
    extras = ("all", "team") if include_team else ("all",)
    placeholders = ",".join("?" * (len(recipients) + len(extras)))
    clause = f"(to_agent IN ({placeholders}))"
    return clause, [*recipients, *extras]


def turn_mark_read_eligible(*, seat: str, to_agent: str) -> bool:
    """True when ``seat`` may mark ``to_agent`` read via get/fetch side effects."""
    if to_agent == "all":
        return False
    return to_agent in expand_recipient_slugs(seat)


def sender_auto_mark_clause(seat: str) -> tuple[str, list[str]]:
    """Alias-resolved ``to_agent`` match for send/through_turn auto-mark.

    Mirrors ``expand_recipient_slugs`` (same vocabulary as ``wait`` inbox
    matching) but excludes broadcast ``all`` — global read_at would clobber
    every other recipient's unread signal.
    """
    recipients = expand_recipient_slugs(seat)
    placeholders = ",".join("?" * len(recipients))
    clause = f"(to_agent IN ({placeholders}))"
    return clause, list(recipients)


def is_same_seat_memo_note(
    *,
    seat: str,
    from_agent: str,
    to_agent: str,
    subject: str | None,
) -> bool:
    """True when a turn is same-seat unpaid MEMO/NOTE for send ``mark_read``.

    Hop CHECKPOINTs with ``mark_read=true`` clear inbox via
    ``mark_sender_unread_in_thread``. Same-from MEMO/NOTE are successor work
    for ``mission.house_unread`` — stamping ``read_at`` hides them (a:38363,
    specimen agent-bus:15441#19).
    """
    recipients = set(expand_recipient_slugs(seat))
    if from_agent not in recipients or to_agent not in recipients:
        return False
    return bool(MEMO_NOTE_SUBJECT_RE.search(subject or ""))
