"""Detect standing agent-bus listeners that stopped calling wait.

``mcp.agentbus.wait.called`` is recorded on every ``agent_bus.wait``. A
house listen loop calls it on a cadence of at most about 20 seconds, and
its backup routine about every 5 minutes. A connector drop (TLS EOF,
discovery timeout) never reaches that call, so the cadence stops.

A short burst (a harvest watcher) is not a standing listener: the calls
must span at least ``min_span_ms``. Silence is ``now - last_call`` at least
``silent_after_ms``, which is longer than one backup interval so a single
late backup does not page. Calls older than ``lookback_ms`` are ignored so
a listener that stopped yesterday does not page forever.
"""

from __future__ import annotations

from typing import Any

SILENT_AFTER_MS = 12 * 60 * 1000
MIN_SPAN_MS = 30 * 60 * 1000
MIN_CALLS = 6
LOOKBACK_MS = 2 * 60 * 60 * 1000


def silent_listeners(
    rows: list[dict[str, Any]],
    *,
    now_ms: int,
    silent_after_ms: int = SILENT_AFTER_MS,
    min_span_ms: int = MIN_SPAN_MS,
    min_calls: int = MIN_CALLS,
    lookback_ms: int = LOOKBACK_MS,
) -> list[dict[str, int | str]]:
    """Return standing listener rows whose last wait is too old.

    Each input row needs ``thread``, ``n`` (call count), ``first_ms``, and
    ``last_ms``. Rows missing a thread, shorter than ``min_span_ms``, or
    with fewer than ``min_calls`` are not standing listeners. The return
    rows are ``thread``, ``calls``, ``last_ms``, and ``age_ms``.
    """
    floor = now_ms - lookback_ms
    silent: list[dict[str, int | str]] = []
    for row in rows:
        thread = str(row.get("thread") or "").strip()
        if not thread or thread.lower() == "null":
            continue
        try:
            calls = int(row.get("n") or 0)
            first_ms = int(row.get("first_ms") or 0)
            last_ms = int(row.get("last_ms") or 0)
        except (TypeError, ValueError):
            continue
        if calls < min_calls or last_ms < floor:
            continue
        if last_ms - first_ms < min_span_ms:
            continue
        age_ms = now_ms - last_ms
        if age_ms < silent_after_ms:
            continue
        silent.append(
            {
                "thread": thread,
                "calls": calls,
                "last_ms": last_ms,
                "age_ms": age_ms,
            }
        )
    return silent


__all__ = [
    "LOOKBACK_MS",
    "MIN_CALLS",
    "MIN_SPAN_MS",
    "SILENT_AFTER_MS",
    "silent_listeners",
]
