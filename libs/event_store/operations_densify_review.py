"""Tripwire query operations for densify review telemetry."""

from __future__ import annotations

import json
from typing import Any

from .operation_parameters import _coerce_limit, _resolve_window_minutes_and_cutoff
from .store import EventStore

_ADMITTED_SIGNAL = "frontier.densify.review.admitted"
_OUTCOME_SIGNAL = "frontier.densify.review.outcome"


async def _densify_review_admitted(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    minutes, cutoff = await _resolve_window_minutes_and_cutoff(params, store)
    limit = _coerce_limit(params.get("limit", 200))
    rows = await store.query(
        "SELECT * FROM events WHERE signal = ? AND ts_unix_ms > ? "
        "ORDER BY seq DESC LIMIT ?",
        (_ADMITTED_SIGNAL, cutoff, limit),
    )
    total = len(rows)
    opt_out_count = 0
    blank_hold_count = 0
    for row in rows:
        payload_raw = row.get("payload")
        payload = (
            json.loads(payload_raw)
            if isinstance(payload_raw, str)
            else (payload_raw or {})
        )
        if payload.get("opt_out"):
            opt_out_count += 1
        if payload.get("hold_reason") == "blank_adequacy":
            blank_hold_count += 1
    opt_out_rate = (opt_out_count / total) if total else 0.0
    return {
        "signal": _ADMITTED_SIGNAL,
        "minutes": minutes,
        "count": total,
        "opt_out_count": opt_out_count,
        "blank_hold_count": blank_hold_count,
        "opt_out_rate": opt_out_rate,
        "tripwire_reading": (
            "high_opt_out_rate"
            if total >= 5 and opt_out_rate >= 0.5
            else "opt_out_rate_normal"
        ),
        "rows": rows,
    }


async def _densify_review_outcome(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    minutes, cutoff = await _resolve_window_minutes_and_cutoff(params, store)
    limit = _coerce_limit(params.get("limit", 200))
    rows = await store.query(
        "SELECT * FROM events WHERE signal = ? AND ts_unix_ms > ? "
        "ORDER BY seq DESC LIMIT ?",
        (_OUTCOME_SIGNAL, cutoff, limit),
    )
    total = len(rows)
    rubber_stamp_count = 0
    finding_delta_sum = 0
    for row in rows:
        payload_raw = row.get("payload")
        payload = (
            json.loads(payload_raw)
            if isinstance(payload_raw, str)
            else (payload_raw or {})
        )
        finding_delta = int(payload.get("finding_delta") or 0)
        finding_delta_sum += finding_delta
        if finding_delta == 0 and payload.get("reviewer_concur_only"):
            rubber_stamp_count += 1
    rubber_stamp_rate = (rubber_stamp_count / total) if total else 0.0
    avg_finding_delta = (finding_delta_sum / total) if total else 0.0
    return {
        "signal": _OUTCOME_SIGNAL,
        "minutes": minutes,
        "count": total,
        "rubber_stamp_count": rubber_stamp_count,
        "rubber_stamp_rate": rubber_stamp_rate,
        "avg_finding_delta": avg_finding_delta,
        "tripwire_reading": (
            "high_rubber_stamp_rate"
            if total >= 5 and rubber_stamp_rate >= 0.5
            else (
                "high_finding_delta"
                if total >= 3 and avg_finding_delta >= 1.0
                else "finding_delta_normal"
            )
        ),
        "rows": rows,
    }
