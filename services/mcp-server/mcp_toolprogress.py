"""Sparse progress and heartbeat events for long-running MCP tools.

Emits ``mcp.toolprogress.*`` so observers can distinguish slow work from hangs.
Timers are daemon threads and are always cancelled on tool exit.
"""

from __future__ import annotations

import threading
from typing import Any

from mcp_events import monotonic_now, record

_DEFAULT_HEARTBEAT_S = 30.0


def _scheduled_heartbeat(tool_name: str, ctx: dict[str, Any]) -> None:
    record(
        "mcp.toolprogress.heartbeat",
        tool_name=tool_name,
        phase="inflight",
        **ctx,
    )


def toolprogress_begin(
    tool_name: str,
    *,
    heartbeat_s: float = _DEFAULT_HEARTBEAT_S,
    **ctx: Any,
) -> tuple[float, threading.Timer]:
    """Emit started + schedule a single mid-flight heartbeat."""
    t0 = monotonic_now()
    record("mcp.toolprogress.started", tool_name=tool_name, **ctx)
    ctx_copy = dict(ctx)
    timer = threading.Timer(
        heartbeat_s,
        _scheduled_heartbeat,
        args=(tool_name, ctx_copy),
    )
    timer.daemon = True
    timer.start()
    return t0, timer


def toolprogress_phase(tool_name: str, phase: str, **ctx: Any) -> None:
    record("mcp.toolprogress.phase", tool_name=tool_name, phase=phase, **ctx)


def toolprogress_end(
    t0: float,
    timer: threading.Timer | None,
    tool_name: str,
    *,
    error: str | None = None,
    **ctx: Any,
) -> None:
    """Cancel heartbeat timer and emit completed or failed."""
    if timer is not None:
        timer.cancel()
    duration_s = round(monotonic_now() - t0, 3)
    if error:
        record(
            "mcp.toolprogress.failed",
            tool_name=tool_name,
            duration_s=duration_s,
            error=error,
            **ctx,
        )
    else:
        record(
            "mcp.toolprogress.completed",
            tool_name=tool_name,
            duration_s=duration_s,
            **ctx,
        )
