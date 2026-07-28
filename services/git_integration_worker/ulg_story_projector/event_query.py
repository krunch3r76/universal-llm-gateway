"""Event Service query helpers for the ULG story projector."""

from __future__ import annotations

import json
import os
from typing import Any

from transport_utils import EVENTS_QUERY_SOCK, make_sync_client

from .allowlist import SIGNAL_ALLOWLIST

_EVENTS_QUERY_URL = f"unix://{EVENTS_QUERY_SOCK}"
_DEFAULT_BATCH_LIMIT = 500


def _post_query(body: dict[str, Any]) -> dict[str, Any]:
    try:
        with make_sync_client(_EVENTS_QUERY_URL, timeout=10.0) as client:
            resp = client.post("/v1/query", json=body)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        return {}
    return {}


def _raw_sql(sql: str, params: list[Any], *, limit: int) -> list[dict[str, Any]]:
    result = _post_query(
        {"type": "sql", "sql": sql, "params": params, "limit": limit},
    )
    rows = result.get("rows")
    return list(rows) if isinstance(rows, list) else []


def parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def query_events_since_seq(
    since_seq: int,
    *,
    limit: int = _DEFAULT_BATCH_LIMIT,
) -> list[dict[str, Any]]:
    """Return allowlisted events with ``seq > since_seq``, ascending."""
    placeholders = ",".join("?" for _ in SIGNAL_ALLOWLIST)
    sql = (
        "SELECT seq, signal, timestamp, ts_unix_ms, execution_id, payload "
        f"FROM events WHERE seq > ? AND signal IN ({placeholders}) "
        "ORDER BY seq ASC LIMIT ?"
    )
    params: list[Any] = [since_seq, *SIGNAL_ALLOWLIST, limit]
    rows = _raw_sql(sql, params, limit=limit)
    for row in rows:
        row["payload"] = parse_payload(row.get("payload"))
    return rows


def query_oldest_live_seq() -> int | None:
    rows = _raw_sql("SELECT MIN(seq) AS oldest_seq FROM events", [], limit=1)
    if not rows:
        return None
    value = rows[0].get("oldest_seq")
    return int(value) if value is not None else None


def query_event_by_seq(seq: int) -> dict[str, Any] | None:
    """Return a single event row by seq, or None if pruned/missing."""
    rows = _raw_sql(
        "SELECT seq, signal, timestamp, ts_unix_ms, execution_id, payload "
        "FROM events WHERE seq = ? LIMIT 1",
        [seq],
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    row["payload"] = parse_payload(row.get("payload"))
    return row


def query_event_timestamp(seq: int) -> int | None:
    rows = _raw_sql(
        "SELECT ts_unix_ms FROM events WHERE seq = ? LIMIT 1",
        [seq],
        limit=1,
    )
    if not rows:
        return None
    value = rows[0].get("ts_unix_ms")
    return int(value) if value is not None else None


def events_query_available() -> bool:
    sock = os.environ.get("EVENTS_QUERY_SOCK", EVENTS_QUERY_SOCK)
    return os.path.exists(sock)
