"""Activity-aware bridge HTTP read deadlines (friction 23057 / agent-bus:10269).

The SDK default stream read timeout is 600s and only bytes on the bridge
response stream reset it. GIW's outer idle-since-successful-toolcall watchdog
runs on a separate clock. Re-arm ``stream_timeout`` from the last completed
tool call so a healthy-but-quiet leg cannot trip ``bridge_read_timeout`` while
heartbeats and tool-call progress still report life.
"""

from __future__ import annotations

import httpx

# Sit just above the outer idle budget — same margin as ``_sdk_client_read_timeout``.
BRIDGE_READ_IDLE_MARGIN_S = 60.0


def bridge_read_timeout_for_idle(*, idle_budget_s: float) -> httpx.Timeout:
    """Build an httpx timeout whose read phase tracks the outer idle budget."""
    read_s = idle_budget_s + BRIDGE_READ_IDLE_MARGIN_S
    return httpx.Timeout(connect=30.0, read=read_s, write=120.0, pool=60.0)


def touch_bridge_read_deadline(
    client: object,
    *,
    idle_budget_s: float,
    margin_s: float = BRIDGE_READ_IDLE_MARGIN_S,
) -> None:
    """Extend the bridge transport stream read deadline from last progress.

    Best-effort: a failure to introspect the client must never disturb dispatch.
    """
    read_s = idle_budget_s + margin_s
    transport = getattr(client, "_transport", None)
    if transport is None:
        return
    current = getattr(transport, "stream_timeout", None)
    try:
        if isinstance(current, httpx.Timeout):
            transport.stream_timeout = httpx.Timeout(
                connect=current.connect,
                read=read_s,
                write=current.write,
                pool=current.pool,
            )
        elif current is None:
            transport.stream_timeout = httpx.Timeout(read_s)
        else:
            transport.stream_timeout = read_s
    except Exception:  # noqa: BLE001 — advisory refresh only
        return


def read_deadline_from_progress(
    *,
    last_progress_at: float,
    idle_budget_s: float,
    now: float,
    margin_s: float = BRIDGE_READ_IDLE_MARGIN_S,
) -> float:
    """Monotonic deadline for read idle: last progress + budget + margin."""
    _ = now
    return last_progress_at + idle_budget_s + margin_s
