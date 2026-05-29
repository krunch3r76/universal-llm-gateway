"""Tracker tunables and the ISO-8601 timestamp helper.

Holds the default capacity and retention bounds for
:class:`~.tracker.PipelineExecutionTracker` plus the UTC timestamp helper used
to stamp ``started_at`` / ``completed_at`` on records. Kept separate so the
bounds (and the rationale comment for the 24h retention window) are not buried
inside the class module.
"""

from __future__ import annotations

from datetime import UTC, datetime

_DEFAULT_MAX_RECORDS = 256
# 24h — covers overnight dispatch ("fire at 11pm, collect at 9am") cleanly.
# Stargate restart invalidates the in-process tracker regardless, so TTL is
# not the reliability bound; weekend-scale retention requires persistent
# backing (phase 2+). Approved thread 617.
_DEFAULT_RETENTION_SECONDS = 86400.0


def _utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 Z form."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
