"""MCP tools: claudeburst — query and control the Polymarket trading bot.

Thin HTTP relays to the ClaudeBurst REST API.
Transport: UDS when bot runs locally; TCP when CLAUDEBURST_HOST is set (Jupiter).
Bot must be running for tools to work.

Available operations:
  status    — Full runtime state (risk, auto-trade, positions, prices)
  positions — Open positions with unrealized P&L
  markets   — Discovered active Polymarket intervals
  risk      — Risk limits, utilization, halt state, velocity
  pnl       — Session and daily P&L summary
  kill      — Activate kill switch (halts all trading permanently)
  pause     — Pause scan loop (existing positions unaffected)
  resume    — Resume scan loop
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

import httpx
from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SOCK = "/tmp/universal-protocol/claudeburst.sock"
_BASE = "http://claudeburst"

# TCP transport (used when CLAUDEBURST_HOST is set — bot running on remote host)
_TCP_HOST = os.environ.get("CLAUDEBURST_HOST", "")
_TCP_PORT = int(os.environ.get("CLAUDEBURST_PORT", "8890"))

_OPS = {
    "status": ("GET", "/status"),
    "positions": ("GET", "/positions"),
    "markets": ("GET", "/markets"),
    "risk": ("GET", "/risk"),
    "pnl": ("GET", "/pnl"),
    "health": ("GET", "/health"),
    "kill": ("POST", "/commands/kill-switch"),
    "pause": ("POST", "/commands/pause"),
    "resume": ("POST", "/commands/resume"),
}


def register_claudeburst_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def claudeburst(op: str) -> str:
        """Query or control the Polymarket trading bot.

        Operations (query — safe, read-only):
          status    — Full snapshot: risk state, auto-trade counters, positions,
                      feed prices, market bias, uptime. Start here for situational
                      awareness.
          positions — Open positions with entry price, current bid, unrealized P&L,
                      and total exposure. Use when reviewing portfolio.
          markets   — Active Polymarket 15-min intervals with token IDs and current
                      odds. Use to see what the bot is tracking.
          risk      — Risk limits and current utilization: exposure remaining,
                      velocity remaining, loss budget. Use before approving trades.
          pnl       — Session and daily P&L, trade count, volume. Use for
                      performance review.
          health    — Liveness check: halted/paused state and uptime.

        Operations (control — mutating, use with care):
          kill      — Activate kill switch. Permanently halts all trading.
                      Survives midnight reset. Use only in emergencies.
          pause     — Pause the scan loop. Existing positions are unaffected
                      but no new trades will be taken. Use for temporary holds.
          resume    — Resume the scan loop after a pause.

        Prefer `status` for general awareness, `risk` before trade approvals,
        `positions` for portfolio review.
        """
        t0 = monotonic_now()
        entry = _OPS.get(op)
        if entry is None:
            ops = ", ".join(sorted(_OPS))
            return json.dumps({"error": f"Unknown op '{op}'. Available: {ops}"})

        method, path = entry
        try:
            if _TCP_HOST:
                base_url = f"http://{_TCP_HOST}:{_TCP_PORT}"
                client_ctx = httpx.AsyncClient(base_url=base_url, timeout=10.0)
            else:
                transport = httpx.AsyncHTTPTransport(uds=_SOCK)
                client_ctx = httpx.AsyncClient(
                    transport=transport, base_url=_BASE, timeout=10.0
                )
            async with client_ctx as client:
                if method == "GET":
                    resp = await client.get(path)
                else:
                    resp = await client.post(path)
                resp.raise_for_status()
                result = resp.text
        except httpx.ConnectError:
            hint = (
                f"Check bot is reachable at {_TCP_HOST}:{_TCP_PORT}"
                if _TCP_HOST
                else "Start with: cd /mnt/torus/projects/claudeburst && python -m bot"
            )
            result = json.dumps({"error": "ClaudeBurst bot is not running", "hint": hint})
        except Exception as exc:
            result = json.dumps({"error": str(exc)})

        record(
            "mcp.tool.claudeburst",
            {"op": op, "elapsed_ms": monotonic_now() - t0},
        )
        return result
