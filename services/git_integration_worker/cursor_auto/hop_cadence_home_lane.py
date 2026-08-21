"""Resolve hop-cadence watch keys from operator mailboxes.

Work-thread Auto jobs from ``cdp-operator-{lane}-*`` are commissions on the
standing private lane, not new CSE seats. Cadence must enroll and age that
lane — never mint a hop whose ``parent_thread`` is the work thread.
"""

from __future__ import annotations

import re
from typing import Any

from agent_seat.registry import normalize_bus_address

from services.git_integration_worker.cursor_auto.queue import AutoJob

_OPERATOR_LANE_RE = re.compile(r"^cdp[-_]operator[-_](\d+)(?:[-_]|$)", re.IGNORECASE)


def home_lane_from_mailbox(from_agent: str) -> str | None:
    """Return the private-lane id encoded in a ``cdp-operator-{id}-*`` mailbox.

    ``web-*`` and other unparseable addresses return ``None`` so callers keep
    today's ``job.thread_id`` first-seat path.
    """
    raw = (from_agent or "").strip()
    if not raw:
        return None
    match = _OPERATOR_LANE_RE.match(raw)
    return match.group(1) if match else None


def watch_thread_for_job(job: AutoJob) -> str:
    """Watch ledger key for an Auto enqueue: home lane, else ``job.thread_id``."""
    home = home_lane_from_mailbox(job.from_agent)
    if home:
        return home
    return str(job.thread_id)


def job_matches_watch_lane(
    job: AutoJob, thread_id: str, *, row: dict[str, Any] | None = None
) -> bool:
    """True when *job* is Auto work belonging to the watch keyed by *thread_id*.

    Match any of: watch-key alias, literal job thread, mailbox home lane,
    CSE registration/chat_url on the watch row, or the same normalized
    ``from_agent`` (so ``web-anthropic`` work on a child thread still binds
    the operator's hop watches). Empty addresses do not match.
    """
    tid = (thread_id or "").strip()
    if not tid:
        return False
    if watch_thread_for_job(job) == tid or str(job.thread_id) == tid:
        return True
    home = home_lane_from_mailbox(job.from_agent)
    if home == tid:
        return True
    watch = row or {}
    row_home = home_lane_from_mailbox(str(watch.get("from_agent") or ""))
    if home and row_home and home == row_home:
        return True
    job_reg = (job.cse_registration_id or "").strip()
    row_reg = str(watch.get("registration_id") or "").strip()
    if job_reg and row_reg and job_reg == row_reg:
        return True
    job_url = (job.cse_chat_url or "").strip()
    row_url = str(watch.get("chat_url") or "").strip()
    if job_url and row_url and job_url == row_url:
        return True
    job_from = normalize_bus_address(job.from_agent)
    row_from = normalize_bus_address(str(watch.get("from_agent") or ""))
    return bool(job_from and row_from and job_from == row_from)
