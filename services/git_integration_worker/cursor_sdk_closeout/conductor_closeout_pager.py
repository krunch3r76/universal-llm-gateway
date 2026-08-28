"""Fi-page conductor silence — bus SCORE_RESURFACE is not a page."""

from __future__ import annotations

import logging

from pager_notify.client import notify_pager
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX, clip
from pager_notify.state import claim_closeout_page

from .degraded_reasons import CONDUCTOR_CONSULT_REASONS

logger = logging.getLogger(__name__)

_PARENT_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def _parent_already_terminal(nest_under: str) -> bool:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    row = CursorDispatchLedger.instance().dispatch_status_by_id(
        dispatch_id=nest_under
    )
    if not row:
        return False
    return str(row.get("status") or "") in _PARENT_TERMINAL


def should_page_conductor_silence(
    *,
    degraded_reason: str | None,
    nest_under: str | None,
    is_conductor: bool = False,
) -> bool:
    """True when a conductor hop stopped and the operator would not otherwise know.

    Liaison IDE is not operator-present. ``live_summoning_chat`` does not suppress.
    """
    if is_conductor:
        return True
    if degraded_reason in CONDUCTOR_CONSULT_REASONS:
        return True
    if nest_under and _parent_already_terminal(nest_under):
        return True
    return False


async def page_conductor_silence(
    *,
    degraded_reason: str | None,
    nest_under: str | None,
    dispatch_id: str,
    thread_id: str,
    is_conductor: bool = False,
) -> bool:
    """Page on consult-class conductor closeout or orphan nest. Fail-open."""
    if not should_page_conductor_silence(
        degraded_reason=degraded_reason,
        nest_under=nest_under,
        is_conductor=is_conductor,
    ):
        return False
    key = f"conductor-stop:{degraded_reason or nest_under or dispatch_id}"
    if not claim_closeout_page(thread_id, key):
        return False
    reason = degraded_reason or ("conductor-hop" if is_conductor else "orphan-nest")
    subject = clip(f"Conductor stopped — {reason}", SMS_SUBJECT_MAX)
    body = clip(
        "Vision: ULG is a house that remembers; silence with work in flight "
        "is hop-scheduler reconstruction on the phone.\n"
        f"Architecture: git_integration_worker closeout dispatch {dispatch_id} "
        f"thread {thread_id} reason={reason}. "
        "agent-bus SCORE_RESURFACE is not a page.\n"
        "Looking ahead: harvest the summoning thread; do not wait for a human "
        "to read the lane.",
        SMS_BODY_MAX,
    )
    try:
        return await notify_pager(subject, body, tag="conductor-stop")
    except Exception:  # noqa: BLE001 — closeout must not fail on pager
        logger.warning(
            "conductor-stop pager failed dispatch=%s thread=%s",
            dispatch_id,
            thread_id,
            exc_info=True,
        )
        return False


__all__ = ["page_conductor_silence", "should_page_conductor_silence"]
