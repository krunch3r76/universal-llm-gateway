"""Map Event Service rows to :class:`dispatch_monitor.core.protocols.Event`."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event


def _parse_ts_ms(row: Mapping[str, Any]) -> int:
    if row.get("ts_unix_ms") is not None:
        return int(row["ts_unix_ms"])
    stamp = row.get("timestamp")
    if isinstance(stamp, str) and stamp:
        normalized = stamp.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    return 0


def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("payload") or {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def event_from_row(row: Mapping[str, Any]) -> Event | None:
    """Convert one Event Service row dict into a core :class:`Event`."""
    signal = row.get("signal")
    if not isinstance(signal, str) or not signal:
        return None
    return Event(
        signal=signal,
        ts_unix_ms=_parse_ts_ms(row),
        payload=_payload(row),
        seq=row.get("seq"),
        source=row.get("source") if isinstance(row.get("source"), str) else None,
        subject=row.get("subject") if isinstance(row.get("subject"), str) else None,
        id=row.get("event_id") if isinstance(row.get("event_id"), str) else None,
    )
