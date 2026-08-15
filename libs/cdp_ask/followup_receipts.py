"""Receipt ranking, delivery caps, and followup response shaping.

Receipt strength is a claim about what was proven, so it is capped by how the
target was obtained: a minted or unbound host proves automation-visible DOM state
only, never that a human can see the paste.
"""

from __future__ import annotations

from cdp_ask.followup_events import cdp_ask_followup_unbound_capped
from cdp_ask.followup_events import emit as emit_followup_event
from cdp_ask.models import (
    FollowupMinReceipt,
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
    FollowupReceipt,
    TargetBinding,
)

_RECEIPT_RANK: dict[str, int] = {"dom_paste": 1, "dom_committed": 2}
_DOM_RECEIPTS = frozenset({"dom_paste", "dom_committed"})

__all__ = [
    "apply_receipt_caps",
    "paste_response",
    "receipt_meets",
    "response_extra",
]


def _receipt_rank(receipt: FollowupReceipt | None) -> int:
    if receipt is None:
        return 0
    return _RECEIPT_RANK.get(receipt, 0)


def receipt_meets(
    receipt: FollowupReceipt | None, min_receipt: FollowupMinReceipt
) -> bool:
    """True when proven *receipt* satisfies the caller gate."""
    if min_receipt == "human_visible":
        return False
    return _receipt_rank(receipt) >= _RECEIPT_RANK.get(min_receipt, 0)


def _cap_receipt_for_lane(
    receipt: FollowupReceipt | None, *, lane_created: bool
) -> FollowupReceipt | None:
    """Cap receipt at DOM rungs when a satellite lane was minted (B2)."""
    if receipt is None:
        return None
    if lane_created and receipt not in _DOM_RECEIPTS:
        return "dom_committed" if _receipt_rank(receipt) >= 2 else "dom_paste"
    return receipt


def _cap_receipt_for_unbound(
    receipt: FollowupReceipt | None,
    *,
    target_binding: TargetBinding | None,
) -> FollowupReceipt | None:
    """Further cap unbound pastes to automation-visible DOM rungs only."""
    capped = _cap_receipt_for_lane(receipt, lane_created=False)
    if target_binding != "unbound" or capped is None:
        return capped
    if capped not in _DOM_RECEIPTS:
        return "dom_paste"
    return capped


def apply_receipt_caps(
    receipt: FollowupReceipt | None,
    *,
    lane_created: bool,
    target_binding: TargetBinding | None,
) -> FollowupReceipt | None:
    """Apply mint and unbound caps in order to a proven receipt."""
    capped = _cap_receipt_for_lane(receipt, lane_created=lane_created)
    return _cap_receipt_for_unbound(capped, target_binding=target_binding)


def response_extra(*, reattach_used: bool, lane_created: bool) -> dict[str, bool]:
    """Reattach provenance fields carried on every followup response."""
    return {"reattach_used": reattach_used, "lane_created": lane_created}


def paste_response(
    *,
    req: FollowupProjectAskRequest,
    target_registration_id: str,
    url: str | None,
    pasted_at: float | None,
    streaming: bool | None,
    receipt: FollowupReceipt | None,
    lane_created: bool,
    reattach_used: bool,
    target_binding: TargetBinding | None,
) -> FollowupProjectAskResponse:
    """Build followup response from proven receipt and caller gate."""
    binding: TargetBinding = target_binding or ("unbound" if lane_created else "explicit")
    receipt = apply_receipt_caps(
        receipt, lane_created=lane_created, target_binding=binding
    )
    if binding == "unbound" and receipt is not None:
        emit_followup_event(
            cdp_ask_followup_unbound_capped(
                registration_id=target_registration_id,
                receipt=receipt,
                target_binding="unbound",
            )
        )
    send_verified = receipt is not None
    ok = receipt_meets(receipt, req.min_receipt)
    extra = response_extra(reattach_used=reattach_used, lane_created=lane_created)
    return FollowupProjectAskResponse(
        ok=ok,
        url=url,
        registration_id=target_registration_id,
        execution_id=req.execution_id,
        pasted_at=pasted_at,
        send_verified=send_verified,
        receipt=receipt,
        streaming_at_paste=streaming,
        error=None if ok else "send_unverified",
        target_binding=binding,
        **extra,
    )
