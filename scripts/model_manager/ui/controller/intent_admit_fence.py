"""Pending-restart admit fence for the lane an intent is still waiting on.

Callers are the cursor-sdk admit route (git_integration_worker) and the MCP
drain middleware. A ``pending_drain`` intent queues the next admit on that
lane so the idle boundary cannot move forward. ``force_requested`` and every
other status leave the existing force and drain rules alone. A store read
that fails returns no status — the admit starts — so a missing or locked
intent db cannot wedge the lane.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Collection
from typing import Any

from .restart_intent_states import STATUS_PENDING_DRAIN

MCP_ADMIT_LANE = "mcp"


def blocked_admit_lanes(
    *,
    service: str,
    intent_status: str | None,
    occupied_lanes: Collection[str] = (),
) -> frozenset[str]:
    """Lanes whose next admit must queue while ``intent_status`` is pending.

    MCP has one admit lane. GIW blocks only the occupied cursor lanes the
    drain is waiting on, so an admit on a different lane still starts.
    Any other status, including ``force_requested``, blocks nothing.
    """
    if intent_status != STATUS_PENDING_DRAIN:
        return frozenset()
    if service == "mcp":
        return frozenset({MCP_ADMIT_LANE})
    if service == "git_integration_worker":
        return frozenset(lane for lane in occupied_lanes if str(lane).strip())
    return frozenset()


def pending_intent_queues_admit(
    *,
    blocked_lanes: Collection[str],
    admit_lane: str,
) -> bool:
    """True when this admit is on a lane a pending intent is still waiting for.

    An empty ``admit_lane`` does not queue. The caller still has to skip
    read-only rows: the durable promoter only rehomes write-lease queues.
    """
    lane = (admit_lane or "").strip()
    if not lane:
        return False
    return lane in set(blocked_lanes)


def queue_kw_for_pending_intent(
    *,
    service: str,
    intent_status: str | None,
    occupied_lanes: Collection[str],
    admit_lane: str,
    armed: bool = False,
    read_only: bool = False,
) -> dict[str, bool]:
    """Ledger kwargs that queue a write admit, or ``{}`` when it should start.

    ``armed`` is the in-process GIW signal and counts as ``pending_drain``
    even when the store read missed. Read-only admits are not queued: the
    write-lease promoter would never rehome them. ``force`` is not an input;
    this fence does not add or remove a force bypass.
    """
    if read_only:
        return {}
    status = STATUS_PENDING_DRAIN if armed else intent_status
    blocked = blocked_admit_lanes(
        service=service,
        intent_status=status,
        occupied_lanes=occupied_lanes,
    )
    if pending_intent_queues_admit(blocked_lanes=blocked, admit_lane=admit_lane):
        return {"queue_for_pending_intent": True}
    return {}


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
    return str(status) if status else None
