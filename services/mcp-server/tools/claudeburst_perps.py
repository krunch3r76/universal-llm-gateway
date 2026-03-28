"""MCP tools: claudeburst_perps — query and control the Lighter perps bot.

Thin HTTP relays to the perps REST API.
Transport: UDS when bot runs locally; TCP when CLAUDEBURST_PERPS_HOST is set (Jupiter).

Available operations:
  status    — Full runtime state (risk, positions, prices, mode)
  positions — Open perps positions with unrealized P&L
  risk      — Risk limits, exposure, halt state
  kill      — Activate kill switch
  pause     — Pause scan loop
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

_SOCK = "/tmp/universal-protocol/claudeburst-perps.sock"
_BASE = "http://claudeburst-perps"

# TCP transport (used when CLAUDEBURST_PERPS_HOST is set — bot running on remote host)
_TCP_HOST = os.environ.get("CLAUDEBURST_PERPS_HOST", "")
_TCP_PORT = int(os.environ.get("CLAUDEBURST_PERPS_PORT", "8891"))

_OPS = {
    "status": ("GET", "/status"),
    "positions": ("GET", "/positions"),
    "risk": ("GET", "/risk"),
    "health": ("GET", "/health"),
    "kill": ("POST", "/commands/kill-switch"),
    "pause": ("POST", "/commands/pause"),
    "resume": ("POST", "/commands/resume"),
}


def register_claudeburst_perps_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def claudeburst_perps(op: str) -> str:
        """Query or control the Lighter perps trading bot.

        Operations (query):
          status    — Full snapshot: risk, positions, prices, paper/live mode.
          positions — Open perps positions with side, entry, size, unrealized P&L.
          risk      — Risk state: exposure, halt status, starting equity.
          health    — Liveness check.

        Operations (control — mutating):
          kill      — Activate kill switch. Halts all perps trading.
          pause     — Pause the scan loop.
          resume    — Resume the scan loop.

        Prefer `status` for general awareness, `positions` for portfolio review.
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
                else "Start with: cd /mnt/torus/projects/claudeburst && python -m perps"
            )
            result = json.dumps({"error": "Perps bot is not running", "hint": hint})
        except Exception as exc:
            result = json.dumps({"error": str(exc)})

        record(
            "mcp.tool.claudeburst.perps",
            {"op": op, "elapsed_ms": monotonic_now() - t0},
        )
        return result
