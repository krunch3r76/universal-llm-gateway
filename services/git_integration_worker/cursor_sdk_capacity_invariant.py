"""Pure capacity invariant helpers for cursor-sdk gate stats (I1 + lane census)."""

from __future__ import annotations

import json
from typing import Any, Literal

Lane = Literal["A", "B"]


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
            raw = data.get("lane")
            if raw in ("A", "B"):
                lane = raw
    if lane is not None:
        return lane  # type: ignore[return-value]
    if lease_key and source_repo and lease_key != source_repo:
        return "B"
    return "A"


def active_by_lane_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count active write dispatches grouped by admit lane."""
    counts = {"A": 0, "B": 0}
    for row in rows:
        lane = resolve_admit_lane(
            record_json=row.get("record_json"),
            lease_key=row.get("lease_key"),
            source_repo=row.get("source_repo"),
        )
        counts[lane] += 1
    return counts
