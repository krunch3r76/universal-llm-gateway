"""Standing-handoff sidecar freshness — shared by cadence and the hop verb.

Classifies the per-lane ``cortex://notes/system/threads/{id}-standing-handoff.md``
file from filesystem mtime. Observed only: missing/stale/current. Cadence and
``agent_bus.hop`` both call this so a verb-fired hop sees the same freshness
token the successor will read.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_AGE_THRESHOLD_S = 1800.0
_STANDING_HANDOFF_STALE_FACTOR = 2.0
_MCP_FILES_ROOT = Path("/mnt/torus/mcp-data/files")


def cse_age_threshold_s() -> float:
    """Seconds of CSE/watch age before a continuity hop is due.

    Override with env ``CURSOR_AUTO_HOP_CSE_AGE_S`` (minimum 60s).
    Default 1800s (30 min). Same knob cadence uses for fire decisions.
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_CSE_AGE_S", "").strip()
    if not raw:
        return _DEFAULT_AGE_THRESHOLD_S
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _DEFAULT_AGE_THRESHOLD_S


def standing_handoff_path(thread_id: str) -> Path:
    """On-disk path for the standing-handoff cortex note of a private lane."""
    return (
        _MCP_FILES_ROOT
        / "notes"
        / "system"
        / "threads"
        / f"{thread_id}-standing-handoff.md"
    )


def standing_handoff_uri(thread_id: str) -> str:
    """Share URI the successor must read before trusting wake prose."""
    return f"cortex://notes/system/threads/{thread_id}-standing-handoff.md"


@dataclass(frozen=True)
class StandingHandoffFreshness:
    """Observed freshness of the standing handoff sidecar for one lane."""

    status: str  # current | stale | missing
    uri: str
    mtime_epoch: float | None
    age_s: float | None


def assess_standing_handoff(
    thread_id: str, *, now: float | None = None, stale_after_s: float | None = None
) -> StandingHandoffFreshness:
    """Classify the standing-handoff sidecar as current, stale, or missing.

    Observed from filesystem mtime only — never inferred from bus prose.
    Default stale window is twice the CSE hop-age threshold.
    """
    uri = standing_handoff_uri(thread_id)
    path = standing_handoff_path(thread_id)
    ts = time.time() if now is None else now
    limit = (
        stale_after_s
        if stale_after_s is not None
        else cse_age_threshold_s() * _STANDING_HANDOFF_STALE_FACTOR
    )
    if not path.is_file():
        return StandingHandoffFreshness("missing", uri, None, None)
    mtime = path.stat().st_mtime
    age = max(0.0, ts - mtime)
    status = "stale" if age > limit else "current"
    return StandingHandoffFreshness(status, uri, mtime, age)
