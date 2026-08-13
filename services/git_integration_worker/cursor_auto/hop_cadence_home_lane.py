"""Resolve hop-cadence watch keys from operator mailboxes.

Work-thread Auto jobs from ``cdp-operator-{lane}-*`` are commissions on the
standing private lane, not new CSE seats. Cadence must enroll and age that
lane — never mint a hop whose ``parent_thread`` is the work thread.
"""

from __future__ import annotations

import re

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
