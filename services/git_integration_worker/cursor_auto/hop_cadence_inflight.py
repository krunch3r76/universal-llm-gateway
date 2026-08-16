"""Lane-keyed in-flight commission probe for hop-cadence evaluate.

``evaluate_watch`` must not fire over work that is actually running, and must
not treat a queue row that still reads ``claimed`` after CLOSEOUT as running.
Callers inject this probe; the watch ledger is not the truth source.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.git_integration_worker.cursor_auto.queue import AutoJobQueue, get_queue

IN_FLIGHT_COMMISSION_REASON = "in_flight_commission"

LiveRunFn = Callable[[str], Any]


def lane_in_flight_commission(
    thread_id: str,
    *,
    queue: AutoJobQueue | None = None,
    live_run_fn: LiveRunFn | None = None,
) -> bool:
    """Return True when *thread_id* has a commission actually running.

    Truth source is the lane: a claimed Auto job that is not
    ``nested_sdk_finished`` (CLOSEOUT already on the lane while the ledger
    row still reads claimed does not inhibit), or a live SDK run on that
    thread. Watch-ledger ``pending_succession`` / raw ``status=claimed``
    are not consulted.

    *thread_id* is the aliased watch row's key, which for an operator
    mailbox (``cdp-operator-{lane}-*``) is the home lane, not necessarily
    the literal thread a commissioned job runs on (see
    ``hop_cadence_home_lane.watch_thread_for_job``). A conductor
    commissioned onto a different work thread than the operator's own
    standing thread is checked via :meth:`AutoJobQueue.claimed_for_home_lane`
    once the literal thread-id checks miss, so the probe still inhibits.
    """
    tid = (thread_id or "").strip()
    if not tid:
        return False
    probe = live_run_fn
    if probe is None:
        from services.git_integration_worker.cursor_sdk_supersede import (
            live_run_for_thread,
        )

        probe = live_run_for_thread
    if probe(tid) is not None:
        return True
    q = queue if queue is not None else get_queue()
    if q.claimed_for_thread(tid) is not None:
        return True
    return q.claimed_for_home_lane(tid) is not None
