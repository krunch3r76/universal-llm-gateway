"""Reattach policy for dormant CSE seats on the followup path.

Waking a seat the fleet itself parked is not an opt-in favour: the caller asked
for a URL that exists, and dormancy is an implementation detail of how hosts are
budgeted. Borrowing an unrelated host or minting a new one stays opt-in, because
those are visible to whoever owns the other glass. A unique *active* bind for the
same URL is the same class as dormancy: resume that registration only.
"""

from __future__ import annotations

import contextlib

from claude_bundles import cdp_registry
from claude_bundles.cse_url import normalize_cse_url

from cdp_ask.followup_reattach import ReattachOutcome, _teardown_attempt
from cdp_ask.models import FollowupProjectAskRequest, FollowupProjectAskResponse

__all__ = [
    "park_relaunched_host",
    "reattach_chat_url",
    "reattach_reason",
    "restrict_registration_id",
    "skipped_reason_for_miss",
    "unique_bound_registration",
]


def reattach_chat_url(
    req: FollowupProjectAskRequest, err: FollowupProjectAskResponse
) -> str | None:
    """CSE URL to reattach: the request's, else the one the resolver reported."""
    requested = (req.chat_url or "").strip()
    if requested:
        return requested
    for candidate in err.candidates or []:
        url = (getattr(candidate, "chat_url", "") or "").strip()
        if url:
            return url
    return None


def unique_bound_registration(chat_url: str) -> str | None:
    """Return the one active registration whose durable ``chat_url`` uniquely matches, else None."""
    target = normalize_cse_url(chat_url)
    matches: list[str] = []
    for lane in cdp_registry.list_active():
        bound = cdp_registry.chat_url_for_registration(lane.registration_id)
        if bound and normalize_cse_url(bound) == target:
            matches.append(lane.registration_id)
    if len(matches) == 1:
        return matches[0]
    return None


def reattach_reason(req: FollowupProjectAskRequest, chat_url: str | None) -> str | None:
    """Why reattach may run — ``requested``, ``dormant_seat``, ``bound_seat``, or None."""
    if req.reattach:
        return "requested"
    if not chat_url:
        return None
    if cdp_registry.dormant_for_chat_url(chat_url) is not None:
        return "dormant_seat"
    if unique_bound_registration(chat_url) is not None:
        return "bound_seat"
    return None


def restrict_registration_id(reason: str | None, chat_url: str | None) -> str | None:
    """Limit auto-resume to the dormant or uniquely bound seat; never other glass."""
    if not chat_url or reason == "requested":
        return None
    if reason == "dormant_seat":
        seat = cdp_registry.dormant_for_chat_url(chat_url)
        return None if seat is None else seat.registration_id
    if reason == "bound_seat":
        return unique_bound_registration(chat_url)
    return None


def skipped_reason_for_miss(
    *,
    reason: str | None,
    chat_url: str | None,
    outcome_error: str | None = None,
) -> str | None:
    """Typed skip so a seat cannot treat the miss as session-gone."""
    if outcome_error == "dormant_relaunch_failed":
        return "dormant_relaunch_failed"
    if reason == "bound_seat":
        return "bound_host_unlistable"
    if reason == "dormant_seat":
        return outcome_error or "dormant_relaunch_failed"
    if chat_url and reason is None:
        return "no_bound_or_dormant_seat"
    return None


async def park_relaunched_host(outcome: ReattachOutcome) -> None:
    """Close the woken tab and park the seat as dormant again."""
    await _teardown_attempt(outcome.page, outcome.pw, close_page=True)
    with contextlib.suppress(Exception):
        cdp_registry.make_dormant(
            outcome.registration_id or "", reason="followup_complete"
        )
