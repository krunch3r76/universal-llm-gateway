"""Hop-cadence skip while Auto occupies the lane — and when it does not.

Wait-report enqueue stamps ``wait_report_job_id`` on the watch row. Cadence
must not age-hop that CSE while the job is still queued or claimed, while any
non-hop Auto job is in flight on the home lane, or while the lane tip is
``TYPE: WAITING`` / ``TYPE: PARKED`` with ``waiting_on``. An empty lane is
also a skip: age-hopping a finished or idle CSE is tab-keepalive
(``decision:cse-tab-decoupled-from-session``). Continuity hops themselves
are not occupancy — they are the fire path being gated, and they do not
license an age hop on an idle watch.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_bundles.cse_session_common import is_parked_waiting_body
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
    job_matches_watch_lane,
)
from services.git_integration_worker.cursor_auto.hop_cadence_standdown import (
    _fetch_thread_turns_sync,
)
from services.git_integration_worker.cursor_auto.queue import AutoJobQueue

logger = get_logger(__name__)

PARKED_WAITING_REASON = "parked_waiting"
AUTO_IN_FLIGHT_REASON = "auto_in_flight"
IDLE_NO_KEEPALIVE_REASON = "idle_no_keepalive"

FetchTurnsFn = Callable[[str], list[dict[str, Any]] | None]


def mark_watch_wait_report(
    thread_id: str,
    job_id: str,
    *,
    path: Path | None = None,
) -> None:
    """Record the waiting Auto job on the hop-cadence watch row."""
    from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
        load_watches,
        save_watches,
    )

    tid = (thread_id or "").strip()
    jid = (job_id or "").strip()
    if not tid or not jid:
        return
    watches = load_watches(path)
    row = dict(watches.get(tid) or {"thread_id": tid})
    row["thread_id"] = tid
    row["wait_report_job_id"] = jid
    watches[tid] = row
    save_watches(watches, path)


def wait_report_job_pending(row: dict[str, Any], queue: AutoJobQueue) -> bool:
    """True while the stamped wait-report job is still queued or claimed."""
    jid = str(row.get("wait_report_job_id") or "").strip()
    if not jid:
        return False
    job = queue.get(jid)
    return job is not None and job.status in ("queued", "claimed")


def _turn_number(turn: dict[str, Any]) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def _latest_body(turns: list[dict[str, Any]]) -> str:
    ordered = sorted(turns, key=_turn_number)
    for turn in reversed(ordered):
        if turn.get("status") == "superseded":
            continue
        return str(turn.get("body") or "")
    return ""


def lane_parked_waiting(
    thread_id: str,
    *,
    fetch_turns_fn: FetchTurnsFn | None = None,
) -> bool:
    """True when the lane tip is WAITING or PARKED-with-waiting_on.

    Transport failure fails open (False) so cadence does not wedge shut.
    """
    tid = (thread_id or "").strip()
    if not tid:
        return False
    fetch = fetch_turns_fn if fetch_turns_fn is not None else _fetch_thread_turns_sync
    turns = fetch(tid)
    if turns is None or not isinstance(turns, list):
        return False
    if any(not isinstance(turn, dict) for turn in turns):
        return False
    return is_parked_waiting_body(_latest_body(turns))


def auto_work_on_lane(
    thread_id: str,
    *,
    row: dict[str, Any],
    queue: AutoJobQueue,
) -> bool:
    """True when a non-hop Auto job is queued or claimed on this watch's lane.

    ``nested_sdk_finished`` residuals and continuity hops do not count.
    Transport is not consulted — occupancy is the in-process queue.
    """
    tid = (thread_id or "").strip()
    if not tid:
        return False
    for job in queue.list_open_jobs():
        if job.continuity_hop or job.nested_sdk_finished:
            continue
        if job_matches_watch_lane(job, tid, row=row):
            return True
    return False


def cadence_skip_reason(
    thread_id: str,
    *,
    row: dict[str, Any],
    queue: AutoJobQueue,
    fetch_turns_fn: FetchTurnsFn | None = None,
) -> str:
    """Return why age cadence must not fire. Always a reason.

    Occupancy and WAITING inhibit first. The remainder is an idle or
    finished lane — age-hopping that CSE would only keep a tab warm.
    Prefer queue occupancy over a bus-tip fetch: Auto in flight on the
    home lane is enough even when Opus never PARKed.
    """
    if wait_report_job_pending(row, queue):
        return PARKED_WAITING_REASON
    if auto_work_on_lane(thread_id, row=row, queue=queue):
        return AUTO_IN_FLIGHT_REASON
    if lane_parked_waiting(thread_id, fetch_turns_fn=fetch_turns_fn):
        return PARKED_WAITING_REASON
    return IDLE_NO_KEEPALIVE_REASON


__all__ = [
    "AUTO_IN_FLIGHT_REASON",
    "IDLE_NO_KEEPALIVE_REASON",
    "PARKED_WAITING_REASON",
    "auto_work_on_lane",
    "cadence_skip_reason",
    "lane_parked_waiting",
    "mark_watch_wait_report",
    "wait_report_job_pending",
]
