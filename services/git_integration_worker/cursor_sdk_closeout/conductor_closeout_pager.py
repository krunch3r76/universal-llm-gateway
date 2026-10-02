"""Fi-page conductor silence — bus SCORE_RESURFACE is not a page."""

from __future__ import annotations

import logging

from pager_notify.client import notify_pager
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX, clip
from pager_notify.state import claim_closeout_page

from .degraded_reasons import CONDUCTOR_CONSULT_HANDOFF_MISSING

logger = logging.getLogger(__name__)


def should_page_conductor_silence(
    *,
    degraded_reason: str | None,
    nest_under: str | None,
    is_conductor: bool = False,
) -> bool:
    """True only when consult wait lacks the handoff the next admit needs.

    Liaison IDE is not operator-present. ``live_summoning_chat`` does not suppress.
    """
    _ = nest_under, is_conductor
    return degraded_reason == CONDUCTOR_CONSULT_HANDOFF_MISSING


async def page_conductor_silence(
    *,
    degraded_reason: str | None,
    nest_under: str | None,
    dispatch_id: str,
    thread_id: str,
    is_conductor: bool = False,
) -> bool:
    """Page when consult closeout lacks admit handoff. Fail-open."""
    if not should_page_conductor_silence(
        degraded_reason=degraded_reason,
        nest_under=nest_under,
        is_conductor=is_conductor,
    ):
        return False
    key = f"conductor-stop:{degraded_reason or nest_under or dispatch_id}"
    if not claim_closeout_page(thread_id, key):
        return False
    subject = clip(
        "Conductor consult handoff missing",
        SMS_SUBJECT_MAX,
    )
    body = clip(
        "The conductor is waiting on a consult and the handoff the next admit "
        "needs is absent. Put that handoff on the summoning thread.\n"
        f"dispatch {dispatch_id} thread {thread_id} "
        f"reason={CONDUCTOR_CONSULT_HANDOFF_MISSING}.",
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
