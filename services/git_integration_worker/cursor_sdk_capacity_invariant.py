"""Pure capacity invariant helpers for cursor-sdk gate stats (I1 + lane census)."""

from __future__ import annotations

import json
from typing import Any, Literal

Lane = Literal["A", "B", "unknown"]


def evaluate_i1(
    standard_limit: int,
    operator_limit: int,
    headroom: int,
) -> Literal["ok", "clamp"]:
    """Return ``clamp`` when configured lane capacity exceeds write headroom."""
    if standard_limit + operator_limit <= headroom:
        return "ok"
    return "clamp"


def resolve_admit_lane(
    *,
    record_json: str | None,
    lease_key: str | None = None,
    source_repo: str | None = None,
) -> Lane:
    """Classify a ledger row's admit lane from durable record_json or lease key."""
    lane: str | None = None
    if record_json:
        try:
            data = json.loads(record_json)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            if data.get("isolation_materialized") is False:
                return "A"
            raw = data.get("lane")
            if raw in ("A", "B"):
                lane = raw
    if lane == "B":
        if lease_key and source_repo and lease_key == source_repo:
            return "A"
        return "B"
    if lane == "A":
        return "A"
    if lease_key and source_repo and lease_key != source_repo:
        return "B"
    if not lease_key and not source_repo:
        return "unknown"
    return "A"


def resolve_isolation_materialized(
    *,
    record_json: str | None,
    lease_key: str | None = None,
    source_repo: str | None = None,
) -> bool:
    """Row-16 isolation gauge — stamped bool replayed for closeout/stats.

    When ``record_json`` carries ``isolation_materialized``, that stamped value
    wins. Lane-A rows are typically ``True`` (vacuous non-B pass from
    ``b_worktree_materialized``). Lane-B: ``True`` = distinct worktree path.
    ``False`` is historical only (nominal B without materialization, reclassified
    to lane A); new write admits refuse that case instead of stamping it.

    Unstamped fallback: ``False`` on shared master or missing keys; ``True`` only
    when ``lease_key`` is a distinct existing directory.
    """
    from services.git_integration_worker.cursor_sdk_concurrency_posture import (
        isolation_materialized_from_record_json,
    )

    stamped = isolation_materialized_from_record_json(record_json)
    if stamped is not None:
        return stamped
    if not lease_key or not source_repo:
        return False
    if lease_key == source_repo:
        return False
    from pathlib import Path

    return Path(lease_key).is_dir()


def active_by_lane_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count active write dispatches grouped by admit lane."""
    counts = {"A": 0, "B": 0, "unknown": 0}
    for row in rows:
        lane = resolve_admit_lane(
            record_json=row.get("record_json"),
            lease_key=row.get("lease_key"),
            source_repo=row.get("source_repo"),
        )
        counts[lane] += 1
    return counts
