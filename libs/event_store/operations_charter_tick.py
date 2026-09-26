"""Named query op for manage.charter.tick.* lifecycle verify parity."""

from __future__ import annotations

import json
import time
from typing import Any

from .operation_parameters import _coerce_limit, _resolve_window_minutes_and_cutoff
from .store import EventStore

_PREFIX = "manage.charter.tick."
_ADMITTED = f"{_PREFIX}admitted"
_CLOSED = f"{_PREFIX}closed"
_FAILED = f"{_PREFIX}window_failed"
_WAITING = f"{_PREFIX}waiting_open"
_AUDIT_SIGNALS = (_ADMITTED, _CLOSED, _FAILED, _WAITING)

# Prefix LIKE is case-insensitive, so it cannot use idx_signal_ts. Paired
# with ORDER BY seq DESC the planner scans events from the newest row, and
# the dispatch-board seed (5s) never sees this op return. These four
# signals are the rows the op classifies; equality is an index range.
_AUDIT_SQL = (
    "SELECT * FROM events WHERE signal = ? AND ts_unix_ms > ? "
    "ORDER BY ts_unix_ms DESC LIMIT ?"
)


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


async def manage_charter_tick_audit(
    params: dict[str, Any], store: EventStore
) -> dict[str, Any]:
    """Return admitted/closed/failed rows plus waiting_open aging for charter ticks."""
    minutes, cutoff = await _resolve_window_minutes_and_cutoff(params, store)
    limit = _coerce_limit(params.get("limit", 200))
    rows: list[dict[str, Any]] = []
    for signal in _AUDIT_SIGNALS:
        rows.extend(
            await store.query(
                _AUDIT_SQL,
                (signal, cutoff, limit),
                limit=limit,
            )
        )
    rows.sort(key=lambda row: int(row.get("seq") or 0), reverse=True)
    rows = rows[:limit]
    admitted: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    waiting_open: list[dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    for row in rows:
        row_dict = dict(row)
        signal = str(row_dict.get("signal") or "")
        if signal == _ADMITTED:
            admitted.append(row_dict)
        elif signal == _CLOSED:
            closed.append(row_dict)
        elif signal == _FAILED:
            failed.append(row_dict)
        elif signal == _WAITING:
            payload = _payload(row_dict)
            age_s = payload.get("age_s")
            if age_s is None:
                ts = row_dict.get("ts_unix_ms")
                try:
                    age_s = max(0, (now_ms - int(ts)) // 1000) if ts else None
                except (TypeError, ValueError):
                    age_s = None
            waiting_open.append({**row_dict, "aging_s": age_s})
    return {
        "minutes": minutes,
        "count": len(rows),
        "admitted": admitted,
        "closed": closed,
        "failed": failed,
        "waiting_open": waiting_open,
    }
