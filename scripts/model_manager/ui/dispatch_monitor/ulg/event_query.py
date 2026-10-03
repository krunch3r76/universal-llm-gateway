"""Synchronous Event Service named-op client over the query UDS."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from scripts.model_manager.ui.dispatch_monitor.core import signals

_DEFAULT_QUERY_SOCK = "/tmp/universal-protocol/events-query.sock"


def query_sock() -> str:
    return os.environ.get("EVENTS_QUERY_SOCK", _DEFAULT_QUERY_SOCK)


def post_query(
    body: dict[str, Any], *, sock: str | None = None, timeout: float = 30.0
) -> dict[str, Any]:
    """Call the origin resource described by a legacy operation or sql body."""
    from event_store.query_client import query_member, query_sql

    url = f"unix://{sock or query_sock()}"
    if body.get("type") == "sql":
        return query_sql(
            str(body.get("sql") or ""),
            params=body.get("params") or None,
            limit=int(body.get("limit", 100)),
            url=url,
            timeout=timeout,
        )
    return query_member(
        str(body.get("name") or ""),
        body.get("params") or {},
        url=url,
        timeout=timeout,
    )


def _operation(
    name: str,
    params: dict[str, Any],
    *,
    sock: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    from event_store.query_client import query_member

    return query_member(
        name,
        params,
        url=f"unix://{sock or query_sock()}",
        timeout=timeout,
    )


def signal_events(
    signal: str,
    *,
    minutes: int = 60,
    limit: int = 500,
    sock: str | None = None,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Fetch recent persisted events matching ``signal`` (glob supported)."""
    result = _operation(
        "signal-events",
        {"signal": signal, "minutes": minutes, "limit": limit},
        sock=sock,
        timeout=timeout,
    )
    rows = result.get("rows")
    return list(rows) if isinstance(rows, list) else []


def charter_tick_audit(
    *,
    minutes: int = 60,
    limit: int = 200,
    sock: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run the ``manage.charter.tick.audit`` named op."""
    return _operation(
        "manage.charter.tick.audit",
        {"minutes": minutes, "limit": limit},
        sock=sock,
        timeout=timeout,
    )


_WORKER_TERMINAL_SIGNALS: tuple[str, ...] = (
    signals.SDK_WORKER_COMPLETED,
    signals.SDK_WORKER_FAILED,
    signals.SDK_WORKER_TIMEOUT,
    signals.SDK_WORKER_ORPHANED,
    signals.SDK_WORKER_CANCELLED,
)


def _payload_dict(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("payload") or {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def row_matches_dispatch(row: dict[str, Any], dispatch_id: str) -> bool:
    """True when ES row payload names ``dispatch_id`` via dispatch or execution id."""
    payload = _payload_dict(row)
    for key in ("dispatch_id", "execution_id"):
        value = payload.get(key)
        if value is not None and str(value) == dispatch_id:
            return True
    return False


def worker_terminals_for_dispatch(
    dispatch_id: str,
    *,
    minutes: int = 60,
    limit: int = 500,
    sock: str | None = None,
    signal_events_fn: Callable[..., list[dict[str, Any]]] = signal_events,
) -> list[dict[str, Any]]:
    """Fetch worker lifecycle terminals from ES for one dispatch id."""
    matches: list[dict[str, Any]] = []
    for signal in _WORKER_TERMINAL_SIGNALS:
        for row in signal_events_fn(signal, minutes=minutes, limit=limit, sock=sock):
            if row_matches_dispatch(row, dispatch_id):
                matches.append(row)
    return matches
