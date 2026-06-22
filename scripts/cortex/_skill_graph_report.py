"""Structured drift metrics for ``ingest_skills.py --check --report``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def classify_drift_lines(drifted: list[str]) -> tuple[int, int]:
    """Return (stale_edges, missing_edges) counts from human drift lines."""
    stale = 0
    missing = 0
    for line in drifted:
        if "stale references edge" in line:
            stale += 1
        elif "missing references edge" in line:
            missing += 1
    return stale, missing


def build_drift_report(
    drifted: list[str],
    *,
    last_clean_ts: str | None = None,
) -> dict[str, Any]:
    """Build machine-readable drift metrics for ``--report`` consumers."""
    stale_edges, missing_edges = classify_drift_lines(drifted)
    clean = not drifted
    if clean:
        last_clean_ts = datetime.now(UTC).isoformat()
    return {
        "drift_count": len(drifted),
        "stale_edges": stale_edges,
        "missing_edges": missing_edges,
        "last_clean_ts": last_clean_ts,
        "clean": clean,
    }
