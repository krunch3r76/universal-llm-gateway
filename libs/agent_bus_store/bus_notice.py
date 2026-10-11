"""Classify an unread bus turn as memo, notice, own, or not addressed.

First match wins. Notice subjects and pipeline senders are defined here so
resume pickup does not import ``bus_watch``.
"""

from __future__ import annotations

from typing import Literal

from continuity_tape.notice import is_notice_row

from .recipients import MEMO_NOTE_SUBJECT_RE, is_same_seat_memo_note

UnreadClass = Literal["memo", "notice", "own", "not_addressed"]

PIPELINE_SENDERS = ("conductor-hop", "dispatch", "closeout-memo", "cursor-sdk")
BUS_NOTICE_SUBJECT_PREFIXES = ("LANE CLOSEOUT", "LANE ABANDONED")
BUS_NOTICE_SUBJECT_MARKERS = ("generate admitted",)
_BROADCAST = frozenset({"all", "team"})


def _pipeline_sender(from_agent: str) -> bool:
    if from_agent in PIPELINE_SENDERS:
        return True
    return from_agent.startswith("cursor-sdk:")


def _notice_subject(subject: str) -> bool:
    folded = subject.casefold()
    if any(
        folded.startswith(prefix.casefold()) for prefix in BUS_NOTICE_SUBJECT_PREFIXES
    ):
        return True
    return any(marker.casefold() in folded for marker in BUS_NOTICE_SUBJECT_MARKERS)


def _same_seat_memo_note(
    *,
    from_agent: str,
    to_agent: str,
    subject: str,
    seat_slugs: set[str],
) -> bool:
    """Same-seat MEMO/NOTE via ``is_same_seat_memo_note``, else the same test on slugs."""
    for slug in seat_slugs:
        if is_same_seat_memo_note(
            seat=slug,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
        ):
            return True
    if from_agent not in seat_slugs or to_agent not in seat_slugs:
        return False
    return bool(MEMO_NOTE_SUBJECT_RE.search(subject or ""))


def classify_unread_turn(
    *,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    seat_slugs: set[str],
) -> UnreadClass:
    """Return the first matching class for one unread turn."""
    if to_agent not in seat_slugs | _BROADCAST:
        return "not_addressed"
    if _same_seat_memo_note(
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        seat_slugs=seat_slugs,
    ):
        return "memo"
    if from_agent in seat_slugs:
        return "own"
    if (
        from_agent == to_agent
        or _pipeline_sender(from_agent)
        or _notice_subject(subject)
        or is_notice_row("user", body)
    ):
        return "notice"
    return "memo"
