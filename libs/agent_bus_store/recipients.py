"""Recipient slug expansion for turn inbox queries."""

from __future__ import annotations

from agent_seat.registry import expand_recipient_slugs


def recipient_in_clause(seat: str, *, include_team: bool) -> tuple[str, list[str]]:
    """Build ``to_agent IN (...)`` SQL fragment and bind values for a seat."""
    recipients = expand_recipient_slugs(seat)
    extras = ("all", "team") if include_team else ("all",)
    placeholders = ",".join("?" * (len(recipients) + len(extras)))
    clause = f"(to_agent IN ({placeholders}))"
    return clause, [*recipients, *extras]


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


def same_seat_memo_note_spare_clause(seat: str) -> tuple[str, list[str]]:
    """SQL predicate: same-seat MEMO/NOTE that send ``mark_read`` must spare.

    Hop CHECKPOINTs with ``mark_read=true`` clear inbox via
    ``mark_sender_unread_in_thread``. Same-from MEMO/NOTE (from and to both
    resolve to the marking seat) are unpaid successor work for
    ``mission.house_unread`` — stamping ``read_at`` hides them even though
    resume-fence says ``from=self`` does not skip NOTE/MEMO (a:38363,
    specimen agent-bus:15441#19).
    """
    recipients = expand_recipient_slugs(seat)
    placeholders = ",".join("?" * len(recipients))
    # Token match without REGEXP: pad subject so INSTR sees word edges.
    padded = "(' ' || UPPER(TRIM(subject)) || ' ')"
    subject_memo_note = (
        f"(INSTR({padded}, ' MEMO ') > 0 OR INSTR({padded}, ' NOTE ') > 0)"
    )
    clause = (
        f"(from_agent IN ({placeholders}) AND to_agent IN ({placeholders}) "
        f"AND {subject_memo_note})"
    )
    return clause, [*recipients, *recipients]
