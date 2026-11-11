"""Pending-restart admit fence for the git_integration_worker cursor-sdk route.

A ``pending_drain`` intent queues every new top-level write admit, on any
lane and for any work key, so the live set only shrinks and the restart
can fire. Expired arms never queue. Queued rows promote after restart (or on
cancel). A store read that fails returns no status — the admit starts — so a
missing or locked intent db cannot wedge admission.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .restart_intent_expiry import intent_expired
from .restart_intent_states import STATUS_PENDING_DRAIN


def queue_kw_for_pending_intent(
    *,
    intent_status: str | None,
    armed: bool = False,
    read_only: bool = False,
    nest_under: str | None = None,
) -> dict[str, bool]:
    """Ledger kwargs that queue a write admit, or ``{}`` when it should start.

    ``armed`` is the in-process GIW signal and counts as ``pending_drain``
    even when the store read missed. Read-only admits are not queued: the
    write-lease promoter would never rehome them. Nested children start so
    a live parent waiting on its child can finish and let the drain land.
    ``force`` is not an input; this fence adds no force bypass.
    """
    if read_only or str(nest_under or "").strip():
        return {}
    status = STATUS_PENDING_DRAIN if armed else intent_status
    if status != STATUS_PENDING_DRAIN:
        return {}
    return {"queue_for_pending_intent": True}


def pending_service_intent_status(
    service: str,
    *,
    loader: Callable[[str], Any] | None = None,
) -> str | None:
    """Status of the live intent for ``service``, or None when it cannot be read.

    Under pytest the default loader is skipped so a host pending row cannot
    change unrelated tests. Pass ``loader`` to exercise the read. Exceptions
    and a missing row return None (fail open).
    """
    if loader is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        if loader is None:
            from .restart_intent_store import RestartIntentStore

            intent = RestartIntentStore.instance().active_for_service(service)
        else:
            intent = loader(service)
    except Exception:
        return None
    if intent is None:
        return None
    status = getattr(intent, "status", None)
    if status == STATUS_PENDING_DRAIN and intent_expired(intent):
        return None
    return str(status) if status else None


def release_expired_arm(
    controller: Any,
    service: str = "git_integration_worker",
    *,
    loader: Callable[[str], Any] | None = None,
) -> bool:
    """Release the in-process GIW arm when the store row is expired but still armed."""
    from universal_logging import get_logger

    log = get_logger(__name__)
    try:
        if loader is None and os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        if loader is None:
            from .restart_intent_store import RestartIntentStore

            intent = RestartIntentStore.instance().active_for_service(service)
        else:
            intent = loader(service)
    except Exception:
        log.warning(
            "release_expired_arm: intent load failed service=%s",
            service,
            exc_info=True,
        )
        return False
    if intent is None:
        return False
    if getattr(intent, "status", None) != STATUS_PENDING_DRAIN:
        return False
    if not intent_expired(intent):
        return False
    if intent.drain_epoch is None:
        return False
    try:
        state = controller.drain_state()
    except Exception:
        log.warning(
            "release_expired_arm: drain_state failed service=%s",
            service,
            exc_info=True,
        )
        return False
    if not (state.get("draining") or state.get("armed")):
        return False
    if str(state.get("intent_id")) != intent.intent_id:
        return False
    if int(state.get("drain_epoch") or 0) != int(intent.drain_epoch):
        return False
    try:
        controller.release_drain(
            intent_id=intent.intent_id, drain_epoch=int(intent.drain_epoch)
        )
        return True
    except Exception:
        log.warning(
            "release_expired_arm: release_drain failed intent_id=%s",
            intent.intent_id,
            exc_info=True,
        )
        return False
