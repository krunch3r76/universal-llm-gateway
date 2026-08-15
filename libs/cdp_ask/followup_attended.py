"""Attended-resolver outcomes mapped into followup envelopes.

Identity-omitted followups ask the attended resolver who is seated. Three answers
matter here: a live host binds, a dormant seat is reported as reattachable with
its ``chat_url``, and anything else refuses. Waking the dormant seat is a side
effect owned by ``followup_reattach`` — this module stays pure.
"""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry

from cdp_ask.attended_operator import (
    AttendedResolveDormant,
    AttendedResolveOutcome,
    AttendedResolveRefused,
    AttendedResolveSuccess,
    resolve_attended_operator,
)
from cdp_ask.followup_envelope import (
    REGISTRY_SOURCE,
    FollowupCandidate,
    fail_followup,
)
from cdp_ask.followup_events import (
    cdp_ask_attended_refused,
    cdp_ask_attended_resolve,
)
from cdp_ask.followup_events import (
    emit as emit_followup_event,
)
from cdp_ask.models import FollowupCandidateInfo, FollowupProjectAskResponse

__all__ = ["resolve_attended_binding"]


def _refused_to_followup(
    refused: AttendedResolveRefused,
) -> FollowupProjectAskResponse:
    """Map attended resolver refusal to followup envelope."""
    extra: dict[str, Any] = {}
    if refused.candidates:
        extra["candidates"] = [
            FollowupCandidateInfo(
                registration_id=c["registration_id"],
                chat_url=c["chat_url"],
                holder="",
                purpose=c.get("purpose"),
                cdp_url=c.get("cdp_url"),
                source=REGISTRY_SOURCE,
                provenance=c.get("provenance"),
            )
            for c in refused.candidates
        ]
    return fail_followup(refused.code, **extra)


def _dormant_to_followup(
    outcome: AttendedResolveDormant,
) -> FollowupProjectAskResponse:
    """Report a dormant seat as a reattachable target, not as a missing one.

    The reattach path in ``followup`` consumes ``candidates[0].chat_url`` to
    reopen the seat, so the URL must travel with the error.
    """
    return fail_followup(
        "attended_dormant",
        detail="attended CSE is dormant; reattach by chat_url",
        candidates=[
            FollowupCandidateInfo(
                registration_id=outcome.registration_id,
                chat_url=outcome.chat_url,
                holder="",
                purpose=outcome.purpose,
                cdp_url=None,
                source=REGISTRY_SOURCE,
                provenance=outcome.provenance,
            )
        ],
    )


def _emit_attended_outcome(outcome: AttendedResolveOutcome) -> None:
    if isinstance(outcome, AttendedResolveSuccess | AttendedResolveDormant):
        cdp_url = getattr(outcome, "cdp_url", None)
        emit_followup_event(
            cdp_ask_attended_resolve(
                registration_id=outcome.registration_id,
                cdp_url=cdp_url,
                chat_url=outcome.chat_url,
                purpose=outcome.purpose,
                source=outcome.source,
            )
        )
        return
    emit_followup_event(
        cdp_ask_attended_refused(
            code=outcome.code,
            candidates_considered=outcome.candidates_considered or None,
            candidate_count=len(outcome.candidates) if outcome.candidates else None,
        )
    )


def resolve_attended_binding() -> (
    tuple[FollowupCandidate | None, FollowupProjectAskResponse | None, str | None]
):
    """Identity-omitted path: bind a live seat, report a dormant one, or refuse."""
    outcome = resolve_attended_operator()
    _emit_attended_outcome(outcome)
    if isinstance(outcome, AttendedResolveDormant):
        return None, _dormant_to_followup(outcome), None
    if isinstance(outcome, AttendedResolveRefused):
        return None, _refused_to_followup(outcome), None
    holder = "cdp-ask-satellite"
    for reg in cdp_registry.list_active():
        if reg.registration_id == outcome.registration_id:
            holder = reg.holder
            break
    candidate = FollowupCandidate(
        registration_id=outcome.registration_id,
        chat_url=outcome.chat_url,
        holder=holder,
        purpose=outcome.purpose,
        cdp_url=outcome.cdp_url,
        target_binding="resolver",
    )
    return candidate, None, "attended_resolver"
