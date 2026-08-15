"""Reattach policy for dormant CSE seats on the followup path.

Waking a seat the fleet itself parked is not an opt-in favour: the caller asked
for a URL that exists, and dormancy is an implementation detail of how hosts are
budgeted. Borrowing an unrelated host or minting a new one stays opt-in, because
those are visible to whoever owns the other glass.
"""

from __future__ import annotations

import contextlib

from claude_bundles import cdp_registry

from cdp_ask.followup_reattach import ReattachOutcome, _teardown_attempt
from cdp_ask.models import FollowupProjectAskRequest, FollowupProjectAskResponse

__all__ = ["park_relaunched_host", "reattach_chat_url", "reattach_reason"]


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


def reattach_reason(
    req: FollowupProjectAskRequest, chat_url: str | None
) -> str | None:
    """Why reattach may run — ``requested``, ``dormant_seat``, or None to refuse."""
    if req.reattach:
        return "requested"
    if not chat_url:
        return None
    if cdp_registry.dormant_for_chat_url(chat_url) is not None:
        return "dormant_seat"
    return None


async def park_relaunched_host(outcome: ReattachOutcome) -> None:
    """Close the woken tab and park the seat as dormant again."""
    await _teardown_attempt(outcome.page, outcome.pw, close_page=True)
    with contextlib.suppress(Exception):
        cdp_registry.make_dormant(
            outcome.registration_id or "", reason="followup_complete"
        )
