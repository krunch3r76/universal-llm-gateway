"""Release the navigator single-flight lease once its cdp execution is over.

``navigator_wake`` invariant 1a promises the lease is released on terminal
dispatch state **or** TTL expiry, whichever is first. Only the TTL half was
ever built: ``release_navigator_lease`` has exactly one caller — the
submit-failure branch of ``fire_navigator_wake`` — so a *successful* wake held
``navigator:<root>`` for the whole ``wake_timeout + grace`` window (6300s on
root 10479) no matter how quickly the navigator actually finished. Two
consequences, both measured 2026-09-14:

* the navigator could fire at most once per 105 minutes, and
* ``navigator_grace_seconds`` (900) was dead code — a grace nested inside a
  6300s lease can never be the binding constraint.

A ticker recycle turned the ceiling into an outage. The lock records
``holder_pid`` and nothing ever reads it, so when the holder died the lease
became unreleasable by anyone and sat until TTL: ~33 minutes with the cheap
off-meter cdp tier dark while the metered cursor-sdk path kept spawning
(a:33724).

This module closes the loop the way ``spawn_pending.dead_sdk_holder`` already
does for the sdk seat lock — by asking whether the *execution* is over rather
than whether the clock ran out.

**Fail-closed.** Every uncertainty keeps the lease. A lease we cannot reason
about is left alone, because releasing one whose execution is still streaming
would put two navigators on the same house; the TTL remains the backstop. Only
a positive sighting of the execution's own reply releases anything.

Deliberately *not* used as the discriminator: holder-process liveness. The cdp
execution runs on another substrate and routinely outlives the process that
fired it, so "holder died" says nothing about whether the work is done.
"""

from __future__ import annotations

from typing import Any

# Module-style imports: these are patch targets for the tests below and
# ``from x import f`` would freeze the binding at import time (a:33608).
from bus_watch import digest_budget, navigator_wake

#: Turns scanned on the root when looking for the execution's reply.
REPLY_SCAN_TURNS = 40

#: Shortest execution-id prefix accepted as a subject match. The bus renders
#: replies as ``cdp reply — 338316c6``; eight hex chars is specific enough to
#: not collide, and short enough to survive that truncation.
_MIN_ID_PREFIX = 8


def execution_reply_landed(
    root_id: str, execution_id: str, *, last: int = REPLY_SCAN_TURNS
) -> bool | None:
    """Has ``execution_id`` reported back on ``root_id``?

    ``True`` when a turn subject names the execution, ``False`` when the scan
    completed and found none, ``None`` when the bus could not be read. The
    tri-state matters: ``None`` and ``False`` are both "do not release", but
    only ``False`` is an observation.
    """
    ident = str(execution_id or "").strip()
    if len(ident) < _MIN_ID_PREFIX:
        return None
    needle = ident[:_MIN_ID_PREFIX]
    try:
        with digest_budget._bus() as client:
            payload = digest_budget._get(client, "/turns", thread=root_id, last=last)
    except Exception:  # noqa: BLE001 — an unreadable bus must never reap a lease
        return None
    if not isinstance(payload, dict) or "_error" in payload:
        return None
    turns = payload.get("turns")
    if not isinstance(turns, list):
        return None
    for turn in turns:
        if isinstance(turn, dict) and needle in str(turn.get("subject") or ""):
            return True
    return False


def reap_navigator_lease(root_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Drop the lease when its execution has already replied.

    Returns ``reaped`` plus the ``reason`` a live lease was kept, so callers can
    show why the navigator is still gated instead of guessing at it — the
    failure that produced a wrong entry in the durable record on 2026-09-14.
    """
    lock = navigator_wake.read_navigator_lock(root_id)
    holder = lock.get("holder")
    if not holder:
        return {"reaped": False, "reason": "no_holder"}
    if navigator_wake._lease_expired(lock):
        return {"reaped": False, "reason": "ttl_already_expired"}
    execution_id = str(lock.get("execution_id") or "").strip()
    if not execution_id:
        # A lease claimed but never stamped with its execution id cannot be
        # judged; the TTL is the only safe release for it.
        return {"reaped": False, "reason": "no_execution_id", "holder": holder}
    landed = execution_reply_landed(root_id, execution_id)
    if landed is None:
        return {"reaped": False, "reason": "bus_unreadable", "holder": holder}
    if landed is False:
        return {"reaped": False, "reason": "execution_in_flight", "holder": holder}
    detail = {
        "reaped": True,
        "reason": "execution_replied",
        "holder": holder,
        "execution_id": execution_id,
        "claimed_at": lock.get("claimed_at"),
    }
    if dry_run:
        detail["reaped"] = False
        detail["would_reap"] = True
        return detail
    release = navigator_wake.release_navigator_lease(root_id)
    detail["released"] = bool(release.get("ok"))
    return detail


__all__ = ["REPLY_SCAN_TURNS", "execution_reply_landed", "reap_navigator_lease"]
